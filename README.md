# CatalogPay Agent

Converts messy, unstructured buyer orders (text, images, voice notes) into SKU-verified PayPal
Orders, then executes automated multi-vendor split payouts on capture.

**Status:** Feature 1 (foundation, financial schema, seed catalog, Hybrid SKU Retrieval Engine)
and Feature 2 (Gemini 2.5 Flash multimodal order parser + end-to-end resolution API).

## Quickstart (backend)

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload        # seeds the demo catalog on startup
pytest                               # test suite
ruff check .                         # lint
```

Open http://localhost:8000/docs for the interactive API.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/health` | Status, DB connectivity, active SKU count (503 if the DB is down) |
| `GET /api/v1/catalog/skus` | List SKUs; filters `vendor_id`, `vendor`, `category`, `q`, paging |
| `POST /api/v1/catalog/match-preview` | Raw phrases + quantities -> matched SKUs, stock check, fee split |
| `POST /api/v1/orders/parse-text` | Messy order text -> Gemini (or offline regex fallback) -> full match preview |
| `POST /api/v1/orders/parse-media` | Photo / PDF / voice note (multipart, max 10 MB) -> Gemini -> full match preview |

Set `GEMINI_API_KEY` in `backend/.env` to enable Gemini 2.5 Flash. Without it, text orders use
the deterministic offline parser and media uploads return `503`. Gemini only extracts product
phrases and quantities. SKUs, prices and fees always come from the catalog database.

## Design notes

- **Money is integers only.** Prices are cents, commissions are basis points. The platform
  fee rounds half-up to a cent and the vendor net is `subtotal - fee`, so every line, vendor
  and order total balances exactly.
- **Hybrid matching runs locally and deterministically.** rapidfuzz lexical scores, numpy
  character n-gram TF-IDF cosine, and unit normalization (`half kg` = `0.5kg` = `500g`) are
  blended into a 0–1 confidence. Near-ties are flagged `is_ambiguous` and never auto-billed.
- **Database:** SQLite with WAL and foreign keys by default. Set `DATABASE_URL` for
  PostgreSQL. See `backend/.env.example`.

## License

[MIT](LICENSE)
