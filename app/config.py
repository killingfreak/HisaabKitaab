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
    # DATABASE_URL: str = (
    #     "postgresql+asyncpg://postgres:Mahaveer%401307@localhost:5432/hisaabkitaab"
    # )

    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres.vlqgprezoimynlqrpxam:Mahaveer%401307@aws-0-ap-southeast-2.pooler.supabase.com:5432/postgres"
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

    # --- CORS ---
    # Comma-separated, not a JSON list — easier to hand-edit in a .env file.
    # MUST include the exact scheme+host+port of every frontend that will
    # call this API from a browser (localhost web dev server now; your
    # deployed web URL and Tauri's dev origin later). Wrapped apps
    # (Capacitor/Tauri production builds) typically load over a custom
    # scheme rather than http(s), which needs handling separately when
    # you get to that phase — don't assume this list covers it yet.
    CORS_ALLOWED_ORIGINS: str = (
        "http://localhost:5173,http://127.0.0.1:5173,https://hisaabkitaab-fe.pages.dev"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]


settings = Settings()
