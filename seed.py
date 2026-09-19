import asyncio
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

# Import your existing engine and models
from database import engine
from models import Tenant, User, UserRole

# Setup bcrypt hasher (matches your auth.py logic)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


async def seed():
    async with AsyncSession(engine) as session:
        # 1. Create a Test Tenant
        tenant = Tenant(business_name="Test Hardware Store")
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)

        # 2. Create a Test Admin User for that Tenant
        user = User(
            tenant_id=tenant.id,
            full_name="Admin User",
            email="admin@test.com",
            hashed_password=get_password_hash("testpassword123"),
            role=UserRole.ADMIN,
        )
        session.add(user)
        await session.commit()



if __name__ == "__main__":
    asyncio.run(seed())
