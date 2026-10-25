# CatalogPay Agent: Pitch, 3-Minute Demo Script & Devpost Answers

> **Before recording:** this script uses a 7-part demo structure: Hook → Problem → Solution → Live Demo → How It Works → Impact & Proof → Close. If you are following a specific named framework, rename the seven parts to match it. The timing, visuals and voiceover are designed so the parts can be re-labelled without re-timing.

**Target runtime: 2:58 (178 s).** The voiceover is about 350 words, roughly 2 words per second on average and never above 2.5, which leaves room to breathe. Hard stop before 3:00.

---

## Recording checklist (do this first)

- [ ] Backend and frontend running (`./scripts/dev.sh`), or the Docker / Render URL open in a browser at **1920×1080**, zoom 100–110%.
- [ ] Fresh database so the history tab holds only `ORD-DEMO00000001`: delete `backend/catalogpay.db`, then restart.
- [ ] Status bar shows **Backend online · 25 active SKUs**. Decide whether you are showing *Sandbox Simulation* (no keys) or *Live Sandbox* + *Gemini 2.5 Flash* (keys set) and say it consistently.
- [ ] `samples/sample_wholesale_po.png` on the desktop if you will show the drag-and-drop (Gemini key required).
- [ ] Browser notifications off, bookmarks bar hidden, cursor highlight on.
- [ ] Do one full rehearsal: the "this run X.Xs" pill must appear at the end.

---

## Part 1: Hook (0:00–0:12)

| Time | Visual | Voiceover |
|---|---|---|
| 0:00–0:06 | Full-screen phone mock: a messy WhatsApp-style order ("3x 1kg Ethiopian Yirgacheffe, 2 cases Barista Oat Milk…"). | "This is how cafés actually reorder from wholesalers: a message, a photo, a voice note." |
| 0:06–0:12 | Hard cut to the CatalogPay Command Center. | "Watch it become a paid, multi-vendor PayPal order, with every supplier paid, in seconds." |

## Part 2: Problem (0:12–0:30)

| Time | Visual | Voiceover |
|---|---|---|
| 0:12–0:20 | Slide: "~7 in 10 B2B orders are still handled manually" · "~8–12 min per manual PO" (sources in small text). | "Seven in ten B2B orders are still keyed in by hand: eight to twelve minutes each, for one supplier." |
| 0:20–0:30 | Slide: one order → four vendors → commission math → four payouts. "~45 min (our estimate)". | "On a marketplace, one order means SKU lookups, per-vendor commission math, and a separate payout for every vendor. We estimate forty-five minutes." |

## Part 3: Solution (0:30–0:42)

| Time | Visual | Voiceover |
|---|---|---|
| 0:30–0:42 | Command Center overview; slowly highlight Panels 1→4 and the flow stepper. | "CatalogPay Agent does all of it: Gemini understands the order, a hybrid matcher verifies every SKU, and PayPal takes the payment and splits the payouts automatically." |

## Part 4: Live Demo (0:42–2:05)

| Time | Visual | Voiceover |
|---|---|---|
| 0:42–0:50 | Click **Specialty Café Restock**. Grid fills instantly. | "One click. The messy message is parsed and resolved." |
| 0:50–1:04 | Hover the confidence pills, vendor commission badges and the pinned totals row. Zoom on KPI cards. | "Every line maps to a real catalog SKU with a confidence score. Three vendors, each with its own commission, priced in integer cents, so the totals add up exactly." |
| 1:04–1:22 | Click **Edge-Case Stress Test**. Point at the typo row, `half kg`, the red out-of-stock row and the red unmatched row. | "Now the hard case. A typo still matches. 'Half kg' becomes the 500-gram bag. Out-of-stock and nonsense lines are flagged, never billed." |
| 1:22–1:32 | On the ambiguous Colombian arabica row, open the in-cell dropdown and pick the 1kg bag; the row turns green and re-prices. | "This line is ambiguous, so the agent asks. Pick the 1 kg bag right in the grid, and it re-prices on the server." |
| 1:32–1:42 | Click **Create PayPal Order**; show the PayPal order ID and idempotency key. | "Create the PayPal order: an itemized Orders v2 checkout with an idempotency key, so a double-click can never charge twice." |
| 1:42–1:58 | Click **Approve & Capture + Trigger Split Payouts**. The timeline animates through captured, payouts dispatched and settled. Vendor payout rows turn **SUCCESS** with batch IDs. | "Capture. The amount and currency are verified against our ledger, stock is reserved atomically, and each vendor gets its own PayPal payout for exactly its net share." |
| 1:58–2:05 | Zoom the ROI pill: **"this run 3.x s"**. Click **Export CSV** on the settlement ledger. | "Settled, in seconds, and finance exports it with one click." |

## Part 5: How It Works (2:05–2:30)

| Time | Visual | Voiceover |
|---|---|---|
| 2:05–2:17 | Architecture diagram from the README (Mermaid render). | "Under the hood: Gemini 2.5 Flash structured outputs, a local hybrid SKU matcher, FastAPI, and PayPal Orders v2 and Payouts v1, all served from one Docker image on Render." |
| 2:17–2:30 | Open **Catalog & agent tools** → MCP tool cards; then flash the test summary (532 pytest + 94 vitest). | "The same flow is exposed as agent tools for Gemini function calling and MCP. Over six hundred tests cover the money paths: retries, timeouts, races and webhooks." |

## Part 6: Impact & Proof (2:30–2:48)

| Time | Visual | Voiceover |
|---|---|---|
| 2:30–2:40 | Before / After table from the README. | "Before: about forty-five minutes of lookups, spreadsheets and transfers. After: seconds, with every cent accounted for." |
| 2:40–2:48 | Order history tab: click `ORD-DEMO00000001`, and the workbench reloads the historical order. | "Every order stays auditable: the full split ledger, PayPal IDs, and CSV export." |

