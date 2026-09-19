from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralized, typed application settings loaded from environment
    variables (or a local .env file). Keeping this in one place means
    every module (database.py, auth.py, main.py) imports the SAME
    validated config instead of scattering os.environ[...] calls.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database ---
    # Must use the asyncpg driver prefix for SQLAlchemy's async engine.
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:Mahaveer%401307@127.0.0.1:5432/hisaabkitaab"
    )

    # --- JWT / Security ---
    # In production, inject this via a secrets manager (AWS Secrets Manager,
    # GCP Secret Manager, Vault, etc.) — never commit a real value.
    JWT_SECRET_KEY: str = "killingfreak"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12  # 12 hours — one retail shift+


settings = Settings()
