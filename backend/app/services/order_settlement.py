"""Order creation, capture, stock deduction, and multi-vendor split-payout orchestration.

State machine::

    DRAFT --create PayPal order--> PAYPAL_ORDER_CREATED --capture--> CAPTURED
    CAPTURED --payouts submitted--> PAYOUTS_PENDING --all SUCCESS--> COMPLETED
                                \\--any FAILED--> PAYOUTS_PARTIAL_FAILED --retry--> ...

Safety properties:

* Stock is deducted once per order (``Order.stock_reserved`` is claimed with a conditional
  UPDATE) and each SKU decrement is ``... WHERE stock_quantity >= :qty``, so concurrent captures
  can never oversell or drive stock negative. Stock is reserved *before* funds are captured;
  a definitive capture failure releases it, an ambiguous one (timeout) keeps it so the retry
  neither oversells nor double-deducts.
* Every PayPal call carries a stable ``PayPal-Request-Id``. Retrying after a timeout replays
  the original result instead of charging the buyer or paying a vendor twice.
* Each vendor payout's ``sender_batch_id`` is rotated only after a *definitive* rejection,
  when PayPal guarantees no money moved.
"""

import hashlib
import json
import logging
import secrets
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import DomainError
from app.models.catalog import ProductSKU
from app.models.order import (
    Order,
    OrderLineItem,
    OrderStatus,
    PayoutStatus,
    PayPalWebhookEvent,
    VendorPayout,
)
from app.schemas.order import CreateOrderItem
from app.services.money import allocate_fee, line_subtotal_cents
from app.services.paypal_client import (
    PayoutItem,
    PayPalAPIError,
    PayPalCaptureResult,
    PayPalClient,
    PayPalItem,
    PayPalNotConfiguredError,
    PayPalPayoutBatchResult,
    PayPalUnavailableError,
    parse_paypal_amount,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class OrderNotFoundError(DomainError):
    status_code, code = 404, "order_not_found"


class IdempotencyConflictError(DomainError):
    status_code, code = 409, "idempotency_conflict"


class InvalidOrderError(DomainError):
    status_code, code = 422, "invalid_order"


class InsufficientStockError(DomainError):
    status_code, code = 409, "insufficient_stock"


class OrderStateError(DomainError):
    status_code, code = 409, "invalid_order_state"


class OrderNotApprovedError(DomainError):
    status_code, code = 409, "order_not_approved"


class PaymentDeclinedError(DomainError):
    status_code, code = 402, "payment_declined"


_CAPTURED_STATES = frozenset(
    {
        OrderStatus.CAPTURED,
        OrderStatus.PAYOUTS_PENDING,
        OrderStatus.PAYOUTS_PARTIAL_FAILED,
        OrderStatus.COMPLETED,
    }
)
_PAYOUT_FINAL_FAILURE = frozenset({"DENIED", "CANCELED", "FAILED", "BLOCKED", "RETURNED"})


def _now() -> datetime:
    return datetime.now(UTC)


def _new_public_order_id() -> str:
    return f"ORD-{secrets.token_hex(6).upper()}"


def request_fingerprint(
    items: Sequence[CreateOrderItem],
    *,
    customer_note: str | None,
    parser_mode: str | None,
    currency: str,
) -> str:
    canonical = json.dumps(
        {
            "items": [[i.sku_code.upper(), i.quantity, i.raw_query] for i in items],
            "customer_note": customer_note,
            "parser_mode": parser_mode,
            "currency": currency,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_order(session: Session, public_order_id: str) -> Order:
    order = session.scalar(
        select(Order)
        .where(Order.public_order_id == public_order_id)
        .options(selectinload(Order.line_items), selectinload(Order.payouts))
    )
    if order is None:
        raise OrderNotFoundError(f"Order {public_order_id} not found.")
    return order


def _ensure_mode(order: Order, paypal: PayPalClient) -> None:
    if not paypal.is_configured:
        raise PayPalNotConfiguredError("PayPal is not configured.")
    if order.paypal_mode != paypal.mode:
        raise OrderStateError(
            f"Order {order.public_order_id} was created in {order.paypal_mode} mode but PayPal "
            f"is now running in {paypal.mode} mode."
        )


# --------------------------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------------------------


def _build_order(
    session: Session,
    items: Sequence[CreateOrderItem],
    *,
    idempotency_key: str,
    request_hash: str,
    customer_note: str | None,
    parser_mode: str | None,
    currency: str,
    paypal_mode: str,
) -> Order:
    codes = {i.sku_code.upper() for i in items}
    skus = {
        s.sku_code.upper(): s
        for s in session.scalars(
            select(ProductSKU)
            .where(ProductSKU.sku_code.in_({i.sku_code for i in items} | codes))
            .options(selectinload(ProductSKU.vendor))
        )
    }
    missing = sorted(codes - skus.keys())
    if missing:
        raise InvalidOrderError(f"Unknown SKU code(s): {', '.join(missing)}.")

    requested: defaultdict[str, int] = defaultdict(int)
    for item in items:
        requested[item.sku_code.upper()] += item.quantity
    problems: list[str] = []
    for code, qty in sorted(requested.items()):
        sku = skus[code]
        if not sku.is_active or not sku.vendor.is_active:
            raise InvalidOrderError(f"SKU {sku.sku_code} is not available for sale.")
        if sku.currency != currency:
            raise InvalidOrderError(f"SKU {sku.sku_code} is priced in {sku.currency}.")
        if sku.stock_quantity < qty:
            problems.append(f"{sku.sku_code}: requested {qty}, available {sku.stock_quantity}")
    if problems:
        raise InsufficientStockError("Insufficient stock - " + "; ".join(problems))

    lines: list[OrderLineItem] = []
    for item in items:
        sku = skus[item.sku_code.upper()]
        lines.append(
            OrderLineItem(
                sku_id=sku.id,
                vendor_id=sku.vendor_id,
                sku_code=sku.sku_code,
                product_name=sku.name,
                raw_query=item.raw_query,
                quantity=item.quantity,
                unit_price_cents=sku.unit_price_cents,
                subtotal_cents=line_subtotal_cents(sku.unit_price_cents, item.quantity),
                commission_rate_bps=sku.vendor.commission_rate_bps,
                platform_fee_cents=0,  # set below once the vendor-level fee is apportioned
                net_vendor_payout_cents=0,
            )
        )
    public_order_id = _new_public_order_id()
    by_vendor: defaultdict[int, list[OrderLineItem]] = defaultdict(list)
    for line in lines:
        by_vendor[line.vendor_id].append(line)

    payouts: list[VendorPayout] = []
    vendors = {s.vendor_id: s.vendor for s in skus.values()}
    for vendor_id in sorted(by_vendor):
        vendor_lines = by_vendor[vendor_id]
        vendor = vendors[vendor_id]
        # Same rule as the preview: fee charged once per vendor, apportioned to lines.
        fees = allocate_fee([ln.subtotal_cents for ln in vendor_lines], vendor.commission_rate_bps)
        for line, fee in zip(vendor_lines, fees, strict=True):
            line.platform_fee_cents = fee
            line.net_vendor_payout_cents = line.subtotal_cents - fee
        gross = sum(ln.subtotal_cents for ln in vendor_lines)
        fee_total = sum(fees)
        payouts.append(
            VendorPayout(
                vendor_id=vendor_id,
                vendor_name=vendor.name,
                receiver_paypal_email=vendor.paypal_merchant_email,
                currency=currency,
                gross_subtotal_cents=gross,
                platform_fee_cents=fee_total,
                payout_amount_cents=gross - fee_total,
                sender_batch_id=f"{public_order_id}-V{vendor_id}",
                sender_item_id=f"{public_order_id}-V{vendor_id}-ITEM",
                status=PayoutStatus.PENDING,
            )
        )

    gross = sum(ln.subtotal_cents for ln in lines)
    fee_total = sum(p.platform_fee_cents for p in payouts)
    return Order(
        public_order_id=public_order_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        status=OrderStatus.DRAFT,
        currency=currency,
        gross_subtotal_cents=gross,
        total_platform_fee_cents=fee_total,
        total_vendor_payout_cents=gross - fee_total,
        customer_note=customer_note,
        parser_mode=parser_mode,
        paypal_mode=paypal_mode,
        line_items=lines,
        payouts=payouts,
    )


async def _open_paypal_order(session: Session, paypal: PayPalClient, order: Order) -> None:
    try:
        result = await paypal.create_paypal_order(
            reference_id=order.public_order_id,
            items=[
                PayPalItem(
                    name=ln.product_name,
                    sku=ln.sku_code,
                    quantity=ln.quantity,
                    unit_amount_cents=ln.unit_price_cents,
                )
                for ln in order.line_items
            ],
            currency=order.currency,
            request_id=f"{order.public_order_id}-create-{order.create_request_seq}",
            description=f"CatalogPay order {order.public_order_id}",
        )
    except DomainError as exc:
        order.last_error = exc.message
        if isinstance(exc, PayPalAPIError):  # definitive: next attempt needs a fresh request id
            order.create_request_seq += 1
        session.commit()
        raise
    order.paypal_order_id = result.order_id
    order.approval_url = result.approval_url
    order.status = OrderStatus.PAYPAL_ORDER_CREATED
    order.last_error = None
    session.commit()


async def create_order_from_items(
    session: Session,
    paypal: PayPalClient,
    *,
    items: Sequence[CreateOrderItem],
    idempotency_key: str,
    customer_note: str | None = None,
    parser_mode: str | None = None,
    currency: str = "USD",
) -> tuple[Order, bool]:
    """Create (or idempotently return) an order. Returns ``(order, created)``."""
    if not paypal.is_configured:
        raise PayPalNotConfiguredError(
            "PayPal is not configured and PAYPAL_SANDBOX_MOCK_FALLBACK is disabled."
        )
    if not items:
        raise InvalidOrderError("An order needs at least one item.")
    fingerprint = request_fingerprint(
        items, customer_note=customer_note, parser_mode=parser_mode, currency=currency
    )

    def _replay(existing: Order) -> Order:
        if existing.request_hash != fingerprint:
            raise IdempotencyConflictError(
                "This Idempotency-Key was already used with a different order payload."
            )
        return existing

    existing = session.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
    if existing is not None:
        order = _replay(existing)
        if order.status == OrderStatus.DRAFT:  # earlier attempt failed before PayPal answered
            await _open_paypal_order(session, paypal, order)
        return load_order(session, order.public_order_id), False

    order = _build_order(
        session,
        items,
        idempotency_key=idempotency_key,
        request_hash=fingerprint,
        customer_note=customer_note,
        parser_mode=parser_mode,
        currency=currency,
        paypal_mode=str(paypal.mode),
    )
    session.add(order)
    try:
        session.commit()
    except IntegrityError:  # concurrent request with the same key won the insert
        session.rollback()
        winner = session.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if winner is None:
            raise
        return load_order(session, _replay(winner).public_order_id), False

    await _open_paypal_order(session, paypal, order)
    return load_order(session, order.public_order_id), True


# --------------------------------------------------------------------------------------------
# Stock
# --------------------------------------------------------------------------------------------


def _sku_quantities(order: Order) -> dict[int, int]:
    totals: defaultdict[int, int] = defaultdict(int)
    for line in order.line_items:
        totals[line.sku_id] += line.quantity
    return dict(sorted(totals.items()))


def reserve_stock(session: Session, order: Order) -> bool:
    """Deduct stock for ``order`` exactly once. Returns False if it was already reserved."""
    claimed = session.execute(
        update(Order)
        .where(Order.id == order.id, Order.stock_reserved.is_(False))
        .values(stock_reserved=True)
        .execution_options(synchronize_session=False)
    ).rowcount
    if claimed != 1:
        session.rollback()
        session.refresh(order)
        return False
    for sku_id, qty in _sku_quantities(order).items():
        result = session.execute(
            update(ProductSKU)
            .where(ProductSKU.id == sku_id, ProductSKU.stock_quantity >= qty)
            .values(stock_quantity=ProductSKU.stock_quantity - qty)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            sku = session.get(ProductSKU, sku_id)
            available = sku.stock_quantity if sku else 0
            code = sku.sku_code if sku else str(sku_id)
            raise InsufficientStockError(
                f"Insufficient stock for {code}: need {qty}, available {available}. "
                "No payment was captured."
            )
    session.commit()
    session.refresh(order)
    return True


def release_stock(session: Session, order: Order) -> None:
    released = session.execute(
        update(Order)
        .where(Order.id == order.id, Order.stock_reserved.is_(True))
        .values(stock_reserved=False)
        .execution_options(synchronize_session=False)
    ).rowcount
    if released == 1:
        for sku_id, qty in _sku_quantities(order).items():
            session.execute(
                update(ProductSKU)
                .where(ProductSKU.id == sku_id)
                .values(stock_quantity=ProductSKU.stock_quantity + qty)
                .execution_options(synchronize_session=False)
            )
    session.commit()
    session.refresh(order)


# --------------------------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------------------------


def _record_capture(
    session: Session,
    order: Order,
    *,
    capture_id: str | None,
    capture_status: str | None,
    amount_cents: int | None,
) -> None:
    order.paypal_capture_id = capture_id or order.paypal_capture_id
    order.paypal_capture_status = capture_status
    order.captured_at = order.captured_at or _now()
    order.status = OrderStatus.CAPTURED
    order.last_error = None
    if amount_cents is not None and amount_cents != order.gross_subtotal_cents:
        order.payout_hold_reason = (
            f"Captured {amount_cents} cents but ledger total is {order.gross_subtotal_cents}."
        )
        logger.error("Capture amount mismatch on %s", order.public_order_id)
    session.commit()


async def _capture_with_recovery(paypal: PayPalClient, order: Order) -> PayPalCaptureResult:
    assert order.paypal_order_id is not None
    try:
        return await paypal.capture_paypal_order(
            order.paypal_order_id,
            request_id=f"{order.public_order_id}-capture-{order.capture_request_seq}",
        )
    except PayPalAPIError as exc:
        if exc.has_issue("ORDER_ALREADY_CAPTURED"):
            # Captured earlier (e.g. our DB write was lost): read the capture back instead.
            return await paypal.get_paypal_order(order.paypal_order_id)
        raise


async def capture_order_and_disburse(
    session: Session, paypal: PayPalClient, public_order_id: str
) -> Order:
    order = load_order(session, public_order_id)
    _ensure_mode(order, paypal)

    if order.status in _CAPTURED_STATES:
        if order.status == OrderStatus.CAPTURED:  # crashed between capture and payouts
            await disburse_payouts(session, paypal, order)
        return load_order(session, public_order_id)
    if order.status != OrderStatus.PAYPAL_ORDER_CREATED:
        raise OrderStateError(f"Order {public_order_id} cannot be captured in {order.status}.")

    reserve_stock(session, order)  # raises InsufficientStockError before any money moves
    try:
        capture = await _capture_with_recovery(paypal, order)
    except PayPalAPIError as exc:
        release_stock(session, order)  # definitive: PayPal captured nothing
        order.last_error = exc.message
        order.capture_request_seq += 1  # a replay of this id would repeat the rejection
        session.commit()
        if exc.has_issue("ORDER_NOT_APPROVED", "PAYER_ACTION_REQUIRED"):
            raise OrderNotApprovedError(
                "The buyer has not approved this PayPal order yet; send them to approval_url."
            ) from exc
        if exc.has_issue("INSTRUMENT_DECLINED", "PAYER_CANNOT_PAY", "TRANSACTION_REFUSED"):
            raise PaymentDeclinedError("The buyer's payment method was declined.") from exc
        raise
    except PayPalUnavailableError as exc:
        # Ambiguous: PayPal may have captured. Keep the stock reservation; retrying capture
        # replays the same PayPal-Request-Id and cannot double-charge or double-deduct.
        order.last_error = exc.message
        session.commit()
        raise

    _record_capture(
        session,
        order,
        capture_id=capture.capture_id,
        capture_status=capture.capture_status,
        amount_cents=capture.amount_cents,
    )
    await disburse_payouts(session, paypal, order)
    return load_order(session, public_order_id)


# --------------------------------------------------------------------------------------------
# Payouts
# --------------------------------------------------------------------------------------------


def _mark_payout_failed(payout: VendorPayout, message: str, *, definitive: bool) -> None:
    payout.status = PayoutStatus.FAILED
    payout.error_message = message[:2000]
    if definitive:
        # Nothing was paid, so a fresh id is safe and avoids PayPal's duplicate-id rejection.
        base = payout.sender_batch_id.split("-R", 1)[0]
        payout.sender_batch_id = f"{base}-R{payout.attempt_count}"
        payout.paypal_payout_batch_id = None


def _apply_batch_result(payout: VendorPayout, batch: PayPalPayoutBatchResult) -> None:
    payout.paypal_payout_batch_id = batch.payout_batch_id
    item = next((i for i in batch.items if i.sender_item_id in (None, payout.sender_item_id)), None)
    if item and item.payout_item_id:
        payout.paypal_payout_item_id = item.payout_item_id
    item_status = (item.transaction_status or "") if item else ""
    if batch.batch_status in _PAYOUT_FINAL_FAILURE or item_status in _PAYOUT_FINAL_FAILURE:
        _mark_payout_failed(
            payout, f"PayPal payout {batch.batch_status}/{item_status}", definitive=True
        )
    elif batch.batch_status == "SUCCESS" and item_status in ("", "SUCCESS"):
        payout.status = PayoutStatus.SUCCESS
        payout.error_message = None
    else:
        payout.status = PayoutStatus.PROCESSING
        payout.error_message = None


def refresh_order_status(order: Order) -> None:
    if order.status not in _CAPTURED_STATES:
        return
    statuses = {p.status for p in order.payouts}
    if statuses <= {PayoutStatus.SUCCESS}:
        order.status = OrderStatus.COMPLETED
    elif PayoutStatus.FAILED in statuses:
        order.status = OrderStatus.PAYOUTS_PARTIAL_FAILED
    elif statuses & {PayoutStatus.PROCESSING, PayoutStatus.SUCCESS}:
        order.status = OrderStatus.PAYOUTS_PENDING
    else:
        order.status = OrderStatus.CAPTURED


async def _submit_payout(
    session: Session, paypal: PayPalClient, order: Order, payout: VendorPayout
) -> None:
    claimed = session.execute(
        update(VendorPayout)
        .where(
            VendorPayout.id == payout.id,
            VendorPayout.status.in_([PayoutStatus.PENDING, PayoutStatus.FAILED]),
        )
        .values(status=PayoutStatus.PROCESSING, attempt_count=VendorPayout.attempt_count + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    session.refresh(payout)
    if claimed != 1:
        return  # another worker owns this payout
    try:
        batch = await paypal.create_payout_batch(
            sender_batch_id=payout.sender_batch_id,
            items=[
                PayoutItem(
                    receiver_email=payout.receiver_paypal_email,
                    amount_cents=payout.payout_amount_cents,
                    sender_item_id=payout.sender_item_id,
                    note=f"CatalogPay settlement for order {order.public_order_id}",
                )
            ],
            currency=payout.currency,
        )
    except PayPalUnavailableError as exc:
        _mark_payout_failed(payout, f"Ambiguous: {exc.message}", definitive=False)
    except PayPalAPIError as exc:
        # A duplicate-id rejection means an earlier attempt *did* create the batch.
        definitive = not exc.has_issue("DUPLICATE_REQUEST_ID", "SENDER_BATCH_ID_ALREADY_USED")
        _mark_payout_failed(payout, exc.message, definitive=definitive)
    else:
        _apply_batch_result(payout, batch)
    session.commit()


async def _poll_payout(paypal: PayPalClient, payout: VendorPayout) -> None:
    if not payout.paypal_payout_batch_id:
        return
    try:
        batch = await paypal.get_payout_batch(payout.paypal_payout_batch_id)
    except (PayPalUnavailableError, PayPalAPIError) as exc:
        logger.warning("Could not poll payout %s: %s", payout.sender_batch_id, exc.message)
        return
    _apply_batch_result(payout, batch)


async def disburse_payouts(
    session: Session, paypal: PayPalClient, order: Order, *, poll_processing: bool = False
) -> None:
    if order.status not in _CAPTURED_STATES or order.status == OrderStatus.COMPLETED:
        return
    if order.payout_hold_reason or order.paypal_capture_status not in (None, "COMPLETED"):
        refresh_order_status(order)
        session.commit()
        return
    for payout in order.payouts:
        if payout.status == PayoutStatus.SUCCESS:
            continue
        if payout.payout_amount_cents == 0:  # nothing owed (e.g. 100% commission)
            payout.status = PayoutStatus.SUCCESS
            session.commit()
            continue
        if payout.status == PayoutStatus.PROCESSING:
            if poll_processing:
                await _poll_payout(paypal, payout)
                session.commit()
            continue
        await _submit_payout(session, paypal, order, payout)
    session.refresh(order)
    refresh_order_status(order)
    session.commit()


async def retry_payouts(session: Session, paypal: PayPalClient, public_order_id: str) -> Order:
    order = load_order(session, public_order_id)
    _ensure_mode(order, paypal)
    if order.status not in _CAPTURED_STATES:
        raise OrderStateError(
            f"Order {public_order_id} has not been captured ({order.status}); nothing to pay out."
        )
    if order.status != OrderStatus.COMPLETED:
        await disburse_payouts(session, paypal, order, poll_processing=True)
    return load_order(session, public_order_id)


# --------------------------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------------------------


async def _on_capture_completed(
    session: Session, paypal: PayPalClient, resource: dict[str, Any]
) -> str:
    related = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    paypal_order_id = related.get("order_id")
    order = session.scalar(select(Order).where(Order.paypal_order_id == paypal_order_id))
    if order is None:
        return f"ignored: no order for PayPal order {paypal_order_id}"
    order = load_order(session, order.public_order_id)
    amount = (resource.get("amount") or {}).get("value")
    amount_cents = parse_paypal_amount(amount) if isinstance(amount, str) else None

    if order.status == OrderStatus.PAYPAL_ORDER_CREATED:
        # Captured outside our capture endpoint: adopt it (deduct stock, then pay vendors).
        try:
            reserve_stock(session, order)
        except InsufficientStockError as exc:
            order.payout_hold_reason = "Captured externally but stock was insufficient."
            order.last_error = exc.message
        _record_capture(
            session,
            order,
            capture_id=resource.get("id"),
            capture_status="COMPLETED",
            amount_cents=amount_cents,
        )
        await disburse_payouts(session, paypal, order)
        return f"captured {order.public_order_id} from webhook"
    if order.status in _CAPTURED_STATES and order.paypal_capture_status != "COMPLETED":
        order.paypal_capture_status = "COMPLETED"  # pending capture has now cleared
        session.commit()
        await disburse_payouts(session, paypal, order)
        return f"capture cleared for {order.public_order_id}"
    return f"no-op: {order.public_order_id} already {order.status}"


def _on_payout_batch(session: Session, resource: dict[str, Any], *, success: bool) -> str:
    header = resource.get("batch_header") or {}
    batch_id = header.get("payout_batch_id")
    payouts = session.scalars(
        select(VendorPayout).where(VendorPayout.paypal_payout_batch_id == batch_id)
    ).all()
    if not payouts:
        return f"ignored: unknown payout batch {batch_id}"
    for payout in payouts:
        if payout.status == PayoutStatus.SUCCESS:
            continue
        if success:
            payout.status = PayoutStatus.SUCCESS
            payout.error_message = None
        else:
            _mark_payout_failed(
                payout, f"PayPal batch {header.get('batch_status', 'DENIED')}", definitive=True
            )
    orders = {p.order_id for p in payouts}
    session.flush()
    for order_id in orders:
        order = session.get(Order, order_id)
        if order is not None:
            session.refresh(order)
            refresh_order_status(order)
    return f"{'settled' if success else 'denied'} batch {batch_id}"


def _on_payout_item(session: Session, resource: dict[str, Any], event_type: str) -> str:
    sender_item_id = (resource.get("payout_item") or {}).get("sender_item_id")
    payout = session.scalar(
        select(VendorPayout).where(VendorPayout.sender_item_id == sender_item_id)
    )
    if payout is None:
        return f"ignored: unknown payout item {sender_item_id}"
    if event_type.endswith("SUCCEEDED"):
        payout.status = PayoutStatus.SUCCESS
        payout.error_message = None
    elif payout.status != PayoutStatus.SUCCESS:
        _mark_payout_failed(payout, f"PayPal item {event_type}", definitive=True)
    session.flush()
    order = session.get(Order, payout.order_id)
    if order is not None:
        session.refresh(order)
        refresh_order_status(order)
    return f"payout item {sender_item_id} -> {payout.status}"


async def handle_paypal_webhook(
    session: Session, paypal: PayPalClient, event: dict[str, Any]
) -> tuple[str, str]:
    """Apply a *verified* webhook event. Returns ``(status, detail)``; replays are no-ops."""
    event_id = str(event.get("id") or "")
    event_type = str(event.get("event_type") or "")
    resource = event.get("resource") if isinstance(event.get("resource"), dict) else {}
    if not event_id or not event_type:
        raise InvalidOrderError("Webhook event is missing id or event_type.")
    if session.scalar(select(PayPalWebhookEvent).where(PayPalWebhookEvent.event_id == event_id)):
        return "duplicate", "event already processed"

    status = "processed"
    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        detail = await _on_capture_completed(session, paypal, resource)
    elif event_type == "PAYMENT.PAYOUTS-BATCH.SUCCESS":
        detail = _on_payout_batch(session, resource, success=True)
    elif event_type == "PAYMENT.PAYOUTS-BATCH.DENIED":
        detail = _on_payout_batch(session, resource, success=False)
    elif event_type.startswith("PAYMENT.PAYOUTSITEM."):
        if event_type in ("PAYMENT.PAYOUTSITEM.UNCLAIMED", "PAYMENT.PAYOUTSITEM.HELD"):
            status, detail = "ignored", f"{event_type} needs no ledger change"
        else:
            detail = _on_payout_item(session, resource, event_type)
    else:
        status, detail = "ignored", f"unhandled event type {event_type}"
    if detail.startswith("ignored"):
        status = "ignored"

    session.add(
        PayPalWebhookEvent(
            event_id=event_id,
            event_type=event_type,
            resource_id=str(resource.get("id") or "")[:128] or None,
            outcome=detail[:255],
        )
    )
    try:
        session.commit()
    except IntegrityError:  # concurrent delivery of the same event
        session.rollback()
        return "duplicate", "event already processed"
    return status, detail
