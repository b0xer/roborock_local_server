"""Runtime configuration, read from environment variables or a `.env` file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_token: str = ""
    """If set, /api/* (except /api/health) and /mcp require `Authorization: Bearer <token>`."""

    roborock_device: str = ""
    """Device name or DUID to control. Empty selects the first supported vacuum."""

    roborock_email: str = ""
    """Optional Roborock account email used to pre-fill the login form and CLI prompt."""

    host: str = "0.0.0.0"
    port: int = 8080

    data_dir: Path = Path("data")
    """Directory for credentials and device cache. The container sets this to /data."""

    status_cache_seconds: float = 5.0
    """Reuse a status response for this many seconds to avoid hammering the robot."""

    allow_raw_commands: bool = False
    """Expose the raw-command REST endpoint and MCP tool."""

    log_level: str = "INFO"

    @property
    def credentials_path(self) -> Path:
        return self.data_dir / "credentials.json"

    @property
    def cache_path(self) -> Path:
        return self.data_dir / "device_cache.pkl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
