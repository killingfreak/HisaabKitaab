"""
routers/transactions.py
Implements the POS sale flow from the flowchart:

    Select Party -> Scan/Add Items -> (warn but allow if stock <= 0) ->
    Calculate Global Discount & GST -> Confirm Net Total ->
    if Partial/Zero payment: update party's udhaar ledger ->
    Deduct stock -> Generate Invoice Payload

Everything below happens inside ONE atomic DB transaction: either the
whole sale (transaction row + line items + stock deduction + ledger
update) commits together, or none of it does. Transactions are
IMMUTABLE once created — there is deliberately no PATCH/DELETE here;
corrections belong to a future void/credit-note phase.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Item, Party, PaymentStatus, Transaction, TransactionItem
from app.schemas import CurrentUser, TransactionCreate, TransactionRead

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _quantize(value: Decimal) -> Decimal:
    """Round to the column's 4 decimal places using banker's-rounding-free
    standard rounding, so we never persist more precision than the
    NUMERIC(12, 4) columns can hold."""
    return value.quantize(Decimal("0.0001"))


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    payload: TransactionCreate,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Transaction:
    # --- 1. Resolve & lock the party (if any) ------------------------------
    # `with_for_update()` takes a row lock for the duration of this DB
    # transaction, so two simultaneous sales against the same party's
    # ledger (or the same item's stock, below) can't race and silently
    # drop one of the updates — critical for a billing system.
    party: Party | None = None
    if payload.party_id is not None:
        result = await db.execute(
            select(Party)
            .where(
                Party.id == payload.party_id, Party.tenant_id == current_user.tenant_id
            )
            .with_for_update()
        )
        party = result.scalar_one_or_none()
        if party is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Party not found"
            )

    # --- 2. Resolve & lock every item referenced in the cart ---------------
    item_ids = {line.item_id for line in payload.items}
    result = await db.execute(
        select(Item)
        .where(Item.id.in_(item_ids), Item.tenant_id == current_user.tenant_id)
        .with_for_update()
    )
    items_by_id: dict[uuid.UUID, Item] = {
        item.id: item for item in result.scalars().all()
    }

    missing_ids = item_ids - items_by_id.keys()
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item(s) not found: {', '.join(str(i) for i in missing_ids)}",
        )

    # --- 3. Price out each line (stock check is informational only — the ---
    #        flowchart says WARN, not block, when stock is insufficient)
    txn_items: list[TransactionItem] = []
    subtotal = Decimal("0.0000")
    tax_amount = Decimal("0.0000")
    stock_warnings: list[str] = []

    for line in payload.items:
        item = items_by_id[line.item_id]
        if not item.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Item '{item.name}' is deactivated and cannot be sold.",
            )

        unit_price = line.unit_price if line.unit_price is not None else item.sale_price
        line_subtotal = _quantize(line.quantity * unit_price)
        line_tax = _quantize(line_subtotal * item.tax_rate / Decimal("100"))
        line_total = line_subtotal + line_tax

        if item.current_stock - line.quantity < 0:
            stock_warnings.append(
                f"'{item.name}': selling {line.quantity} against only {item.current_stock} in stock"
            )

        txn_items.append(
            TransactionItem(
                item_id=item.id,
                item_name=item.name,
                quantity=line.quantity,
                unit_price=unit_price,
                tax_rate=item.tax_rate,
                line_subtotal=line_subtotal,
                line_tax=line_tax,
                line_total=line_total,
            )
        )
        subtotal += line_subtotal
        tax_amount += line_tax

        # Deduct stock now (in-memory; flushed with the rest of the commit
        # below). Allowed to go negative — the flowchart explicitly permits
        # this ("Warn Cashier but Allow Sale"), it's just surfaced as a
        # warning in the response rather than blocking the sale.
        item.current_stock = item.current_stock - line.quantity

    # --- 4. Totals -----------------------------------------------------------
    if payload.discount_amount > subtotal + tax_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Discount cannot exceed the pre-discount total.",
        )
    net_amount = _quantize(subtotal + tax_amount - payload.discount_amount)

    if payload.paid_amount > net_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Paid amount ({payload.paid_amount}) cannot exceed net amount ({net_amount}). "
            "Overpayment/advance credit isn't supported yet.",
        )
    balance_due = _quantize(net_amount - payload.paid_amount)

    if balance_due == Decimal("0.0000"):
        payment_status = PaymentStatus.PAID
    elif payload.paid_amount == Decimal("0.0000"):
        payment_status = PaymentStatus.UNPAID
    else:
        payment_status = PaymentStatus.PARTIAL

    # A sale that isn't paid in full has to be attributed to a party so
    # the udhaar (credit) ledger has somewhere to record the debt.
    if balance_due > 0 and party is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A party must be selected to record a partial or unpaid sale on the ledger.",
        )

    # --- 5. Persist: transaction + lines + stock (already mutated above) ---
    #        + ledger update, all in this one DB transaction.
    transaction = Transaction(
        tenant_id=current_user.tenant_id,
        party_id=party.id if party else None,
        created_by_user_id=current_user.id,
        doc_type=payload.doc_type,
        subtotal=subtotal,
        discount_amount=payload.discount_amount,
        tax_amount=tax_amount,
        net_amount=net_amount,
        paid_amount=payload.paid_amount,
        balance_due=balance_due,
        payment_status=payment_status,
        items=txn_items,
    )
    db.add(transaction)

    # Per the flowchart: only Partial/Zero payment touches the ledger; a
    # fully-paid sale leaves outstanding_balance untouched.
    if balance_due > 0 and party is not None:
        party.outstanding_balance = party.outstanding_balance + balance_due

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not record the sale due to a data conflict. Please retry.",
        )

    # Re-fetch with items eager-loaded so the response model doesn't trigger
    # lazy I/O (which would fail — the session's implicit-await guard is off
    # for async sessions; see database.py's expire_on_commit=False comment).
    result = await db.execute(
        select(Transaction)
        .where(Transaction.id == transaction.id)
        .options(selectinload(Transaction.items))
    )
    transaction = result.scalar_one()

    if stock_warnings:
        # FastAPI has no first-class "warnings" slot on a 201 response, and
        # the flowchart's "warn but allow" should be a UI nudge, not a
        # reason to change the response shape. A header keeps TransactionRead
        # clean while still letting the POS frontend flag it to the cashier.
        response.headers["X-Stock-Warnings"] = " | ".join(stock_warnings)

    return transaction


@router.get("", response_model=list[TransactionRead])
async def list_transactions(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    party_id: uuid.UUID | None = Query(None, description="Filter to one party's sales"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[Transaction]:
    stmt = (
        select(Transaction)
        .where(Transaction.tenant_id == current_user.tenant_id)
        .options(selectinload(Transaction.items))
        .order_by(Transaction.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if party_id is not None:
        stmt = stmt.where(Transaction.party_id == party_id)

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{transaction_id}", response_model=TransactionRead)
async def get_transaction(
    transaction_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Transaction:
    result = await db.execute(
        select(Transaction)
        .where(
            Transaction.id == transaction_id,
            Transaction.tenant_id == current_user.tenant_id,
        )
        .options(selectinload(Transaction.items))
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found"
        )
    return transaction
