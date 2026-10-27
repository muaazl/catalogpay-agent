"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repository root (same layout inside the Docker image: /app).
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RETURN_URL = "http://localhost:5173/checkout/return"
DEFAULT_CANCEL_URL = "http://localhost:5173/checkout/cancel"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env", ".env"),
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
    # One completed multi-vendor order (ORD-DEMO00000001) so the ledger is never empty in demos.
    seed_demo_orders: bool = True

    # Built React app to serve from FastAPI in production. Empty -> auto-detect frontend/dist.
    static_dist_dir: str = ""
    # Set by Render on every web service; used to derive PayPal return/cancel URLs.
    render_external_url: str = ""

    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    # Browser origins allowed to call the API (the Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Hybrid matcher decision thresholds (confidence is normalized to 0.0-1.0).
    match_confidence_threshold: float = Field(default=0.62, ge=0.0, le=1.0)
    low_confidence_threshold: float = Field(default=0.40, ge=0.0, le=1.0)
    match_top_k_default: int = Field(default=3, ge=1, le=25)

    # Gemini order parser. An empty key disables Gemini (text falls back to the regex parser).
    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = DEFAULT_GEMINI_MODEL
    gemini_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
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
    paypal_return_url: str = DEFAULT_RETURN_URL
    paypal_cancel_url: str = DEFAULT_CANCEL_URL
    paypal_brand_name: str = Field(default="CatalogPay", max_length=127)
    # Signs simulated webhooks so the simulation endpoint cannot be trivially forged.
    paypal_simulation_secret: str = Field(default="catalogpay-local-simulation", repr=False)

    def resolved_static_dist(self) -> Path | None:
        """The built frontend directory to serve, or None when there is nothing to serve."""
        candidate = (
            Path(self.static_dist_dir) if self.static_dist_dir else REPO_ROOT / "frontend/dist"
        )
        return candidate if (candidate / "index.html").is_file() else None

    @property
    def paypal_credentials_configured(self) -> bool:
        return bool(self.paypal_client_id.strip() and self.paypal_client_secret.strip())

    @model_validator(mode="after")
    def _check_thresholds(self) -> "Settings":
        if self.low_confidence_threshold > self.match_confidence_threshold:
            raise ValueError("LOW_CONFIDENCE_THRESHOLD must not exceed MATCH_CONFIDENCE_THRESHOLD")
        return self

    @model_validator(mode="after")
    def _derive_public_urls(self) -> "Settings":
        self.gemini_api_key = self.gemini_api_key.strip().strip("\"'")
        cleaned_model = self.gemini_model.strip().strip("\"'").removeprefix("models/")
        self.gemini_model = cleaned_model or DEFAULT_GEMINI_MODEL
        # On Render the SPA is served from the service's own URL; send PayPal buyers back there.
        base = self.render_external_url.rstrip("/")
        if base:
            if self.paypal_return_url == DEFAULT_RETURN_URL:
                self.paypal_return_url = f"{base}/checkout/return"
            if self.paypal_cancel_url == DEFAULT_CANCEL_URL:
                self.paypal_cancel_url = f"{base}/checkout/cancel"
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
