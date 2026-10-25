// Money arrives from the API as integer cents and is never turned into a float here.
// Cents are split with integer arithmetic and handed to Intl.NumberFormat as an exact decimal
// *string* ("1234.05"), which Intl formats without binary floating-point rounding.

const formatters = new Map<string, Intl.NumberFormat>();

function formatterFor(currency: string, locale: string): Intl.NumberFormat {
  const key = `${locale}|${currency}`;
  let formatter = formatters.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    formatters.set(key, formatter);
  }
  return formatter;
}

function assertCents(cents: number): void {
  if (!Number.isSafeInteger(cents)) {
    throw new TypeError(`Expected integer cents, got ${String(cents)}`);
  }
}

/** Exact decimal string for integer cents: 1050 -> "10.50", -7 -> "-0.07". */
export function centsToDecimalString(cents: number): string {
  assertCents(cents);
  const negative = cents < 0;
  const abs = Math.abs(cents);
  const fraction = abs % 100;
  const whole = (abs - fraction) / 100; // exact: abs - fraction is a multiple of 100
  return `${negative ? "-" : ""}${whole}.${String(fraction).padStart(2, "0")}`;
}

/** Integer cents -> localized currency string, e.g. formatCents(1250) === "$12.50". */
export function formatCents(cents: number, currency = "USD", locale = "en-US"): string {
  const exact = centsToDecimalString(cents) as Intl.StringNumericLiteral;
  return formatterFor(currency, locale).format(exact);
}

/** Basis points -> percent with two decimals using integer math: 650 -> "6.50%". */
export function formatBps(bps: number): string {
  assertCents(bps);
  const abs = Math.abs(bps);
  const fraction = abs % 100;
  return `${bps < 0 ? "-" : ""}${(abs - fraction) / 100}.${String(fraction).padStart(2, "0")}%`;
}

/** Sum integer cents, refusing anything that is not a safe integer. */
export function sumCents(values: readonly number[]): number {
  let total = 0;
  for (const v of values) {
    assertCents(v);
    total += v;
  }
  assertCents(total);
  return total;
}
