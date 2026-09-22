"""
routers/items.py
CRUD endpoints for Items (inventory/stock-keeping units).

Same tenant-isolation rule as routers/parties.py: every query filters by
`current_user.tenant_id` from the JWT, and a cross-tenant ID looks like
a 404, not a 403.

`current_stock` is deliberately NOT part of ItemUpdate — once an item
exists, its stock should only move via (a) explicit adjustment below,
or (b) transaction processing in Phase 3. This keeps stock changes
auditable instead of silently overwritable through a generic PATCH.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models import Item
from app.schemas import CurrentUser, ItemCreate, ItemRead, ItemUpdate

router = APIRouter(prefix="/items", tags=["items"])


class StockAdjustment(BaseModel):
    """
    Body for POST /items/{item_id}/adjust-stock.
    `delta` is signed: positive to add stock (e.g. new purchase / stock
    take-on), negative to remove it (e.g. damage, correction). Kept
    separate from ItemUpdate so stock changes always go through this
    single, auditable, quantity-validated path.
    """

    delta: Decimal = Field(
        ..., description="Signed quantity change, e.g. 10.5 or -2.25"
    )
    reason: str = Field(..., min_length=1, max_length=255)

    @field_validator("delta", mode="before")
    @classmethod
    def _coerce_delta(cls, v: Decimal | str | float) -> Decimal:
        return Decimal(str(v))


async def _get_item_or_404(
    item_id: uuid.UUID, tenant_id: uuid.UUID, db: AsyncSession
) -> Item:
    result = await db.execute(
        select(Item).where(Item.id == item_id, Item.tenant_id == tenant_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Item not found"
        )
    return item


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
async def create_item(
    payload: ItemCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Item:
    item = Item(tenant_id=current_user.tenant_id, **payload.model_dump())
    db.add(item)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An item named '{payload.name}' already exists.",
        )
    await db.refresh(item)
    return item


@router.get("", response_model=list[ItemRead])
async def list_items(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    search: str | None = Query(
        None, description="Case-insensitive match on name or SKU"
    ),
    active_only: bool = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[Item]:
    stmt = select(Item).where(Item.tenant_id == current_user.tenant_id)
    if active_only:
        stmt = stmt.where(Item.is_active.is_(True))
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where((Item.name.ilike(pattern)) | (Item.sku.ilike(pattern)))
    stmt = stmt.order_by(Item.name).offset(skip).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{item_id}", response_model=ItemRead)
async def get_item(
    item_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Item:
    return await _get_item_or_404(item_id, current_user.tenant_id, db)


@router.patch("/{item_id}", response_model=ItemRead)
async def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Item:
    item = await _get_item_or_404(item_id, current_user.tenant_id, db)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An item with that name already exists.",
        )
    await db.refresh(item)
    return item


@router.post("/{item_id}/adjust-stock", response_model=ItemRead)
async def adjust_stock(
    item_id: uuid.UUID,
    payload: StockAdjustment,
    # Manual stock corrections are an owner-level action; Cashiers move
    # stock only indirectly, through completed sales (Phase 3).
    current_user: CurrentUser = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
) -> Item:
    item = await _get_item_or_404(item_id, current_user.tenant_id, db)

    new_stock = item.current_stock + payload.delta
    if new_stock < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Adjustment would result in negative stock ({new_stock}).",
        )

    item.current_stock = new_stock
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_role("ADMIN")),
    db: AsyncSession = Depends(get_db),
) -> None:
    item = await _get_item_or_404(item_id, current_user.tenant_id, db)
    # Soft delete only — TransactionItems (Phase 3) will hold historical
    # references to this item_id and must not be orphaned.
    item.is_active = False
    await db.commit()
