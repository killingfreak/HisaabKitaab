"""
models.py
Async SQLAlchemy 2.0 declarative models for HisaabKitaab.

Phase 1 scope: `Tenant` (a shop/business) and `User` (staff who log in).
Every other domain table (Parties, Items, Transactions, TransactionItems)
arrives in later phases, but will follow the same `tenant_id` isolation
pattern established here — see the multi-tenancy note on `User.tenant_id`.

NOTE ON NUMERIC TYPES: this file has no financial columns yet, but every
model added later (Items.current_stock, Transactions.net_amount, etc.)
MUST use `NUMERIC(12, 4)` (SQLAlchemy `Numeric(12, 4)`), never Float —
floats introduce rounding drift that is unacceptable in billing.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base. All future domain models inherit from this."""

    pass


class UserRole(str, enum.Enum):
    """RBAC roles. Extend this enum (e.g. MANAGER) as the product grows."""

    ADMIN = "ADMIN"  # Shop owner: full access, including settings & reports
    CASHIER = "CASHIER"  # Staff: POS / billing access only


class Tenant(Base):
    """
    A single shop/business subscribed to HisaabKitaab.
    Every other table in the system is scoped to a `tenant_id` FK pointing
    here — this is the root of the multi-tenancy model.
    """

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    gst_number: Mapped[str | None] = mapped_column(String(15), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tenant id={self.id} business_name={self.business_name!r}>"


class User(Base):
    """
    A staff member (Admin or Cashier) who can log into a specific tenant.
    """

    __tablename__ = "users"
    __table_args__ = (
        # Phase 1 keeps login simple: email is globally unique, so /login
        # only needs email+password (no tenant selector). If you later
        # want the SaaS-standard "same email, different shops" pattern,
        # drop this and instead enforce UniqueConstraint(tenant_id, email),
        # then require a tenant slug/subdomain at login time.
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- Multi-tenancy anchor -------------------------------------------------
    # Every non-tenant table in the schema carries this column. Application
    # code (see auth.get_current_user) must ALWAYS filter queries by the
    # tenant_id extracted from the caller's JWT — never trust a tenant_id
    # passed in a request body/query param, to prevent cross-tenant leaks.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), nullable=False, default=UserRole.CASHIER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="users")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


# ---------------------------------------------------------------------------
# Phase 2: Parties & Items
# ---------------------------------------------------------------------------
# NUMERIC(12, 4) everywhere money or stock quantity is involved: 8 integer
# digits + 4 decimal places, enough for gram/ml-level fractional units
# (e.g. 0.2500 kg of nails) without ever touching a binary float.


class PartyType(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"


class Party(Base):
    """
    A customer or supplier belonging to one tenant (shop). `outstanding_balance`
    is the running "udhaar" (credit) ledger total — positive means the party
    owes the shop money (customer credit sale), by convention.
    """

    __tablename__ = "parties"
    __table_args__ = (
        # Two shops can each have their own "Ramesh Traders" — uniqueness is
        # scoped to the tenant, not global.
        UniqueConstraint(
            "tenant_id", "name", "type", name="uq_parties_tenant_name_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[PartyType] = mapped_column(
        SAEnum(PartyType, name="party_type"), nullable=False
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Credit/debit ledger balance. Only ever mutated by transaction logic
    # (Phase 3) — never edited directly by a PATCH endpoint here.
    outstanding_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0.0000")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Party id={self.id} name={self.name!r} type={self.type}>"


class Item(Base):
    """
    A stock-keeping item belonging to one tenant. `current_stock` is a
    running quantity — decremented/incremented by transaction logic
    (Phase 3), never written directly by a client PATCH.
    """

    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_items_tenant_name"),
        # Guards against corrupt data even if a future code path forgets to
        # validate: a tax rate can never be negative or nonsensically large.
        CheckConstraint(
            "tax_rate >= 0 AND tax_rate <= 100", name="ck_items_tax_rate_range"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # e.g. "kg", "pcs", "ltr", "box" — free text by design (hardware stores
    # sell everything from loose nails by weight to boxed fittings by count).
    base_unit: Mapped[str] = mapped_column(String(20), nullable=False, default="pcs")

    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00")
    )
    sale_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0.0000")
    )
    purchase_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0.0000")
    )

    # Supports fractional stock (e.g. 12.5000 kg) per the product brief.
    current_stock: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0.0000")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Item id={self.id} name={self.name!r} stock={self.current_stock}>"


# ---------------------------------------------------------------------------
# Phase 3: Transactions & TransactionItems (the POS sale itself)
# ---------------------------------------------------------------------------
class DocType(str, enum.Enum):
    INVOICE = "INVOICE"
    CHALLAN = "CHALLAN"


class PaymentStatus(str, enum.Enum):
    PAID = "PAID"
    PARTIAL = "PARTIAL"
    UNPAID = "UNPAID"


class Transaction(Base):
    """
    A completed sale/document (Invoice or Challan). Deliberately IMMUTABLE
    once created — no fields here are ever updated by a PATCH endpoint.
    Corrections happen via a future void/credit-note flow, not by editing
    financial history in place.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nullable: a pure cash/walk-in sale with no ledger tracking needn't
    # name a party. Required only when the sale isn't paid in full (see
    # routers/transactions.py validation).
    party_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Who rang up this sale — useful for cashier-level audit/reporting later.
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    doc_type: Mapped[DocType] = mapped_column(
        SAEnum(DocType, name="doc_type"), nullable=False
    )

    # subtotal = sum of line subtotals (qty * unit_price), pre-tax, pre-discount
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # flat currency amount knocked off after tax (cashier-entered "global discount")
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0.0000")
    )
    # sum of per-line GST, computed on each line's own subtotal
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # net_amount = subtotal + tax_amount - discount_amount  (what the customer owes)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0.0000")
    )
    # balance_due = net_amount - paid_amount; always >= 0 (overpayment is rejected, not stored)
    balance_due: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    items: Mapped[list["TransactionItem"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Transaction id={self.id} doc_type={self.doc_type} net_amount={self.net_amount}>"


class TransactionItem(Base):
    """
    One line of a sale. Snapshots `item_name`, `tax_rate` and the agreed
    `unit_price` at time of sale — deliberately NOT a live join to Item,
    so a later rename/price/tax-rate change on the Item never rewrites
    the history of an already-issued invoice.
    """

    __tablename__ = "transaction_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_transaction_items_quantity_positive"),
        CheckConstraint(
            "unit_price >= 0", name="ck_transaction_items_unit_price_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # RESTRICT: an Item that has ever been sold can be soft-deactivated
    # (Item.is_active=False) but never hard-deleted, so this FK is safe.
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    item_name: Mapped[str] = mapped_column(String(255), nullable=False)  # snapshot
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False
    )  # snapshot, cashier-editable at sale time
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)  # snapshot

    line_subtotal: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False
    )  # quantity * unit_price
    line_tax: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False
    )  # line_subtotal * tax_rate / 100
    line_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False
    )  # line_subtotal + line_tax

    transaction: Mapped["Transaction"] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TransactionItem item_name={self.item_name!r} qty={self.quantity}>"


# ---------------------------------------------------------------------------
# Phase 4a: Party Payments (the other half of the ledger)
# ---------------------------------------------------------------------------
# A Transaction with balance_due > 0 is a DEBIT — it increases what the
# party owes. PartyPayment is the corresponding CREDIT — money collected
# against that existing debt, outside of any new sale (e.g. a customer
# walks in next week and clears part of their udhaar). Together, sales
# + payments reconstruct Party.outstanding_balance as a full history,
# not just a single running number.
class PartyPayment(Base):
    __tablename__ = "party_payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_party_payments_amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    party_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PartyPayment party_id={self.party_id} amount={self.amount}>"
