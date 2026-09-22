"""
config.py
Centralized, typed application settings loaded from environment
variables (or a local .env file). Keeping this in one place means every
module (database.py, auth.py, main.py) imports the SAME validated
config instead of scattering os.environ[...] / os.getenv(...) calls.

SECURITY: the defaults below are intentionally NOT real credentials.
Real values belong only in a local, git-ignored `.env` file (see
`.env.example` for the shape) or in your deployment platform's secrets
manager — never in this file, since this file is committed to version
control.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database ---
    # Must use the asyncpg driver prefix for SQLAlchemy's async engine.
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:Mahaveer%401307@localhost:5432/hisaabkitaab"
    )

    # --- JWT / Security ---
    # In production, inject this via a secrets manager (AWS Secrets Manager,
    # GCP Secret Manager, Vault, etc.) — never commit a real value.
    # Generate one with: openssl rand -hex 32
    JWT_SECRET_KEY: str = "CHANGE-ME-INSECURE-DEV-ONLY-SECRET"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12 hours — one retail shift+

    # --- Misc ---
    SQL_ECHO: bool = False  # flip to True locally to log every SQL statement


settings = Settings()
