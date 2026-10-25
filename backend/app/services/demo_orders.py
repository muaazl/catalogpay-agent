"""Pre-seeded order history for demos.

Creates one *completed* multi-vendor order so the Order history tab and the Vendor Settlement
grid are populated the moment someone opens the app. The order is priced by the exact same
``_build_order`` code path as a real checkout (catalog prices, vendor-level fee allocation,
integer cents), then marked settled with ``SIM-...`` ids in ``sandbox_simulation`` mode.

Safe to call on every startup: it does nothing if *any* order already exists, and it never
touches catalog stock (historical sales are already reflected in the seeded stock levels).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.order import Order, OrderStatus, PayoutStatus, PayPalMode
from app.schemas.order import CreateOrderItem
from app.services.order_settlement import _build_order, request_fingerprint

logger = logging.getLogger(__name__)

DEMO_ORDER_ID: Final = "ORD-DEMO00000001"
DEMO_NOTE: Final = "Elm Street Cafe weekly restock - deliver Thursday before 7am (demo history)"
DEMO_ITEMS: Final = (
    CreateOrderItem(
        sku_code="SPR-COF-ETH-1KG", quantity=3, raw_query="3x 1kg Ethiopian Yirgacheffe"
    ),
    CreateOrderItem(sku_code="SPR-OAT-BAR-6X1L", quantity=2, raw_query="2 cases Barista Oat Milk"),
    CreateOrderItem(sku_code="BPE-TMP-58MM", quantity=1, raw_query="1x 58mm calibrated tamper"),
    CreateOrderItem(sku_code="GLP-CUP-12OZ-1000", quantity=2, raw_query="2 cases 12oz hot cups"),
    CreateOrderItem(sku_code="GLP-LID-12OZ-1000", quantity=2, raw_query="2 cases 12oz sip lids"),
    CreateOrderItem(sku_code="GCB-CRS-FRZ-48", quantity=1, raw_query="1 box frozen croissants"),
    CreateOrderItem(sku_code="GCB-FLR-BRD-25KG", quantity=2, raw_query="2 x 25kg bread flour"),
)


def seed_demo_orders(session: Session, *, now: datetime | None = None) -> bool:
    """Insert the demo order if the ledger is empty. Returns True if it was created."""
    if session.scalar(select(func.count()).select_from(Order)):
        return False
    created_at = (now or datetime.now(UTC)) - timedelta(days=1)
    order = _build_order(
        session,
        DEMO_ITEMS,
        idempotency_key=f"demo-seed-{DEMO_ORDER_ID}",
        request_hash=request_fingerprint(
            DEMO_ITEMS,
            customer_note=DEMO_NOTE,
            parser_mode="deterministic_fallback",
            currency="USD",
        ),
        customer_note=DEMO_NOTE,
        parser_mode="deterministic_fallback",
        currency="USD",
        paypal_mode=PayPalMode.SANDBOX_SIMULATION,
        public_order_id=DEMO_ORDER_ID,
    )
    suffix = DEMO_ORDER_ID.removeprefix("ORD-")
    order.status = OrderStatus.COMPLETED
    order.paypal_order_id = f"SIM-ORDER-{suffix}"
    order.paypal_capture_id = f"SIM-CAPTURE-{suffix}"
    order.paypal_capture_status = "COMPLETED"
    order.approval_url = f"https://www.sandbox.paypal.com/checkoutnow?token=SIM-ORDER-{suffix}"
    order.stock_reserved = True
    order.created_at = order.updated_at = created_at
    order.captured_at = created_at + timedelta(seconds=4)
    for payout in order.payouts:
        payout.status = PayoutStatus.SUCCESS
        payout.attempt_count = 1
        payout.paypal_payout_batch_id = f"SIM-PAYOUT-{suffix}-V{payout.vendor_id}"
        payout.paypal_payout_item_id = f"SIM-ITEM-{suffix}-V{payout.vendor_id}"
        payout.created_at = payout.updated_at = created_at
    session.add(order)
    try:
        session.commit()
    except IntegrityError:  # a concurrent startup seeded it first
        session.rollback()
        return False
    logger.info("Seeded demo order %s (%s cents)", DEMO_ORDER_ID, order.gross_subtotal_cents)
    return True
