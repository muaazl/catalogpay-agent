import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ApiClient } from "../api/client";
import { ApiError } from "../api/client";
import { ApiProvider } from "../api/context";
import App from "../App";
import { statusPath } from "../components/PayPalSettlementPanel";
import { orderItemsFromPreview } from "../hooks/useCommandCenter";
import { fakeClient, sampleOrder, samplePreview } from "./fixtures";

function renderApp(client: ApiClient) {
  render(
    <ApiProvider client={client}>
      <App animationStepMs={0} />
    </ApiProvider>,
  );
}

async function loadCafePreset() {
  await userEvent.click(screen.getByRole("button", { name: /Specialty Café Restock/ }));
  await screen.findByLabelText('SKU for "arabica beans half kg"');
}

describe("status path animation", () => {
  it("walks every intermediate state", () => {
    expect(statusPath("PAYPAL_ORDER_CREATED", "COMPLETED")).toEqual([
      "CAPTURED",
      "PAYOUTS_PENDING",
      "COMPLETED",
    ]);
    expect(statusPath("PAYPAL_ORDER_CREATED", "PAYOUTS_PARTIAL_FAILED")).toEqual([
      "CAPTURED",
      "PAYOUTS_PARTIAL_FAILED",
    ]);
    expect(statusPath(null, "PAYPAL_ORDER_CREATED")).toEqual(["DRAFT", "PAYPAL_ORDER_CREATED"]);
    expect(statusPath("COMPLETED", "COMPLETED")).toEqual(["COMPLETED"]);
  });
});

