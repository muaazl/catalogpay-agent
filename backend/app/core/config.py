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

    # Gemini order parser. An empty key disables Gemini (text falls back to the regex parser).
    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)

    # PayPal. Without client credentials and with the mock fallback on, the client runs in
    # deterministic ``sandbox_simulation`` mode (SIM-ORDER-... / SIM-PAYOUT-... ids).
    paypal_client_id: str = Field(default="", repr=False)
    paypal_client_secret: str = Field(default="", repr=False)
    paypal_env: Literal["sandbox", "live"] = "sandbox"
    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_webhook_id: str = ""
    paypal_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    paypal_sandbox_mock_fallback: bool = True
    paypal_return_url: str = "http://localhost:5173/checkout/return"
    paypal_cancel_url: str = "http://localhost:5173/checkout/cancel"
    paypal_brand_name: str = Field(default="CatalogPay", max_length=127)
    # Signs simulated webhooks so the simulation endpoint cannot be trivially forged.
    paypal_simulation_secret: str = Field(default="catalogpay-local-simulation", repr=False)

    @property
    def paypal_credentials_configured(self) -> bool:
        return bool(self.paypal_client_id.strip() and self.paypal_client_secret.strip())

    @model_validator(mode="after")
    def _check_thresholds(self) -> "Settings":
        if self.low_confidence_threshold > self.match_confidence_threshold:
            raise ValueError("LOW_CONFIDENCE_THRESHOLD must not exceed MATCH_CONFIDENCE_THRESHOLD")
        return self

    @model_validator(mode="after")
    def _check_paypal(self) -> "Settings":
        if not self.paypal_base_url.startswith("https://"):
            raise ValueError("PAYPAL_BASE_URL must use https")
        is_sandbox_url = "sandbox" in self.paypal_base_url
        if (self.paypal_env == "live") == is_sandbox_url:
            raise ValueError("PAYPAL_ENV and PAYPAL_BASE_URL disagree (sandbox vs live)")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
