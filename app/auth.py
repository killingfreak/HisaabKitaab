"""
auth.py
Password hashing, JWT issuance/verification, and the reusable FastAPI
dependencies that enforce authentication + tenant isolation + RBAC.

Every protected endpoint in the system should depend on `get_current_user`
(or `require_role(...)`) rather than re-implementing token parsing —
that's what keeps tenant_id extraction consistent and auditable in one
place.
"""

import os
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import ValidationError

from app.schemas import CurrentUser, TokenPayload

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# SECRET_KEY must come from the environment in every real deployment —
# the fallback here exists only so the app boots locally without setup.
# Generate a real one with: openssl rand -hex 32
SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "CHANGE-ME-INSECURE-DEV-ONLY-SECRET")
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
# We call the `bcrypt` library directly rather than going through Passlib's
# CryptContext. Passlib has been unmaintained since ~2020 and its bcrypt
# backend-detection shim breaks under bcrypt>=4.1 (raises spurious
# "password cannot be longer than 72 bytes" / AttributeError on
# bcrypt.__about__), so we sidestep that whole class of bug.
#
# bcrypt is deliberately slow (adaptive cost) — that's what makes brute
# forcing stolen hashes expensive. Cost factor 12 is a reasonable default
# in 2026; raise it if your auth server's hardware can absorb the extra
# latency without hurting login throughput.
_BCRYPT_ROUNDS = 12

# bcrypt has a hard 72-BYTE input limit (not 72 characters — multi-byte
# UTF-8, e.g. emoji or Devanagari in a passphrase, can exceed this well
# before 72 chars). We enforce a 72-char cap at the Pydantic layer
# (schemas.LoginRequest) so this never silently truncates a user's
# intended password.


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password for storage. Call this once, at signup."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a login attempt's password against the stored bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        # Malformed/corrupt hash in the DB — treat as a failed login, not a 500.
        return False


# ---------------------------------------------------------------------------
# JWT issuance
# ---------------------------------------------------------------------------
def create_access_token(*, user_id: str, tenant_id: str, role: str) -> tuple[str, int]:
    """
    Build a signed JWT embedding the claims every downstream request needs
    to enforce tenant isolation and RBAC WITHOUT hitting the database:
      - sub: the user's id
      - tenant_id: the shop this user belongs to (THE isolation boundary)
      - role: ADMIN | CASHIER

    Returns (token, expires_in_seconds).
    """
    expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    expire_at = datetime.now(timezone.utc) + expires_delta

    to_encode = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "role": role,
        "exp": expire_at,
    }
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt, int(expires_delta.total_seconds())


# ---------------------------------------------------------------------------
# JWT verification / FastAPI dependencies
# ---------------------------------------------------------------------------
# tokenUrl points at our own /login endpoint purely so interactive docs
# (Swagger UI's "Authorize" button) know where to send credentials.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")


def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:
    """
    Decode + validate the bearer JWT and return a `CurrentUser`.

    This is stateless by design: no DB round-trip is made here. That's
    what "stateless JWT auth" buys us — cheap, horizontally-scalable auth
    checks on every request. Endpoints that need live DB fields (e.g. to
    confirm the user wasn't deactivated mid-session) should additionally
    fetch the User row themselves.

    Raises 401 for any missing/invalid/expired token so callers get a
    uniform, spec-compliant error response.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        token_data = TokenPayload(**payload)
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (JWTError, ValidationError):
        raise credentials_exception

    return CurrentUser(
        id=token_data.sub,
        tenant_id=token_data.tenant_id,
        role=token_data.role,
    )


def require_role(*allowed_roles: str):
    """
    RBAC dependency factory. Usage:

        @app.delete("/items/{item_id}")
        async def delete_item(
            item_id: uuid.UUID,
            current_user: CurrentUser = Depends(require_role("ADMIN")),
        ):
            ...

    Only ADMIN can hit that endpoint; CASHIER gets a 403.
    """

    def _dependency(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not permitted to perform this action",
            )
        return current_user

    return _dependency
