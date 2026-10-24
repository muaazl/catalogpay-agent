"""Integer-only money arithmetic. Floats never touch a monetary amount."""

from dataclasses import dataclass

BPS_DENOMINATOR = 10_000


@dataclass(frozen=True, slots=True)
class FeeSplit:
    subtotal_cents: int
    platform_fee_cents: int
    net_vendor_payout_cents: int


def line_subtotal_cents(unit_price_cents: int, quantity: int) -> int:
    if unit_price_cents < 0 or quantity < 0:
        raise ValueError("unit_price_cents and quantity must be non-negative")
    return unit_price_cents * quantity


def platform_fee_cents(subtotal_cents: int, commission_rate_bps: int) -> int:
    """Commission on ``subtotal_cents`` at ``commission_rate_bps``, rounded half-up to a cent."""
    if subtotal_cents < 0:
        raise ValueError("subtotal_cents must be non-negative")
    if not 0 <= commission_rate_bps <= BPS_DENOMINATOR:
        raise ValueError("commission_rate_bps must be between 0 and 10000")
    return (subtotal_cents * commission_rate_bps + BPS_DENOMINATOR // 2) // BPS_DENOMINATOR


def split_fee(subtotal_cents: int, commission_rate_bps: int) -> FeeSplit:
    """Split a subtotal into platform fee + vendor net. Net is derived, so the parts always sum."""
    fee = platform_fee_cents(subtotal_cents, commission_rate_bps)
    return FeeSplit(
        subtotal_cents=subtotal_cents,
        platform_fee_cents=fee,
        net_vendor_payout_cents=subtotal_cents - fee,
    )
