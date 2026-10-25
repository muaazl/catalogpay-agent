import { describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient } from "../api/client";

function mockFetch(status: number, body: unknown) {
  // A fresh Response per call: a body can only be read once.
  return vi.fn<typeof fetch>().mockImplementation(async () =>
    new Response(body === undefined ? "" : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("api client", () => {
  it("sends Idempotency-Key and JSON on createOrder", async () => {
    const fetchImpl = mockFetch(201, { public_order_id: "ORD-1" });
    const client = createApiClient({ baseUrl: "http://api.test", fetchImpl });
    await client.createOrder({ items: [{ sku_code: "GLP-STR-500", quantity: 2 }] }, "cp-key-123");

    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("http://api.test/api/v1/orders");
    expect(init?.method).toBe("POST");
    const headers = new Headers(init?.headers);
    expect(headers.get("Idempotency-Key")).toBe("cp-key-123");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({
      items: [{ sku_code: "GLP-STR-500", quantity: 2 }],
    });
  });

  it("uploads media as multipart FormData without forcing a JSON content type", async () => {
    const fetchImpl = mockFetch(200, {});
    const client = createApiClient({ fetchImpl });
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "po.png", { type: "image/png" });
    await client.parseMedia(file, "whiteboard photo", 5);

    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/v1/orders/parse-media");
    const form = init?.body as FormData;
    expect(form).toBeInstanceOf(FormData);
    expect((form.get("file") as File).name).toBe("po.png");
    expect(form.get("note")).toBe("whiteboard photo");
    expect(form.get("top_k")).toBe("5");
    expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
  });

  it("encodes path ids and builds query strings", async () => {
    const fetchImpl = mockFetch(200, {});
    const client = createApiClient({ fetchImpl });
    await client.captureOrder("ORD-A/../B");
    await client.listOrders({ status: "COMPLETED", offset: 0 });
    await client.listSkus({ q: "100%", vendor: undefined });
    expect(fetchImpl.mock.calls.map((c) => c[0])).toEqual([
      "/api/v1/orders/ORD-A%2F..%2FB/capture",
      "/api/v1/orders?limit=100&status=COMPLETED&offset=0",
      "/api/v1/catalog/skus?limit=200&q=100%25",
    ]);
  });

  it("raises ApiError with the backend error code and detail", async () => {
    const client = createApiClient({
      fetchImpl: mockFetch(409, { detail: "Insufficient stock - GCB-YST-500G", error: "insufficient_stock" }),
    });
    const err = await client.captureOrder("ORD-1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "insufficient_stock", message: "Insufficient stock - GCB-YST-500G" });
  });

  it("summarizes FastAPI 422 validation errors", async () => {
    const client = createApiClient({
      fetchImpl: mockFetch(422, {
        detail: [{ loc: ["body", "items", 0, "quantity"], msg: "Input should be greater than or equal to 1" }],
      }),
    });
    await expect(client.matchPreview({ items: [{ query: "x", quantity: 0 }] })).rejects.toThrow(
      "items.0.quantity: Input should be greater than or equal to 1",
    );
  });

  it("maps network failures to a friendly ApiError", async () => {
    const client = createApiClient({ fetchImpl: vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch")) });
    await expect(client.health()).rejects.toMatchObject({ status: 0, code: "network_error" });
  });
});
