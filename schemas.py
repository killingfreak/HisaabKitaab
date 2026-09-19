import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from models import UserRole


class LoginRequest(BaseModel):
    """Payload for POST /login — plain email + password credential auth."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    """Returned on successful login: a stateless bearer JWT."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until expiry — lets clients schedule a refresh/re-login


class TokenPayload(BaseModel):
    """
    Shape of the decoded JWT claims. This is an INTERNAL contract between
    `create_access_token` and `get_current_user` in auth.py — it is never
    returned directly from an endpoint.
    """

    sub: str  # user id ("subject"), stored as a string per JWT convention
    tenant_id: uuid.UUID
    role: UserRole
    exp: int  # unix timestamp expiry (standard JWT "exp" claim)


class CurrentUser(BaseModel):
    """
    Validated "who is calling this endpoint" object, built from JWT claims
    (and re-confirmed against the DB in get_current_user). Route handlers
    depend on this instead of the raw User ORM model to keep the web layer
    decoupled from ORM internals.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    role: UserRole
