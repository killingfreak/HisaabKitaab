from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, get_current_user, verify_password
from database import Base, engine, get_db
from models import User
from schemas import CurrentUser, LoginRequest, TokenResponse
from fastapi.security import OAuth2PasswordRequestForm

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    App startup/shutdown hook.

    `Base.metadata.create_all` is a dev-only convenience that spins up the
    schema on a fresh local/staging DB. In production, REPLACE this with
    Alembic migrations run as a separate deploy step — create_all cannot
    express schema changes (renames, constraint additions, data backfills)
    once real tenant data exists.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # dev-only
    yield
    await engine.dispose()


app = FastAPI(
    title="HisaabKitaab API",
    description="Multi-tenant billing & inventory management for retail/hardware stores.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.post("/login", response_model=TokenResponse, tags=["auth"])
async def login(
    credentials: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:

    result = await db.execute(
        select(User).where(User.email == credentials.username, User.is_active.is_(True))
    )

    user = result.scalars().first()

    if user is None or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    access_token, expires_in = create_access_token(
        user_id=user.id, tenant_id=user.tenant_id, role=user.role.value
    )

    return TokenResponse(access_token=access_token, expires_in=expires_in)


@app.get("/me", response_model=CurrentUser, tags=["auth"])
async def read_current_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Sanity-check route: confirms the JWT decodes and tenant scoping works end-to-end."""
    return current_user
