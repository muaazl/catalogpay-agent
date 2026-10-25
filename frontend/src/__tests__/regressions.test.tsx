// Feature 4 audit regressions: async races, history loading, invalid edits, ledger currency,
// client-side upload guards and approval-URL safety.
import "../lib/agGrid";

import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../api/client";
import { ApiError } from "../api/client";
import { ApiProvider } from "../api/context";
import type { MatchPreviewResponse, OrderDetail, ParsedOrderPreviewResponse } from "../api/types";
import App from "../App";
import { OrderIntakePanel } from "../components/OrderIntakePanel";
import { PayPalSettlementPanel, safeHttpUrl } from "../components/PayPalSettlementPanel";
import { SkuResolutionGrid } from "../components/SkuResolutionGrid";
import { toSplitRows, VendorSettlementGrid } from "../components/VendorSettlementGrid";
import { previewFromOrder, quantityError, useCommandCenter } from "../hooks/useCommandCenter";
import { fakeClient, sampleOrder, sampleParse, samplePreview } from "./fixtures";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderApp(client: ApiClient) {
  render(
    <ApiProvider client={client}>
      <App animationStepMs={0} />
    </ApiProvider>,
  );
}

function previewWithQuery(query: string): MatchPreviewResponse {
  const preview = samplePreview();
  preview.lines[0] = { ...preview.lines[0]!, query };
  return preview;
}

const createButton = () => screen.getByRole("button", { name: /Create PayPal Order/ });

async function loadPreset(name: RegExp, firstQuery = "arabica beans half kg") {
  await userEvent.click(screen.getByRole("button", { name }));
  await screen.findByLabelText(`SKU for "${firstQuery}"`);
}

function historicalOrder(): OrderDetail {
  const order = sampleOrder("COMPLETED", ["SUCCESS", "SUCCESS"]);
  order.public_order_id = "ORD-DEMO00000001";
  order.line_items = order.line_items.map((l, i) => ({ ...l, raw_query: `historic fragment ${i + 1}` }));
  return order;
}

// ---------------------------------------------------------------------------------------------
// F4-01: one request sequence for every action; checkout locked while anything is in flight
// ---------------------------------------------------------------------------------------------

