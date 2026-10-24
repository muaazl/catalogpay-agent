"""Integer-only money arithmetic. Floats never touch a monetary amount."""

from collections.abc import Sequence
from dataclasses import dataclass

BPS_DENOMINATOR = 10_000


@dataclass(frozen=True, slots=True)
class FeeSplit:
    subtotal_cents: int
    platform_fee_cents: int
    net_vendor_payout_cents: int


def require_int(name: str, value: object) -> int:
    # ``type(...) is int`` on purpose: bool subclasses int, and floats/Decimals/strings must
    # never silently become money.
    if type(value) is not int:
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    return value


def line_subtotal_cents(unit_price_cents: int, quantity: int) -> int:
    require_int("unit_price_cents", unit_price_cents)
    require_int("quantity", quantity)
    if unit_price_cents < 0 or quantity < 0:
        raise ValueError("unit_price_cents and quantity must be non-negative")
    return unit_price_cents * quantity


def platform_fee_cents(subtotal_cents: int, commission_rate_bps: int) -> int:
    """Commission on ``subtotal_cents`` at ``commission_rate_bps``, rounded half-up to a cent."""
    require_int("subtotal_cents", subtotal_cents)
    require_int("commission_rate_bps", commission_rate_bps)
    if subtotal_cents < 0:
        raise ValueError("subtotal_cents must be non-negative")
    if not 0 <= commission_rate_bps <= BPS_DENOMINATOR:
        raise ValueError("commission_rate_bps must be between 0 and 10000")
    return (subtotal_cents * commission_rate_bps + BPS_DENOMINATOR // 2) // BPS_DENOMINATOR


def allocate_fee(subtotals_cents: Sequence[int], commission_rate_bps: int) -> list[int]:
    """Charge the fee once on the combined subtotal, then apportion it to the parts.

    Uses the largest-remainder method (ties go to the earlier part), so the parts always sum to
    ``platform_fee_cents(sum(subtotals), bps)``. Splitting or merging lines therefore never
    changes the total fee.
    """
    for i, subtotal in enumerate(subtotals_cents):
        require_int(f"subtotals_cents[{i}]", subtotal)
        if subtotal < 0:
            raise ValueError("subtotals must be non-negative")
    total_fee = platform_fee_cents(sum(subtotals_cents), commission_rate_bps)
    shares = [divmod(s * commission_rate_bps, BPS_DENOMINATOR) for s in subtotals_cents]
    fees = [whole for whole, _ in shares]
    leftover = total_fee - sum(fees)
    by_remainder = sorted(range(len(shares)), key=lambda i: (-shares[i][1], i))
    for i in by_remainder[:leftover]:
        fees[i] += 1
    return fees


def split_fee(subtotal_cents: int, commission_rate_bps: int) -> FeeSplit:
    """Split a subtotal into platform fee + vendor net. Net is derived, so the parts always sum."""
    fee = platform_fee_cents(subtotal_cents, commission_rate_bps)
    return FeeSplit(
        subtotal_cents=subtotal_cents,
        platform_fee_cents=fee,
        net_vendor_payout_cents=subtotal_cents - fee,
    )