describe("order -> capture -> payout workflow", () => {
  it("runs the zero-friction happy path end to end", async () => {
    const client = fakeClient();
    renderApp(client);

    expect(await screen.findByText("Backend online")).toBeInTheDocument();
    expect(screen.getByText("25 active SKUs")).toBeInTheDocument();
    expect(screen.getByText("PayPal Sandbox Simulation")).toBeInTheDocument();
    expect(screen.getByTestId("roi-pill")).toHaveTextContent("~45 mins");

    await loadCafePreset();
    expect(client.parseText).toHaveBeenCalledWith({
      text: expect.stringContaining("Ethiopian Yirgacheffe"),
      top_k: 3,
    });
    expect(screen.getByTestId("kpi-gmv")).toHaveTextContent("$139.78");
    expect(screen.getByTestId("kpi-fee")).toHaveTextContent("$9.39");
    expect(screen.getByTestId("kpi-net")).toHaveTextContent("$130.39");

    // 1. Create the PayPal order: only billable lines, with an idempotency key.
    await userEvent.click(screen.getByRole("button", { name: /Create PayPal Order/ }));
    await screen.findByText("PayPal order created");
    const [payload, key] = vi.mocked(client.createOrder).mock.calls[0]!;
    expect(payload).toEqual({
      items: [
        { sku_code: "SPR-COF-ARB-500G", quantity: 4, raw_query: "arabica beans half kg" },
        { sku_code: "BPE-TMP-58MM", quantity: 2, raw_query: "espresso tamper 58mm" },
      ],
      customer_note: "Back door delivery",
      parser_mode: "deterministic_fallback",
    });
    expect(key).toMatch(/^cp-[0-9a-f-]{36}$/);
    const panel = screen.getByTestId("settlement-panel");
    expect(within(panel).getAllByText(/ORD-1A2B3C4D5E6F/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Locked to ORD-1A2B3C4D5E6F/)).toBeInTheDocument();

    // 2. Capture + split payouts.
    await userEvent.click(screen.getByRole("button", { name: /Approve & Capture \+ Trigger Split Payouts/ }));
    await waitFor(() => expect(screen.getByTestId("order-status")).toHaveTextContent("Completed"));
    expect(client.captureOrder).toHaveBeenCalledWith("ORD-1A2B3C4D5E6F");
    expect(screen.getByTestId("payout-status-1")).toHaveTextContent("SUCCESS");
    expect(screen.getByTestId("payout-status-2")).toHaveTextContent("SUCCESS");
    expect(within(panel).getByText("SIM-PAYOUT-AAAA")).toBeInTheDocument();
    expect(screen.getByTestId("settled-banner")).toHaveTextContent("2 vendors paid $130.39");
    expect(screen.getByTestId("run-duration")).toHaveTextContent(/this run \d+\.\ds/);
    expect(screen.queryByRole("button", { name: /Retry Failed Payouts/ })).not.toBeInTheDocument();
    await waitFor(() => expect(client.listOrders).toHaveBeenCalledTimes(3)); // ledger refreshed
  });

  it("recovers from a failed vendor payout with one retry click", async () => {
    const client = fakeClient({
      captureOrder: vi.fn().mockResolvedValue(sampleOrder("PAYOUTS_PARTIAL_FAILED", ["FAILED", "SUCCESS"])),
    });
    renderApp(client);
    await loadCafePreset();
    await userEvent.click(screen.getByRole("button", { name: /Create PayPal Order/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Approve & Capture/ }));

    await waitFor(() => expect(screen.getByTestId("order-status")).toHaveTextContent("Payout failed"));
    expect(screen.getByTestId("payout-status-1")).toHaveTextContent("FAILED");
    expect(screen.queryByTestId("run-duration")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Retry Failed Payouts/ }));
    await waitFor(() => expect(screen.getByTestId("order-status")).toHaveTextContent("Completed"));
    expect(client.retryPayouts).toHaveBeenCalledWith("ORD-1A2B3C4D5E6F");
    expect(client.captureOrder).toHaveBeenCalledTimes(1); // never re-captured
    expect(screen.getByTestId("payout-status-1")).toHaveTextContent("SUCCESS");
  });

  it("re-prices server-side when the operator switches a candidate", async () => {
    const repriced = samplePreview();
    repriced.lines[0] = {
      ...repriced.lines[0]!,
      is_pinned: true,
      matched_sku: repriced.lines[0]!.candidates[1]!,
      unit_price_cents: 2790,
      subtotal_cents: 11160,
    };
    const client = fakeClient({ matchPreview: vi.fn().mockResolvedValue(repriced) });
    renderApp(client);
    await loadCafePreset();

    await userEvent.selectOptions(screen.getByLabelText('SKU for "arabica beans half kg"'), "SPR-COF-ARB-1KG");
    await waitFor(() => expect(client.matchPreview).toHaveBeenCalledTimes(1));
    expect(client.matchPreview).toHaveBeenCalledWith({
      top_k: 3,
      items: [
        { query: "arabica beans half kg", quantity: 4, sku_code: "SPR-COF-ARB-1KG" },
        { query: "espresso tamper 58mm", quantity: 2 },
        { query: "unicorn saddles", quantity: 3 },
      ],
    });
    await screen.findByText("$111.60");
    expect(screen.getByLabelText('SKU for "arabica beans half kg"')).toHaveValue("SPR-COF-ARB-1KG");
  });

  it("reuses the idempotency key when order creation is retried", async () => {
    const createOrder = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(503, "paypal_unavailable", "PayPal returned HTTP 503.", null))
      .mockResolvedValueOnce(sampleOrder("PAYPAL_ORDER_CREATED"));
    const client = fakeClient({ createOrder });
    renderApp(client);
    await loadCafePreset();

    await userEvent.click(screen.getByRole("button", { name: /Create PayPal Order/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("PayPal returned HTTP 503.");
    await userEvent.click(screen.getByRole("button", { name: /Create PayPal Order/ }));
    await screen.findByText("PayPal order created");
    expect(createOrder.mock.calls[0]![1]).toBe(createOrder.mock.calls[1]![1]);
  });

  it("shows the true order state after a capture error", async () => {
    const client = fakeClient({
      captureOrder: vi
        .fn()
        .mockRejectedValue(new ApiError(502, "capture_verification_failed", "Capture verification failed.", null)),
      getOrder: vi.fn().mockResolvedValue({
        ...sampleOrder("PAYPAL_ORDER_CREATED"),
        payout_hold_reason: "captured 13977 cents but ledger total is 13978",
      }),
    });
    renderApp(client);
    await loadCafePreset();
    await userEvent.click(screen.getByRole("button", { name: /Create PayPal Order/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Approve & Capture/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Capture verification failed.");
    expect(await screen.findByText(/Payouts on hold/)).toBeInTheDocument();
    expect(screen.queryByTestId("settled-banner")).not.toBeInTheDocument();
  });

  it("explains when media parsing needs a Gemini key", async () => {
    const client = fakeClient({
      parseMedia: vi.fn().mockRejectedValue(new ApiError(503, "gemini_not_configured", "GEMINI_API_KEY is required", null)),
    });
    renderApp(client);
    const input = screen.getByTestId("media-input");
    await userEvent.upload(input, new File(["png"], "po.png", { type: "image/png" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Media parsing needs GEMINI_API_KEY");
  });

  it("only billable lines become order items", () => {
    expect(orderItemsFromPreview(samplePreview()).map((i) => i.sku_code)).toEqual([
      "SPR-COF-ARB-500G",
      "BPE-TMP-58MM",
    ]);
    expect(orderItemsFromPreview(null)).toEqual([]);
  });

  it("disables checkout until at least one line is billable", async () => {
    const preview = samplePreview();
    preview.lines = preview.lines.map((l) => ({ ...l, is_billable: false }));
    const client = fakeClient({
      parseText: vi.fn().mockResolvedValue({ ...(await fakeClient().parseText({ text: "" })), match_preview: preview }),
    });
    renderApp(client);
    await loadCafePreset();
    expect(screen.getByRole("button", { name: /Create PayPal Order/ })).toBeDisabled();
  });
});
