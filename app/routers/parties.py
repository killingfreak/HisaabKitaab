"""
routers/parties.py
CRUD endpoints for Parties (Customers/Suppliers).

TENANT ISOLATION RULE (applies to every endpoint in this file): every
query is filtered by `current_user.tenant_id`, taken from the verified
JWT — never from a path/query/body parameter. A party ID from another
tenant should behave exactly like a nonexistent ID (404), never a 403,
so we don't leak the existence of other tenants' data.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models import Party, PartyPayment, Transaction
from app.schemas import (
    CurrentUser,
    LedgerEntry,
    PartyCreate,
    PartyPaymentCreate,
    PartyPaymentRead,
    PartyRead,
    PartyStatementResponse,
    PartyUpdate,
)

router = APIRouter(prefix="/parties", tags=["parties"])


async def _get_party_or_404(
    party_id: uuid.UUID, tenant_id: uuid.UUID, db: AsyncSession
) -> Party:
    result = await db.execute(
        select(Party).where(Party.id == party_id, Party.tenant_id == tenant_id)
    )
    party = result.scalar_one_or_none()
    if party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Party not found"
        )
    return party


@router.post("", response_model=PartyRead, status_code=status.HTTP_201_CREATED)
async def create_party(
    payload: PartyCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Party:
    party = Party(tenant_id=current_user.tenant_id, **payload.model_dump())
    db.add(party)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A {payload.type.value.lower()} named '{payload.name}' already exists.",
        )
    await db.refresh(party)
    return party


@router.get("", response_model=list[PartyRead])
async def list_parties(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    active_only: bool = Query(True, description="Exclude soft-deactivated parties"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[Party]:
    stmt = select(Party).where(Party.tenant_id == current_user.tenant_id)
    if active_only:
        stmt = stmt.where(Party.is_active.is_(True))
    stmt = stmt.order_by(Party.name).offset(skip).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{party_id}", response_model=PartyRead)
async def get_party(
    party_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Party:
    return await _get_party_or_404(party_id, current_user.tenant_id, db)


@router.patch("/{party_id}", response_model=PartyRead)
async def update_party(
    party_id: uuid.UUID,
    payload: PartyUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Party:
    party = await _get_party_or_404(party_id, current_user.tenant_id, db)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(party, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A party with that name and type already exists.",
        )
    await db.refresh(party)
    return party


@router.delete("/{party_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_party(
    party_id: uuid.UUID,
    # Deleting a party (and its ledger history implications) is an
    # owner-level action — Cashiers can create/edit but not delete.
    current_user: CurrentUser = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
) -> None:
    party = await _get_party_or_404(party_id, current_user.tenant_id, db)

    # Soft delete: preserves ledger/transaction history integrity. A hard
    # DELETE would orphan any TRANSACTIONS rows already pointing at this
    # party_id (Phase 3).
    party.is_active = False
    await db.commit()


@router.post(
    "/{party_id}/payments",
    response_model=PartyPaymentRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_payment(
    party_id: uuid.UUID,
    payload: PartyPaymentCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PartyPayment:
    """
    Record money collected against an existing balance (a customer paying
    down their udhaar). This is the CREDIT counterpart to the DEBIT a
    Transaction leaves behind when it isn't paid in full.

    Row-locked (`with_for_update`) for the same reason transactions.py
    locks Party/Item — two simultaneous payments against the same party
    shouldn't be able to race and drop one of them.
    """
    result = await db.execute(
        select(Party)
        .where(Party.id == party_id, Party.tenant_id == current_user.tenant_id)
        .with_for_update()
    )
    party = result.scalar_one_or_none()
    if party is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Party not found"
        )

    if payload.amount > party.outstanding_balance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Payment ({payload.amount}) exceeds the party's outstanding balance "
                f"({party.outstanding_balance}). Advance/overpayment isn't supported yet."
            ),
        )

    party.outstanding_balance = party.outstanding_balance - payload.amount
    payment = PartyPayment(
        tenant_id=current_user.tenant_id,
        party_id=party.id,
        created_by_user_id=current_user.id,
        amount=payload.amount,
        note=payload.note,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


@router.get("/{party_id}/statement", response_model=PartyStatementResponse)
async def get_party_statement(
    party_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PartyStatementResponse:
    """
    Full ledger history for a party: every sale that left a balance_due
    (a debit) interleaved chronologically with every payment collected
    (a credit), each row showing the running balance at that point in time.

    This is reconstructed from Transaction + PartyPayment rows rather than
    just returning `party.outstanding_balance` directly — that single
    number IS the source of truth for "what's owed right now", but a
    statement needs the full history, and cross-checking that the
    reconstruction's final running_balance matches outstanding_balance is
    a useful sanity check that nothing has drifted out of sync.
    """
    party = await _get_party_or_404(party_id, current_user.tenant_id, db)

    txn_result = await db.execute(
        select(Transaction)
        .where(
            Transaction.party_id == party_id,
            Transaction.tenant_id == current_user.tenant_id,
            Transaction.balance_due > 0,
        )
        .order_by(Transaction.created_at)
    )
    sales = txn_result.scalars().all()

    pay_result = await db.execute(
        select(PartyPayment)
        .where(
            PartyPayment.party_id == party_id,
            PartyPayment.tenant_id == current_user.tenant_id,
        )
        .order_by(PartyPayment.created_at)
    )
    payments = pay_result.scalars().all()

    # Merge both event types into one chronological timeline. Sales are
    # positive (they add to what's owed); payments are negative (they
    # reduce it) — so a running sum down the sorted list IS the balance.
    raw_events: list[tuple[str, uuid.UUID, datetime, str, Decimal]] = [
        (
            "SALE",
            t.id,
            t.created_at,
            f"{t.doc_type.value.title()} — net {t.net_amount}, unpaid {t.balance_due}",
            t.balance_due,
        )
        for t in sales
    ] + [
        ("PAYMENT", p.id, p.created_at, p.note or "Payment received", -p.amount)
        for p in payments
    ]
    raw_events.sort(key=lambda e: e[2])

    running_balance = Decimal("0.0000")
    entries: list[LedgerEntry] = []
    for entry_type, reference_id, created_at, description, amount in raw_events:
        running_balance += amount
        entries.append(
            LedgerEntry(
                entry_type=entry_type,
                reference_id=reference_id,
                created_at=created_at,
                description=description,
                amount=amount,
                running_balance=running_balance,
            )
        )

    return PartyStatementResponse(
        party=party,
        entries=entries,
        current_outstanding_balance=party.outstanding_balance,
    )
