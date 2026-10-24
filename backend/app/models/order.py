"""Order & settlement ledger. All money columns are integer cents.

Arithmetic invariants are enforced by the database itself (CHECK constraints), so no code
path, including a buggy one, can persist a ledger row whose parts do not sum to its whole.
"""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{m.value}'" for m in enum)
    return f"{column} IN ({values})"


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    PAYPAL_ORDER_CREATED = "PAYPAL_ORDER_CREATED"
    CAPTURED = "CAPTURED"
    PAYOUTS_PENDING = "PAYOUTS_PENDING"
    COMPLETED = "COMPLETED"
    PAYOUTS_PARTIAL_FAILED = "PAYOUTS_PARTIAL_FAILED"
    CANCELLED = "CANCELLED"


class PayoutStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class PayPalMode(StrEnum):
    LIVE_SANDBOX = "live_sandbox"
    SANDBOX_SIMULATION = "sandbox_simulation"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(_in("status", OrderStatus), name="ck_order_status"),
        CheckConstraint(_in("paypal_mode", PayPalMode), name="ck_order_paypal_mode"),
        CheckConstraint(
            "gross_subtotal_cents >= 0 AND total_platform_fee_cents >= 0 "
            "AND total_vendor_payout_cents >= 0",
            name="ck_order_amounts_non_negative",
        ),
        CheckConstraint(
            "gross_subtotal_cents = total_platform_fee_cents + total_vendor_payout_cents",
            name="ck_order_amounts_balance",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_order_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=OrderStatus.DRAFT)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    gross_subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    total_platform_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    total_vendor_payout_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_note: Mapped[str | None] = mapped_column(Text)
    parser_mode: Mapped[str | None] = mapped_column(String(32))

    paypal_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(64), index=True)
    paypal_capture_status: Mapped[str | None] = mapped_column(String(32))
    approval_url: Mapped[str | None] = mapped_column(String(1024))
    # Set when funds are captured but vendor payouts must not run automatically (e.g. the
    # captured amount disagrees with the ledger). Requires operator review.
    payout_hold_reason: Mapped[str | None] = mapped_column(String(255))
    # PayPal-Request-Id sequence numbers. A request id is reused after an *ambiguous* failure
    # (so PayPal replays the original outcome) and advanced only after a *definitive* rejection,
    # since PayPal would otherwise replay that rejection forever.
    create_request_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    capture_request_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # True once stock has been deducted for this order; makes capture retries deduct only once.
    stock_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    line_items: Mapped[list["OrderLineItem"]] = relationship(
        back_populates="order", order_by="OrderLineItem.id", cascade="all, delete-orphan"
    )
    payouts: Mapped[list["VendorPayout"]] = relationship(
        back_populates="order", order_by="VendorPayout.vendor_id", cascade="all, delete-orphan"
    )


class OrderLineItem(Base):
    __tablename__ = "order_line_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_line_quantity_positive"),
        CheckConstraint(
            "unit_price_cents >= 0 AND platform_fee_cents >= 0 AND net_vendor_payout_cents >= 0",
            name="ck_line_amounts_non_negative",
        ),
        CheckConstraint(
            "subtotal_cents = unit_price_cents * quantity", name="ck_line_subtotal_matches"
        ),
        CheckConstraint(
            "subtotal_cents = platform_fee_cents + net_vendor_payout_cents",
            name="ck_line_amounts_balance",
        ),
        CheckConstraint(
            "commission_rate_bps >= 0 AND commission_rate_bps <= 10000", name="ck_line_bps_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("product_skus.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_query: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    net_vendor_payout_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship(back_populates="line_items")


class VendorPayout(Base):
    __tablename__ = "vendor_payouts"
    __table_args__ = (
        CheckConstraint(_in("status", PayoutStatus), name="ck_payout_status"),
        CheckConstraint(
            "gross_subtotal_cents >= 0 AND platform_fee_cents >= 0 AND payout_amount_cents >= 0",
            name="ck_payout_amounts_non_negative",
        ),
        CheckConstraint(
            "gross_subtotal_cents = platform_fee_cents + payout_amount_cents",
            name="ck_payout_amounts_balance",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    vendor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    receiver_paypal_email: Mapped[str] = mapped_column(String(320), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gross_subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # Stable across ambiguous retries (so PayPal dedupes); rotated only after a definitive
    # rejection, when no money can have moved.
    sender_batch_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    sender_item_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    paypal_payout_batch_id: Mapped[str | None] = mapped_column(String(64), index=True)
    paypal_payout_item_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PayoutStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    order: Mapped[Order] = relationship(back_populates="payouts")


class PayPalWebhookEvent(Base):
    """Every verified webhook we have processed; ``event_id`` uniqueness makes replays no-ops."""

    __tablename__ = "paypal_webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=_utcnow)
