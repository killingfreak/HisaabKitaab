import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class UserRole(str, enum.Enum):
    """
    RBAC roles referenced throughout the app.
    ADMIN   -> shop owner: full access (reports, settings, user management).
    CASHIER -> staff: POS / billing operations only.
    Inheriting from `str` lets this enum serialize cleanly in both
    Pydantic schemas and JWT claims without extra conversion.
    """

    ADMIN = "admin"
    CASHIER = "cashier"


class Tenant(Base):
    """
    Root of the multi-tenancy hierarchy — one row per shop/business.
    Every other table in the system (Users, Parties, Items, Transactions,
    ...) carries a `tenant_id` FK back to this table, and EVERY query
    downstream must be filtered by it. Nothing crosses tenant boundaries.
    """

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    gst_number: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    # One tenant -> many staff/owner logins.
    users: Mapped[list["User"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} business_name={self.business_name!r}>"


class User(Base):
    """
    A login-capable account (Admin/Owner or Cashier/Staff) belonging to
    exactly one Tenant. Passwords are stored ONLY as bcrypt hashes.

    `email` is unique PER TENANT rather than globally: two different shops
    onboarding independently may legitimately register a cashier with the
    same email address, and that shouldn't collide across tenants.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),
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

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role_enum", native_enum=True),
        nullable=False,
        default=UserRole.CASHIER,
    )

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="users")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
