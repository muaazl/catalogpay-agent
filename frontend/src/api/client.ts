import type {
  AgentToolsResponse,
  ApiErrorBody,
  CreateOrderRequest,
  HealthResponse,
  MatchPreviewRequest,
  MatchPreviewResponse,
  OrderDetail,
  OrderListParams,
  OrderListResponse,
  ParsedOrderPreviewResponse,
  ParseTextRequest,
  SkuListParams,
  SkuListResponse,
} from "./types";

/** An HTTP error from the API, carrying the backend's machine-readable ``error`` code. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly body: unknown;

  constructor(status: number, code: string | null, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.body = body;
  }
}

function describeError(body: unknown, status: number, statusText: string): string {
  const detail = (body as ApiErrorBody | null)?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI validation errors: [{loc: ["body", "items", 0, "quantity"], msg: "..."}]
    return detail
      .slice(0, 3)
      .map((d) => {
        const loc = (d.loc ?? []).filter((p) => p !== "body").join(".");
        return loc ? `${loc}: ${d.msg ?? "invalid"}` : (d.msg ?? "invalid");
      })
      .join("; ");
  }
  return `HTTP ${status}${statusText ? ` ${statusText}` : ""}`;
}

function query(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

export function createApiClient(options: ApiClientOptions = {}) {
  const base = `${options.baseUrl ?? import.meta.env.VITE_API_BASE_URL ?? ""}/api/v1`;
  const doFetch: typeof fetch = (input, init) => (options.fetchImpl ?? fetch)(input, init);

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined && !(init.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    let response: Response;
    try {
      response = await doFetch(`${base}${path}`, { ...init, headers });
    } catch (cause) {
      throw new ApiError(0, "network_error", "Cannot reach the CatalogPay API.", cause);
    }
    const text = await response.text();
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
    }
    if (!response.ok) {
      const code = (body as ApiErrorBody | null)?.error ?? null;
      throw new ApiError(
        response.status,
        code,
        describeError(body, response.status, response.statusText),
        body,
      );
    }
    return body as T;
  }

  const post = <T>(path: string, payload?: unknown, headers?: HeadersInit) =>
    request<T>(path, {
      method: "POST",
      body: payload === undefined ? undefined : JSON.stringify(payload),
      headers,
    });

  const orderPath = (id: string) => `/orders/${encodeURIComponent(id)}`;

  return {
    health: () => request<HealthResponse>("/health"),
    listSkus: (params: SkuListParams = {}) =>
      request<SkuListResponse>(`/catalog/skus${query({ limit: 200, ...params })}`),
    matchPreview: (payload: MatchPreviewRequest) =>
      post<MatchPreviewResponse>("/catalog/match-preview", payload),
    parseText: (payload: ParseTextRequest) =>
      post<ParsedOrderPreviewResponse>("/orders/parse-text", payload),
    parseMedia: (file: File, note = "", topK = 3) => {
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("note", note);
      form.append("top_k", String(topK));
      return request<ParsedOrderPreviewResponse>("/orders/parse-media", {
        method: "POST",
        body: form,
      });
    },
    createOrder: (payload: CreateOrderRequest, idempotencyKey: string) =>
      post<OrderDetail>("/orders", payload, { "Idempotency-Key": idempotencyKey }),
    listOrders: (params: OrderListParams = {}) =>
      request<OrderListResponse>(`/orders${query({ limit: 100, ...params })}`),
    getOrder: (id: string) => request<OrderDetail>(orderPath(id)),
    captureOrder: (id: string) => post<OrderDetail>(`${orderPath(id)}/capture`),
    retryPayouts: (id: string) => post<OrderDetail>(`${orderPath(id)}/retry-payouts`),
    agentTools: () => request<AgentToolsResponse>("/agent/tools"),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

export const api: ApiClient = createApiClient();
