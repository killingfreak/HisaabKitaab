"""
schemas.py
Pydantic v2 request/response models for authentication.

Kept separate from models.py deliberately: SQLAlchemy models describe
DB shape, Pydantic schemas describe wire shape. They will diverge (e.g.
schemas never expose hashed_password).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import DocType, PartyType, PaymentStatus, UserRole


class LoginRequest(BaseModel):
    """Body of POST /login."""

    email: EmailStr
    # 72-char cap matches bcrypt's hard 72-BYTE input limit (see auth.py) —
    # capped here so an over-length password fails validation with a clear
    # 422 instead of being silently truncated by bcrypt.
    password: str = Field(..., min_length=8, max_length=72)


class TokenResponse(BaseModel):
    """What /login returns on success."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Seconds until the access token expires")


class TokenPayload(BaseModel):
    """
    Decoded shape of our JWT's payload (the `sub`/`tenant_id`/`role` claims).
    Used internally by auth.get_current_user; never returned to clients.
    """

    sub: str  # user id, as string (JWT claims must be JSON-serializable)
    tenant_id: str
    role: UserRole
    exp: int


class CurrentUser(BaseModel):
    """
    Lightweight, request-scoped representation of "who is calling this
    endpoint", built from the JWT — NOT re-fetched from the DB on every
    request. Endpoints that need fresh DB fields should query explicitly.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    role: UserRole


# ---------------------------------------------------------------------------
# Phase 2: Parties
# ---------------------------------------------------------------------------
class PartyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: PartyType
    phone: str | None = Field(None, max_length=20)
    address: str | None = Field(None, max_length=500)


class PartyCreate(PartyBase):
    """
    Note: no `outstanding_balance` field here on purpose — a party's ledger
    balance is derived from transactions (Phase 3), not client-supplied at
    creation. New parties always start at 0.
    """

    pass


class PartyUpdate(BaseModel):
    """All fields optional for PATCH-style partial updates."""

    name: str | None = Field(None, min_length=1, max_length=255)
    phone: str | None = Field(None, max_length=20)
    address: str | None = Field(None, max_length=500)
    is_active: bool | None = None


class PartyRead(PartyBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    outstanding_balance: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Phase 2: Items
# ---------------------------------------------------------------------------
class ItemBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    sku: str | None = Field(None, max_length=100)
    base_unit: str = Field("pcs", max_length=20)
    tax_rate: Decimal = Field(Decimal("0.00"), ge=0, le=100)
    sale_price: Decimal = Field(Decimal("0.0000"), ge=0)
    purchase_price: Decimal = Field(Decimal("0.0000"), ge=0)

    @field_validator("tax_rate", "sale_price", "purchase_price", mode="before")
    @classmethod
    def _coerce_numeric_strings(cls, v: Decimal | str | float) -> Decimal:
        # Accepts "199.50" (string, safest for clients) or a float/Decimal,
        # but always normalizes to Decimal — never let a float slip into
        # a money field.
        return Decimal(str(v))


class ItemCreate(ItemBase):
    """
    `current_stock` is intentionally settable at creation (opening stock)
    but NOT via ItemUpdate below — after creation, stock only moves through
    transaction logic (Phase 3) or a dedicated stock-adjustment endpoint.
    """

    current_stock: Decimal = Field(Decimal("0.0000"), ge=0)

    @field_validator("current_stock", mode="before")
    @classmethod
    def _coerce_stock(cls, v: Decimal | str | float) -> Decimal:
        return Decimal(str(v))


class ItemUpdate(BaseModel):
    """All fields optional for PATCH-style partial updates. No stock field."""

    name: str | None = Field(None, min_length=1, max_length=255)
    sku: str | None = Field(None, max_length=100)
    base_unit: str | None = Field(None, max_length=20)
    tax_rate: Decimal | None = Field(None, ge=0, le=100)
    sale_price: Decimal | None = Field(None, ge=0)
    purchase_price: Decimal | None = Field(None, ge=0)
    is_active: bool | None = None


class ItemRead(ItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    current_stock: Decimal
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Phase 3: Transactions (the POS sale)
# ---------------------------------------------------------------------------
class TransactionItemCreate(BaseModel):
    """One scanned/entered line in the cart."""

    item_id: uuid.UUID
    quantity: Decimal = Field(
        ..., gt=0, description="Fractional quantities allowed, e.g. 2.5 kg"
    )
    # If omitted, the item's current `sale_price` is used. Present so the
    # cashier can override it per the flowchart's "Enter Quantity & Edit Price".
    unit_price: Decimal | None = Field(None, ge=0)

    @field_validator("quantity", "unit_price", mode="before")
    @classmethod
    def _coerce_numeric_strings(cls, v: Decimal | str | float | None) -> Decimal | None:
        if v is None:
            return None
        return Decimal(str(v))


class TransactionCreate(BaseModel):
    # Optional: a walk-in cash sale paid in full needn't name a party.
    # Required by the API if the sale isn't paid in full — see
    # routers/transactions.py for that cross-field validation (it needs
    # the computed net_amount, so it can't be expressed as a pure schema rule).
    party_id: uuid.UUID | None = None
    doc_type: DocType = DocType.INVOICE
    items: list[TransactionItemCreate] = Field(..., min_length=1)
    discount_amount: Decimal = Field(Decimal("0.0000"), ge=0)
    paid_amount: Decimal = Field(Decimal("0.0000"), ge=0)

    @field_validator("discount_amount", "paid_amount", mode="before")
    @classmethod
    def _coerce_totals(cls, v: Decimal | str | float) -> Decimal:
        return Decimal(str(v))


class TransactionItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    item_id: uuid.UUID
    item_name: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal
    line_subtotal: Decimal
    line_tax: Decimal
    line_total: Decimal


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    party_id: uuid.UUID | None
    created_by_user_id: uuid.UUID
    doc_type: DocType
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    net_amount: Decimal
    paid_amount: Decimal
    balance_due: Decimal
    payment_status: PaymentStatus
    created_at: datetime
    items: list[TransactionItemRead]


# ---------------------------------------------------------------------------
# Phase 4a: Party Payments & Statement
# ---------------------------------------------------------------------------
class PartyPaymentCreate(BaseModel):
    amount: Decimal = Field(..., gt=0)
    note: str | None = Field(None, max_length=255)

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, v: Decimal | str | float) -> Decimal:
        return Decimal(str(v))


class PartyPaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    party_id: uuid.UUID
    created_by_user_id: uuid.UUID
    amount: Decimal
    note: str | None
    created_at: datetime


class LedgerEntry(BaseModel):
    """
    One row of a party's statement — either a SALE (a Transaction that
    left a balance_due, so it added to what the party owes) or a PAYMENT
    (money collected against that debt). `amount` is signed: positive for
    a SALE debit, negative for a PAYMENT credit, so `running_balance` is
    just the cumulative sum down the list.
    """

    entry_type: Literal["SALE", "PAYMENT"]
    reference_id: uuid.UUID
    created_at: datetime
    description: str
    amount: Decimal
    running_balance: Decimal


class PartyStatementResponse(BaseModel):
    party: PartyRead
    entries: list[LedgerEntry]
    # Authoritative figure straight from Party.outstanding_balance — should
    # always equal the last entry's running_balance (or 0 if there are no
    # entries). Included separately as a sanity check / quick-glance total.
    current_outstanding_balance: Decimal
