from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read from environment variables (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://localhost/radar_vie"
    # Argon2 hash of the owner's password (scripts/set_password.py). Empty: nobody can sign in.
    app_password_hash: str = ""
    # Signs the session cookie. At least 32 characters, or sign-in stays closed.
    session_secret: str = ""
    # Bearer token of the scheduled agents that write offers and letters. Empty: agent writes refused.
    api_token: str = ""
    session_days: int = 90
    secure_cookies: bool = True
    timezone: str = "Europe/Paris"


@lru_cache
def get_settings() -> Settings:
    return Settings()
