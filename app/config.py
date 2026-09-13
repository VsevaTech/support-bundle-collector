from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SBC_", extra="ignore")

    database_url: str = "sqlite:///./data/sbc.db"
    upload_dir: Path = Path("./data/uploads")
    # Public origin used to build customer links (no trailing slash). Empty = derive from request.
    base_url: str = ""
    default_ttl_hours: int = 24
    max_ttl_hours: int = 24 * 7
    max_upload_mb: int = 8
    # Optional shared secret protecting /support/* pages. Empty = no protection (local dev only).
    support_access_key: str = ""

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
