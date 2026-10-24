"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "CatalogPay Agent"
    environment: Literal["local", "test", "staging", "production"] = "local"
    api_v1_prefix: str = "/api/v1"

    database_url: str = "sqlite:///./catalogpay.db"
    database_echo: bool = False
    seed_on_startup: bool = True

    default_currency: str = Field(default="USD", min_length=3, max_length=3)

    # Hybrid matcher decision thresholds (confidence is normalized to 0.0-1.0).
    match_confidence_threshold: float = Field(default=0.62, ge=0.0, le=1.0)
    low_confidence_threshold: float = Field(default=0.40, ge=0.0, le=1.0)
    match_top_k_default: int = Field(default=3, ge=1, le=25)

    @model_validator(mode="after")
    def _check_thresholds(self) -> "Settings":
        if self.low_confidence_threshold > self.match_confidence_threshold:
            raise ValueError("LOW_CONFIDENCE_THRESHOLD must not exceed MATCH_CONFIDENCE_THRESHOLD")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