## Part 7: Close (2:48–2:58)

| Time | Visual | Voiceover |
|---|---|---|
| 2:48–2:58 | Logo + repo URL + "Runs with zero keys: try it in 2 minutes". | "CatalogPay Agent: messy orders in, verified PayPal checkouts and paid vendors out. It runs with zero setup. Try it yourself." |

---

## 30-second elevator pitch

> Wholesale buyers still order by text, photo and voice note, and marketplaces still turn those into payments by hand: SKU lookups, commission spreadsheets and one payout per vendor. CatalogPay Agent uses Gemini 2.5 Flash to understand the order, a hybrid matcher to verify every SKU, and PayPal Orders v2 plus Payouts v1 to take the payment and pay every vendor its exact share. Every amount is integer cents, enforced by the database, and every PayPal call is idempotent. A 45-minute chore becomes a few seconds.

---

## Devpost submission answers (copy-paste)

### Inspiration
Watching independent cafés reorder supplies: a WhatsApp message to one supplier, a photo of a whiteboard to another, a voice note to a third. On a marketplace, someone then has to turn that into SKUs, compute each vendor's commission without rounding errors, collect the payment, and pay every vendor separately. We wanted an agent that does the whole loop and that a finance team could actually trust with money.

### What it does
CatalogPay Agent turns a messy wholesale order (typed, photographed, or spoken) into a SKU-verified PayPal checkout that automatically splits and pays every vendor.

- Gemini 2.5 Flash extracts product phrases and quantities with structured outputs.
- A hybrid matcher maps each phrase to a catalog SKU, combining fuzzy matching, character n-gram TF-IDF and unit normalization ("half kg" = 500g). It shows a confidence score and lets the operator switch candidates in an AG Grid workbench.
- Prices and commissions are computed in integer cents, with the fee charged once per vendor.
- PayPal Orders v2 collects the payment. After a verified capture, PayPal Payouts v1 pays each vendor its net share automatically.
- A React 19 Command Center with two AG Grid v33 workbenches shows the resolution, the economics and the full settlement ledger, with CSV export.
- The same flow is exposed as Gemini function-calling and MCP tools.

### How we built it
- **Backend:** FastAPI + SQLAlchemy 2.0 on Python 3.12+.
- **AI:** Gemini 2.5 Flash via `google-genai`, with a prompt-injection-hardened system instruction and item-by-item validation of model output.
- **Matching:** a local hybrid SKU matcher (rapidfuzz + numpy TF-IDF + unit agreement), so it is fast and deterministic.
- **Payments:** an httpx-based PayPal client (OAuth2 token caching, Orders v2, Payouts v1, webhook verification), plus an in-process PayPal simulator that speaks the same HTTP API, so the full lifecycle runs without credentials.
- **Frontend:** React 19 + TypeScript + Vite + Tailwind v4 + AG Grid v33 (Theming API, custom cell renderers and editors, pinned totals rows, CSV export).
- **Deployment:** one multi-stage Docker image serving the API and the SPA, deployed with a Render Blueprint.
- **Tests:** 532 pytest and 94 Vitest tests, with mutation checks on the safety guards.

### Challenges we ran into
- **Rounding drift.** Charging commission per line made two identical lines cost 1¢ more than one combined line. We moved to charging the fee once per vendor and apportioning it by largest remainder, so splitting or merging lines never changes what a vendor pays.
- **Ambiguous payment failures.** A timed-out PayPal call may have succeeded. We separated *ambiguous* failures (retry with the same `PayPal-Request-Id`) from *definitive* ones (rotate IDs), and made our simulator replay cached responses like PayPal does. That exposed a bug where a declined capture would have replayed its rejection forever.
- **Concurrency.** Two simultaneous captures must never oversell. We added a capture lease plus conditional `UPDATE … WHERE stock_quantity >= qty`, with database CHECK constraints as a second line of defense.
- **Trusting AI with money.** We let the model extract, never price: buyer text is fenced as untrusted data, quantities must be real integers, and tests prove an injected "$0.01" can't change the charge.
- **Unit parsing edge cases.** "1,000g" originally normalized to "1g" and "500 gm" was read as a quantity of 500. Both were caught in review and are now covered by regression tests.

### Accomplishments that we're proud of
- The full lifecycle (messy text → verified SKUs → PayPal order → verified capture → per-vendor payouts) takes about 60 ms of server time in sandbox simulation (9 lines, 4 vendors, measured median), and runs with zero setup for judges.
- The ledger invariant `GMV = platform fee + vendor payouts` is enforced at every level, including by the database itself.
- Retries are provably safe: lost responses, timeouts and duplicate webhooks never charge a buyer or pay a vendor twice.
- Two AG Grid workbenches that are the actual operating surface: in-cell SKU switching, live re-pricing, pinned totals and finance-ready CSV.

### What we learned
- In payments, *"did it fail?"* is the wrong question; *"could it have succeeded?"* is the right one. Idempotency keys only help if you know when to reuse them and when to rotate them.
- A simulator that faithfully reproduces the real API's quirks (request-ID replay, duplicate-ID rejection) finds real bugs before production does.
- Structured outputs make LLMs dependable extractors, as long as you still validate every field and keep them away from prices.

### What's next
- Live PayPal Sandbox → production onboarding for real marketplace vendors (PayPal for Marketplaces / partner onboarding).
- Inventory sync and SKU learning from operator corrections, so a pinned candidate becomes a learned alias.
- Voice-first reordering over messaging channels, with the agent asking clarifying questions for ambiguous lines.
- Postgres plus background payout reconciliation workers for scale.
