import "../lib/agGrid";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SkuResolutionGrid } from "../components/SkuResolutionGrid";
import { confidenceTone, lineHealth, toResolutionRows, totalsRow } from "../utils/resolution";
import { samplePreview } from "./fixtures";

function renderGrid(overrides: Partial<Parameters<typeof SkuResolutionGrid>[0]> = {}) {
  const props = {
    preview: samplePreview(),
    locked: false,
    busy: false,
    onQuantityChange: vi.fn(),
    onPinSku: vi.fn(),
    ...overrides,
  };
  render(<SkuResolutionGrid {...props} />);
  return props;
}

describe("resolution row model", () => {
  it("classifies lines and confidence tones", () => {
    const [arabica, tamper, unicorn] = samplePreview().lines;
    expect(lineHealth(arabica!)).toBe("ready");
    expect(confidenceTone(arabica!)).toBe("green"); // 0.86
    expect(confidenceTone(tamper!)).toBe("amber"); // 0.65
    expect(lineHealth(unicorn!)).toBe("unmatched");
    expect(confidenceTone(unicorn!)).toBe("red");
    const outOfStock = { ...arabica!, stock_status: "insufficient_stock" as const, is_billable: false };
    expect(confidenceTone(outOfStock)).toBe("red"); // high score, but cannot ship
  });

  it("builds rows and a totals row from integer cents", () => {
    const preview = samplePreview();
    const rows = toResolutionRows(preview);
    expect(rows.map((r) => r.skuCode)).toEqual(["SPR-COF-ARB-500G", "BPE-TMP-58MM", null]);
    const [total] = totalsRow(preview);
    expect(total).toMatchObject({ isTotal: true, subtotalCents: 13978, platformFeeCents: 939, netPayoutCents: 13039 });
    expect(total!.subtotalCents).toBe(total!.platformFeeCents + total!.netPayoutCents);
  });
});

describe("SkuResolutionGrid", () => {
  it("renders fragments, matched SKUs, confidence, vendors and economics", async () => {
    renderGrid();
    const grid = await screen.findByTestId("resolution-grid");
    await within(grid).findByText("arabica beans half kg");
    expect(within(grid).getByText("espresso tamper 58mm")).toBeInTheDocument();
    expect(within(grid).getByText("unicorn saddles")).toBeInTheDocument();

    expect(within(grid).getByLabelText('SKU for "arabica beans half kg"')).toHaveValue("SPR-COF-ARB-500G");
    expect(within(grid).getByTestId("confidence-0")).toHaveAttribute("data-tone", "green");
    expect(within(grid).getByTestId("confidence-1")).toHaveAttribute("data-tone", "amber");
    expect(within(grid).getByTestId("confidence-2")).toHaveAttribute("data-tone", "red");

    expect(within(grid).getByText("Summit Peak Roasters")).toBeInTheDocument();
    expect(within(grid).getByText("5.00%")).toBeInTheDocument();
    // The tamper line and the unmatched line's best guess are both BaristaPro (8%).
    expect(within(grid).getAllByText("8.00%")).toHaveLength(2);
    for (const amount of ["$59.80", "$2.99", "$56.81", "$79.98", "$6.40", "$73.58"]) {
      expect(within(grid).getByText(amount)).toBeInTheDocument();
    }
    // Pinned totals row.
    expect(within(grid).getByText(/Order totals · 2\/3 lines billable/)).toBeInTheDocument();
    for (const amount of ["$139.78", "$9.39", "$130.39"]) {
      expect(within(grid).getByText(amount)).toBeInTheDocument();
    }
    expect(within(grid).getByText("Unmatched")).toBeInTheDocument();
    expect(screen.getByText("2/3 lines billable")).toBeInTheDocument();
  });

  it("switching a candidate pins that SKU for the line", async () => {
    const props = renderGrid();
    const select = await screen.findByLabelText('SKU for "arabica beans half kg"');
    await userEvent.selectOptions(select, "SPR-COF-ARB-1KG");
    expect(props.onPinSku).toHaveBeenCalledWith(0, "SPR-COF-ARB-1KG");
  });

  it("offers a 'Choose a SKU' prompt for unresolved lines", async () => {
    renderGrid();
    const select = await screen.findByLabelText('SKU for "unicorn saddles"');
    expect(select).toHaveValue("");
    expect(within(select).getByRole("option", { name: "Choose a SKU…" })).toBeInTheDocument();
  });

  it("locks candidate switching once an order exists", async () => {
    renderGrid({ locked: true, lockedOrderId: "ORD-1A2B3C4D5E6F" });
    expect(await screen.findByLabelText('SKU for "arabica beans half kg"')).toBeDisabled();
    expect(screen.getByText(/Locked to ORD-1A2B3C4D5E6F/)).toBeInTheDocument();
  });

  it("shows an empty state and disables CSV export without a preview", () => {
    renderGrid({ preview: null });
    expect(screen.getByText("No order resolved yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Export CSV/ })).toBeDisabled();
  });

  it("exports CSV with exact integer cents", async () => {
    const created: Blob[] = [];
    const createObjectURL = vi.fn((blob: Blob) => {
      created.push(blob);
      return "blob:csv";
    });
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    // jsdom rejects AG Grid's `new MouseEvent("click", { view: window })` under Vitest; drop
    // `view` so the real exportDataAsCsv download path still runs end to end.
    const RealMouseEvent = window.MouseEvent;
    vi.stubGlobal(
      "MouseEvent",
      class extends RealMouseEvent {
        constructor(type: string, init: MouseEventInit = {}) {
          const { view: _view, ...rest } = init;
          super(type, rest);
        }
      },
    );
    renderGrid();
    await screen.findByText("arabica beans half kg");
    await userEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    await waitFor(() => expect(created).toHaveLength(1));
    const csv = await created[0]!.text();
    expect(csv).toContain('"Line subtotal (cents)"');
    expect(csv).toContain('"5980"'); // raw cents, not "$59.80"
    expect(csv).toContain('"13978"'); // pinned totals row
    expect(csv).not.toContain("$59.80");
  });
});
