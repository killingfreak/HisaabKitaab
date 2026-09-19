import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import User, UserRole
from schemas import CurrentUser, TokenPayload

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
# bcrypt via Passlib. `deprecated="auto"` lets us migrate to a stronger
# scheme (e.g. argon2) later — Passlib will re-hash on next successful
# login for anyone still on the old scheme, no forced password resets.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# JWT creation
# ---------------------------------------------------------------------------
def create_access_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
) -> tuple[str, int]:
    """
    Builds a signed, self-contained JWT embedding `tenant_id` and `role`
    as claims. Because the token carries the tenant on its face, every
    downstream request can be scoped to the correct shop WITHOUT an extra
    DB round-trip — the single most important property in a multi-tenant
    billing system, where a cross-tenant data leak is the worst-case bug.

    Returns (token, expires_in_seconds) so the caller can build the
    TokenResponse without recomputing the expiry window.
    """
    expire_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    expire_at = datetime.now(timezone.utc) + expire_delta

    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "exp": expire_at,
    }
    token = jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return token, int(expire_delta.total_seconds())


# ---------------------------------------------------------------------------
# get_current_user dependency
# ---------------------------------------------------------------------------
# OAuth2PasswordBearer only tells FastAPI/Swagger where the "Authorize"
# button should point (and how to extract the `Authorization: Bearer ...`
# header) — our actual login endpoint takes a plain JSON body, not the
# OAuth2 password-grant form, which is fine; this class doesn't enforce that.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """
    Decodes and validates the bearer JWT, then re-confirms the user still
    exists, is active, AND still belongs to the claimed tenant_id.

    Re-checking against the DB on every call (rather than trusting the
    token blindly for its full lifetime) means deactivating a cashier or
    revoking access takes effect immediately — important when "fire the
    cashier right now" needs to actually work, not wait ~12h for the JWT
    to expire. The cost is one indexed primary-key lookup per request,
    which is a reasonable tradeoff for a financial system.
    """
    try:
        raw_payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        token_data = TokenPayload(**raw_payload)
    except (JWTError, ValidationError):
        raise credentials_exception

    try:
        user_id = uuid.UUID(token_data.sub)
    except ValueError:
        raise credentials_exception

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == token_data.tenant_id,  # belt-and-braces tenant check
        )
    )
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception

    return CurrentUser(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        role=user.role,
    )


def require_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """
    RBAC gate for Admin-only routes (financial reports, tenant settings,
    user management). Attach via:
        @router.get("/reports", dependencies=[Depends(require_admin)])
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires Admin privileges.",
        )
    return current_user
