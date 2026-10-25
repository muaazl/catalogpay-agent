"""Pydantic v2 API contracts for the catalog and match-preview endpoints."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.services.sku_matcher import MatchStatus


class VendorSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    paypal_merchant_email: str
    commission_rate_bps: int


class VendorOut(VendorSummary):
    is_active: bool
    created_at: datetime


class SKUOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    name: str
    description: str
    category: str
    unit_of_measure: str
    aliases: list[str] = Field(validation_alias="aliases_json")
    unit_price_cents: int
    currency: str
    stock_quantity: int
    is_active: bool
    vendor: VendorSummary


class SKUListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[SKUOut]


class DatabaseHealth(BaseModel):
    connected: bool
    dialect: str
    error: str | None = None


class IntegrationsHealth(BaseModel):
    parser_mode: str  # "gemini" | "deterministic_fallback"
    parser_model: str
    paypal_mode: str | None  # "live_sandbox" | "sandbox_simulation" | None (not configured)
    paypal_env: str


class HealthResponse(BaseModel):
    status: str
    app: str
    environment: str
    database: DatabaseHealth
    active_sku_count: int
    integrations: IntegrationsHealth


# --- match preview -----------------------------------------------------------------------


class MatchPreviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500, examples=["arabica beans half kg"])
    quantity: StrictInt = Field(ge=1, le=100_000, examples=[4])
    # Operator override: resolve this line to exactly this SKU (e.g. picked from candidates).
    sku_code: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()


class MatchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MatchPreviewItem] = Field(min_length=1, max_length=100)
    top_k: StrictInt = Field(default=3, ge=1, le=10)


class StockStatus(StrEnum):
    IN_STOCK = "in_stock"
    INSUFFICIENT_STOCK = "insufficient_stock"
    NOT_APPLICABLE = "not_applicable"


class CandidateOut(BaseModel):
    sku_id: int
    sku_code: str
    name: str
    unit_of_measure: str
    unit_price_cents: int
    currency: str
    stock_quantity: int
    confidence_score: float = Field(ge=0.0, le=1.0)
    match_reasoning: str
    vendor: VendorSummary


class LineItemPreview(BaseModel):
    line_index: int
    query: str
    normalized_query: str
    quantity: int
    match_status: MatchStatus
    is_ambiguous: bool
    is_pinned: bool = False
    matched_sku: CandidateOut | None
    candidates: list[CandidateOut]
    stock_status: StockStatus
    available_quantity: int | None
    is_billable: bool
    issues: list[str]
    unit_price_cents: int
    subtotal_cents: int
    commission_rate_bps: int
    platform_fee_cents: int
    net_vendor_payout_cents: int


class VendorPayoutPreview(BaseModel):
    vendor_id: int
    vendor_name: str
    paypal_merchant_email: str
    commission_rate_bps: int
    line_count: int
    subtotal_cents: int
    platform_fee_cents: int
    net_vendor_payout_cents: int


class PreviewTotals(BaseModel):
    subtotal_cents: int
    platform_fee_cents: int
    net_vendor_payout_cents: int


class MatchPreviewResponse(BaseModel):
    currency: str
    all_lines_billable: bool
    unresolved_line_indexes: list[int]
    lines: list[LineItemPreview]
    vendor_payouts: list[VendorPayoutPreview]
    totals: PreviewTotals