describe("F4-01 async races & checkout lock", () => {
  it("disables Create PayPal Order while a re-price is in flight", async () => {
    const pending = deferred<MatchPreviewResponse>();
    const client = fakeClient({ matchPreview: vi.fn().mockReturnValue(pending.promise) });
    renderApp(client);
    await loadPreset(/Specialty Café Restock/);
    expect(createButton()).toBeEnabled();

    await userEvent.selectOptions(screen.getByLabelText('SKU for "arabica beans half kg"'), "SPR-COF-ARB-1KG");
    expect(createButton()).toBeDisabled();
    await userEvent.click(createButton());
    expect(client.createOrder).not.toHaveBeenCalled();

    await act(async () => pending.resolve(samplePreview()));
    await waitFor(() => expect(createButton()).toBeEnabled());
  });

  it("disables Create PayPal Order while a parse is in flight", async () => {
    const second = deferred<ParsedOrderPreviewResponse>();
    const parseText = vi.fn().mockResolvedValueOnce(sampleParse()).mockReturnValueOnce(second.promise);
    renderApp(fakeClient({ parseText }));
    await loadPreset(/Specialty Café Restock/);
    expect(createButton()).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: /Artisan Bakery/ }));
    expect(createButton()).toBeDisabled();
    await act(async () => second.resolve(sampleParse()));
    await waitFor(() => expect(createButton()).toBeEnabled());
  });

  it("ignores a stale re-price that resolves after a newer preset", async () => {
    const stale = deferred<MatchPreviewResponse>();
    const parseText = vi
      .fn()
      .mockResolvedValueOnce(sampleParse())
      .mockResolvedValueOnce(sampleParse(previewWithQuery("bakery preset line")));
    renderApp(fakeClient({ parseText, matchPreview: vi.fn().mockReturnValue(stale.promise) }));
    await loadPreset(/Specialty Café Restock/);

    await userEvent.selectOptions(screen.getByLabelText('SKU for "arabica beans half kg"'), "SPR-COF-ARB-1KG");
    await loadPreset(/Artisan Bakery/, "bakery preset line");
    await act(async () => stale.resolve(previewWithQuery("STALE re-price")));

    expect(screen.getByLabelText('SKU for "bakery preset line"')).toBeInTheDocument();
    expect(screen.queryByText("STALE re-price")).not.toBeInTheDocument();
  });

  it("ignores a stale re-price that resolves after reset", async () => {
    const stale = deferred<MatchPreviewResponse>();
    const client = fakeClient({ matchPreview: vi.fn().mockReturnValue(stale.promise) });
    const { result } = renderHook(() => useCommandCenter(client));
    await act(() => result.current.actions.parseText("2x arabica"));
    act(() => result.current.actions.pinSku(0, "SPR-COF-ARB-1KG"));
    expect(result.current.state.busy).toBe("previewing");

    act(() => result.current.actions.reset());
    await act(async () => stale.resolve(previewWithQuery("STALE")));
    expect(result.current.state.preview).toBeNull();
    expect(result.current.state.items).toEqual([]);
    expect(result.current.state.busy).toBeNull();
  });

  it("ignores a stale parse that resolves after an order is loaded from history", async () => {
    const slowParse = deferred<ParsedOrderPreviewResponse>();
    const client = fakeClient({
      parseText: vi.fn().mockReturnValue(slowParse.promise),
      getOrder: vi.fn().mockResolvedValue(historicalOrder()),
    });
    const { result } = renderHook(() => useCommandCenter(client));
    let parsing!: Promise<void>;
    act(() => {
      parsing = result.current.actions.parseText("2x arabica");
    });
    await act(() => result.current.actions.loadOrder("ORD-DEMO00000001"));
    await act(async () => {
      slowParse.resolve(sampleParse(previewWithQuery("STALE parse")));
      await parsing;
    });
    expect(result.current.state.order?.public_order_id).toBe("ORD-DEMO00000001");
    expect(result.current.state.preview?.lines[0]?.query).toBe("historic fragment 1");
  });

  it("ignores a stale error from a superseded request", async () => {
    const failing = deferred<ParsedOrderPreviewResponse>();
    const parseText = vi.fn().mockReturnValueOnce(failing.promise).mockResolvedValueOnce(sampleParse());
    const { result } = renderHook(() => useCommandCenter(fakeClient({ parseText })));
    let first!: Promise<void>;
    act(() => {
      first = result.current.actions.parseText("first");
    });
    await act(() => result.current.actions.parseText("second"));
    await act(async () => {
      failing.reject(new ApiError(503, null, "late failure", null));
      await first;
    });
    expect(result.current.state.error).toBeNull();
    expect(result.current.state.preview).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------
// F4-02: loading an order from history drives the KPIs and the workbench
// ---------------------------------------------------------------------------------------------

describe("F4-02 history order selection", () => {
  it("previewFromOrder mirrors the persisted ledger exactly", () => {
    const order = historicalOrder();
    const preview = previewFromOrder(order);
    expect(preview.totals).toEqual({
      subtotal_cents: order.gross_subtotal_cents,
      platform_fee_cents: order.total_platform_fee_cents,
      net_vendor_payout_cents: order.total_vendor_payout_cents,
    });
    expect(preview.lines.map((l) => [l.query, l.quantity, l.subtotal_cents, l.platform_fee_cents])).toEqual(
      order.line_items.map((l) => [l.raw_query, l.quantity, l.subtotal_cents, l.platform_fee_cents]),
    );
    expect(preview.lines.every((l) => l.is_billable && l.matched_sku?.confidence_score === 1)).toBe(true);
    expect(preview.vendor_payouts.map((v) => v.net_vendor_payout_cents)).toEqual(
      order.payouts.map((p) => p.payout_amount_cents),
    );
  });

  it("clicking an order in Order history updates KPI cards and the resolution grid", async () => {
    const order = historicalOrder();
    const client = fakeClient({
      listOrders: vi.fn().mockResolvedValue({ total: 1, limit: 100, offset: 0, items: [order] }),
      getOrder: vi.fn().mockResolvedValue(order),
    });
    renderApp(client);
    expect(screen.getByTestId("kpi-lines")).toHaveTextContent("0/0");
    expect(screen.getByTestId("kpi-gmv")).toHaveTextContent("$0.00");

    await userEvent.click(screen.getByRole("tab", { name: /Order history/ }));
    const historyGrid = await screen.findByTestId("history-grid");
    await userEvent.click(await within(historyGrid).findByText("ORD-DEMO00000001"));

    await waitFor(() => expect(client.getOrder).toHaveBeenCalledWith("ORD-DEMO00000001"));
    await waitFor(() => expect(screen.getByTestId("kpi-lines")).toHaveTextContent("2/2"));
    expect(screen.getByTestId("kpi-gmv")).toHaveTextContent("$139.78");
    expect(screen.getByTestId("kpi-fee")).toHaveTextContent("$9.39");
    expect(screen.getByTestId("kpi-net")).toHaveTextContent("$130.39");
    const grid = screen.getByTestId("resolution-grid");
    expect(within(grid).getByText("historic fragment 1")).toBeInTheDocument();
    expect(within(grid).getByText("historic fragment 2")).toBeInTheDocument();
    expect(screen.getByText(/Locked to ORD-DEMO00000001/)).toBeInTheDocument();
    expect(screen.getByTestId("order-status")).toHaveTextContent("Completed");
  });
});

// ---------------------------------------------------------------------------------------------
// F4-03: invalid edits revert; failed re-prices restore the last good basket
// ---------------------------------------------------------------------------------------------

describe("F4-03 quantity edits", () => {
  it.each([["", false], ["abc", false], [0, false], [10_001, false], [2.5, false], ["-3", false], [1, true], [10_000, true], [" 7 ", true]])(
    "quantityError(%j) valid=%s",
    (value, valid) => {
      expect(quantityError(value) === null).toBe(valid);
    },
  );

  it("reverts an invalid quantity typed into the AG Grid cell", async () => {
    const onQuantityChange = vi.fn();
    const onInvalidQuantity = vi.fn();
    render(
      <SkuResolutionGrid
        preview={samplePreview()}
        locked={false}
        busy={false}
        onPinSku={vi.fn()}
        onQuantityChange={onQuantityChange}
        onInvalidQuantity={onInvalidQuantity}
      />,
    );
    const grid = await screen.findByTestId("resolution-grid");
    const qtyCell = await waitFor(() => {
      const cell = grid.querySelector<HTMLElement>('.ag-center-cols-container [row-index="0"] [col-id="quantity"]')
        ?? grid.querySelector<HTMLElement>('[row-index="0"] [col-id="quantity"]');
      expect(cell).not.toBeNull();
      return cell!;
    });
    expect(qtyCell).toHaveTextContent("4");

    await userEvent.click(qtyCell); // singleClickEdit opens agNumberCellEditor
    const input = await waitFor(() => {
      const el = qtyCell.querySelector("input");
      expect(el).not.toBeNull();
      return el!;
    });
    await userEvent.clear(input);
    await userEvent.type(input, "0{Enter}");

    await waitFor(() => expect(onInvalidQuantity).toHaveBeenCalledWith(expect.stringContaining("whole number")));
    expect(onQuantityChange).not.toHaveBeenCalled();
    await waitFor(() => expect(qtyCell).toHaveTextContent("4"));
  });

  it("passes a valid quantity edit through", async () => {
    const onQuantityChange = vi.fn();
    render(
      <SkuResolutionGrid preview={samplePreview()} locked={false} busy={false} onPinSku={vi.fn()} onQuantityChange={onQuantityChange} />,
    );
    const grid = await screen.findByTestId("resolution-grid");
    const qtyCell = await waitFor(() => {
      const cell = grid.querySelector<HTMLElement>('[row-index="0"] [col-id="quantity"]');
      expect(cell).not.toBeNull();
      return cell!;
    });
    await userEvent.click(qtyCell);
    const input = await waitFor(() => qtyCell.querySelector("input")!);
    await userEvent.clear(input);
    await userEvent.type(input, "7{Enter}");
    await waitFor(() => expect(onQuantityChange).toHaveBeenCalledWith(0, 7));
  });

  it("restores the previous items when a re-price fails", async () => {
    const client = fakeClient({
      matchPreview: vi.fn().mockRejectedValue(new ApiError(0, "network_error", "Cannot reach the CatalogPay API.", null)),
    });
    const { result } = renderHook(() => useCommandCenter(client));
    await act(() => result.current.actions.parseText("2x arabica"));
    const before = result.current.state.items;
    expect(before[0]?.quantity).toBe(4);

    await act(async () => result.current.actions.updateQuantity(0, 9));
    await waitFor(() => expect(result.current.state.busy).toBeNull());
    expect(result.current.state.items).toEqual(before);
    expect(result.current.state.error).toBe("Cannot reach the CatalogPay API.");
  });

  it("rejects invalid quantities in the hook without calling the API", async () => {
    const client = fakeClient();
    const { result } = renderHook(() => useCommandCenter(client));
    await act(() => result.current.actions.parseText("2x arabica"));
    act(() => result.current.actions.updateQuantity(0, 2.5));
    expect(result.current.state.error).toMatch(/whole number/);
    expect(client.matchPreview).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------------------------
// F4-04: ledger currency and the Total row
// ---------------------------------------------------------------------------------------------

describe("F4-04 vendor settlement grid", () => {
  it("formats with each row's currency and leaves Total attempts blank", async () => {
    const order = sampleOrder("COMPLETED", ["SUCCESS", "SUCCESS"]);
    order.currency = "EUR";
    order.payouts = order.payouts.map((p) => ({ ...p, currency: "EUR" }));
    expect(toSplitRows(order, null).map((r) => r.currency)).toEqual(["EUR", "EUR"]);

    render(
      <ApiProvider client={fakeClient()}>
        <VendorSettlementGrid order={order} preview={null} historyVersion={0} onSelectOrder={vi.fn()} />
      </ApiProvider>,
    );
    const grid = await screen.findByTestId("split-grid");
    await within(grid).findByText("Summit Peak Roasters");
    expect(within(grid).getByText("€56.81")).toBeInTheDocument();
    expect(within(grid).queryByText("$56.81")).not.toBeInTheDocument();
    expect(within(grid).getByText("€130.39")).toBeInTheDocument(); // pinned Total row

    const totalAttempts = await waitFor(() => {
      const cell = grid.querySelector('.ag-floating-bottom [col-id="attempts"]');
      expect(cell).not.toBeNull();
      return cell!;
    });
    expect(totalAttempts.textContent?.trim()).toBe("");
    const rowAttempts = grid.querySelector('.ag-center-cols-container [row-index="0"] [col-id="attempts"]');
    expect(rowAttempts?.textContent?.trim()).toBe("1");
  });
});

// ---------------------------------------------------------------------------------------------
// F4-05: client-side 0-byte guard and approval_url scheme check
// ---------------------------------------------------------------------------------------------

describe("F4-05 upload guard & approval URL safety", () => {
  it("rejects a 0-byte file before upload", () => {
    const onParseMedia = vi.fn();
    const onReject = vi.fn();
    render(
      <OrderIntakePanel
        text=""
        busy={false}
        locked={false}
        parse={null}
        sourceLabel={null}
        onTextChange={vi.fn()}
        onParseText={vi.fn()}
        onParseMedia={onParseMedia}
        onReject={onReject}
      />,
    );
    const empty = new File([], "empty.png", { type: "image/png" });
    fireEvent.change(screen.getByTestId("media-input"), { target: { files: [empty] } });
    expect(onParseMedia).not.toHaveBeenCalled();
    expect(onReject).toHaveBeenCalledWith(expect.stringContaining("empty (0 bytes)"));
  });

  it.each([
    ["https://www.sandbox.paypal.com/checkoutnow?token=X", "https://www.sandbox.paypal.com/checkoutnow?token=X"],
    ["HTTP://example.com/a", "http://example.com/a"],
    ["javascript:alert(1)", null],
    ["JaVaScRiPt:alert(1)", null],
    ["data:text/html,<script>alert(1)</script>", null],
    ["//evil.example/x", null],
    ["https://", null],
    ["", null],
    [null, null],
  ])("safeHttpUrl(%j) -> %j", (input, expected) => {
    expect(safeHttpUrl(input)).toBe(expected);
  });

  function renderLivePanel(approvalUrl: string) {
    const order = { ...sampleOrder("PAYPAL_ORDER_CREATED"), paypal_mode: "live_sandbox" as const, approval_url: approvalUrl };
    render(
      <PayPalSettlementPanel
        preview={samplePreview()}
        order={order}
        busy={null}
        idempotencyKey="cp-test"
        paypalMode="live_sandbox"
        animationStepMs={0}
        onCreateOrder={vi.fn()}
        onCapture={vi.fn()}
        onRetry={vi.fn()}
        onReset={vi.fn()}
      />,
    );
  }

  it("never renders a javascript: approval_url as a link", () => {
    renderLivePanel("javascript:alert(document.cookie)");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText(/Approval link withheld/)).toBeInTheDocument();
    expect(document.querySelector('a[href^="javascript"]')).toBeNull();
  });

  it("renders a real PayPal approval URL as a safe external link", () => {
    renderLivePanel("https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T");
    const link = screen.getByRole("link", { name: /Open PayPal Sandbox checkout/ });
    expect(link).toHaveAttribute("href", "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("locks every settlement action while another request is in flight", () => {
    render(
      <PayPalSettlementPanel
        preview={samplePreview()}
        order={sampleOrder("PAYPAL_ORDER_CREATED")}
        busy={null}
        blocked
        idempotencyKey="cp-test"
        paypalMode="sandbox_simulation"
        animationStepMs={0}
        onCreateOrder={vi.fn()}
        onCapture={vi.fn()}
        onRetry={vi.fn()}
        onReset={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /Approve & Capture/ })).toBeDisabled();
  });
});
