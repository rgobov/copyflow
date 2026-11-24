from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "copyflow"
    debug: bool = True

    database_url: str = "sqlite+aiosqlite:///./copyflow.db"

    redis_url: str | None = None

    fernet_key: str = ""

    hyperliquid_api_url: str = "https://api.hyperliquid.xyz"

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = False

    hl_agent_address: str = ""
    hl_agent_private_key: str = ""
    hl_builder_code: str = ""

    telegram_bot_token: str = ""

    hl_poll_interval_sec: float = 7.0
    reconcile_interval_sec: float = 30.0
    risk_check_interval_sec: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
