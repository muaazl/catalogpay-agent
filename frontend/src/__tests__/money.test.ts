import { describe, expect, it } from "vitest";

import { centsToDecimalString, formatBps, formatCents, sumCents } from "../utils/money";

describe("formatCents", () => {
  it.each([
    [0, "$0.00"],
    [1, "$0.01"],
    [99, "$0.99"],
    [100, "$1.00"],
    [1050, "$10.50"],
    [1250, "$12.50"],
    [13978, "$139.78"],
    [9_999_999, "$99,999.99"],
    [-7, "-$0.07"],
    [-1050, "-$10.50"],
  ])("formats %i cents as %s", (cents, expected) => {
    expect(formatCents(cents)).toBe(expected);
  });

  it("is exact where float division would drift", () => {
    // 0.1 + 0.2 style traps: these cent values are not exactly representable as dollars.
    for (const cents of [1, 3, 7, 29, 57, 101, 1005, 10_000_005, 123_456_789]) {
      const [whole, frac] = centsToDecimalString(cents).split(".");
      expect(Number(whole) * 100 + Number(frac)).toBe(cents);
    }
    expect(formatCents(Number.MAX_SAFE_INTEGER)).toBe("$90,071,992,547,409.91");
  });

  it("honours other currencies and locales", () => {
    expect(formatCents(1250, "EUR", "de-DE")).toBe("12,50 €");
    expect(formatCents(1250, "GBP", "en-GB")).toBe("£12.50");
  });

  it.each([1.5, 0.1, Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53])(
    "rejects non-integer cents %s",
    (bad) => {
      expect(() => formatCents(bad)).toThrow(TypeError);
    },
  );
});

describe("centsToDecimalString", () => {
  it.each([
    [0, "0.00"],
    [5, "0.05"],
    [1050, "10.50"],
    [-99, "-0.99"],
  ])("%i -> %s", (cents, expected) => {
    expect(centsToDecimalString(cents)).toBe(expected);
  });
});

describe("formatBps", () => {
  it.each([
    [0, "0.00%"],
    [450, "4.50%"],
    [500, "5.00%"],
    [650, "6.50%"],
    [10_000, "100.00%"],
    [1, "0.01%"],
  ])("%i bps -> %s", (bps, expected) => {
    expect(formatBps(bps)).toBe(expected);
  });
});

describe("sumCents", () => {
  it("sums integers and refuses floats", () => {
    expect(sumCents([299, 640, 897])).toBe(1836);
    expect(() => sumCents([1, 0.5])).toThrow(TypeError);
  });
});
