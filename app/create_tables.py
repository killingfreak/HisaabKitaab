import asyncio

# Adjust these imports based on your actual folder structure (e.g., app.database)
from app.database import engine
from app.models import (
    Base,
)  # IMPORTANT: This imports your declarative base and all table models


async def init_models():
    print("Connecting to Supabase and ensuring tables exist...")
    async with engine.begin() as conn:
        # This reads your models and creates tables that don't exist yet
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables verified!")


if __name__ == "__main__":
    asyncio.run(init_models())
