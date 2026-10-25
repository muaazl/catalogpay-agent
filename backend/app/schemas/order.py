"""Order ledger API contracts. All amounts are integer cents."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.models.order import OrderStatus, PayoutStatus, PayPalMode

IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_.:\-]{8,128}$"
PUBLIC_ORDER_ID_PATTERN = r"^ORD-[A-Z0-9]{12}$"


class CreateOrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    quantity: StrictInt = Field(ge=1, le=10_000)
    raw_query: str | None = Field(default=None, max_length=500)


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CreateOrderItem] = Field(min_length=1, max_length=100)
    customer_note: str | None = Field(default=None, max_length=2000)
    parser_mode: Literal["gemini", "deterministic_fallback", "manual"] | None = None
    idempotency_key: str | None = Field(default=None, pattern=IDEMPOTENCY_KEY_PATTERN)


class OrderLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_id: int
    vendor_id: int
    sku_code: str
    product_name: str
    raw_query: str | None
    quantity: int
    unit_price_cents: int
    subtotal_cents: int
    commission_rate_bps: int
    platform_fee_cents: int
    net_vendor_payout_cents: int


class VendorPayoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor_id: int
    vendor_name: str
    receiver_paypal_email: str
    currency: str
    gross_subtotal_cents: int
    platform_fee_cents: int
    payout_amount_cents: int
    sender_batch_id: str
    sender_item_id: str
    paypal_payout_batch_id: str | None
    paypal_payout_item_id: str | None
    status: PayoutStatus
    attempt_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class OrderSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_order_id: str
    status: OrderStatus
    currency: str
    gross_subtotal_cents: int
    total_platform_fee_cents: int
    total_vendor_payout_cents: int
    paypal_mode: PayPalMode
    paypal_order_id: str | None
    paypal_capture_id: str | None
    created_at: datetime
    updated_at: datetime
    captured_at: datetime | None


class OrderOut(OrderSummaryOut):
    idempotency_key: str
    customer_note: str | None
    parser_mode: str | None
    approval_url: str | None
    paypal_capture_status: str | None
    stock_reserved: bool
    payout_hold_reason: str | None
    last_error: str | None
    line_items: list[OrderLineOut]
    payouts: list[VendorPayoutOut]


class OrderListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[OrderSummaryOut]


class WebhookAck(BaseModel):
    status: Literal["processed", "duplicate", "ignored", "rejected"]
    event_id: str
    event_type: str
    detail: str
