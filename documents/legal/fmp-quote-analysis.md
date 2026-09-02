# FMP formal quote — analysis and counter-offer

> Saved from the working plan, 2026-09-02. Companion to
> [`fmp-order-form-checklist.md`](fmp-order-form-checklist.md) (endpoint-level) and
> [`fmp-negotiation-reply.md`](fmp-negotiation-reply.md) (the email thread).
> Supersedes the vendor-alternative conclusions in `fmp-vs-twelvedata-survival-analysis.md`
> only where it cites a 2026-09 price; the licensing findings there still stand.

## Context

FMP's formal quote: **$29,500 list → 55% "initial partnership discount" → $13,245/yr = $1,104/mo.**
That is 2.2× the *"approximately $500 per month"* quoted by email on 2026-08-26, against a stated
ceiling of **$600/mo**, pre-launch, no revenue, personally funded.

Laith's explanation holds up: *"Indexes, mutual funds, and commodities … were not part of the
original scope of work, however, I added them."* The user's KEEP list did name
**"ETF & Mutual Fund APIs"**, **"Index Market Data APIs"** and **"Commodity Market Data APIs"** —
FMP's own category names from the original request form. So the increase is explained, not
sharp practice. It is still unaffordable.

The user asked whether a cheaper vendor could replace or supplement FMP. **It cannot** (§1).
The lever is the package list and the contract, not the vendor.

---

## 1. Alternatives — settled, do not re-litigate

The market has one shape: **published prices sell API *access*; showing data to end users is always
a separate negotiated licence.** Across ~30 vendors already evaluated in
`documents/legal/`, exactly **three or four** have published, self-serve external-display grants.

| Vendor | External display | Price | Verdict |
|---|---|---|---|
| **Twelve Data Venture** | ✅ published | **$499/mo** ($414 annual) | **No analyst estimates, no institutional holdings, no insider** — those need Enterprise. And **no news API at all** (0 hits across their docs), no 13F reverse lookup, no congress, no transcripts, **no product revenue segmentation** |
| **Twelve Data Enterprise** | ✅ | **$1,099/mo** | Same price as FMP, still missing news/congress/transcripts/segmentation |
| **Alpha Vantage** | ❌ *"personal, non-commercial use"*; §2.a.iii catches indirect access | contact sales | Not the cheap commercial tier the summary claimed |
| **OHLC.dev** | ⚠️ claims *"full commercial rights, no additional licensing fees"* | $15–100/mo | **Reject** — §2 |
| **EODHD** | ❌ $399 tier **explicitly forbids display**; real display quote ~$2,499 | — | 12× the apparent price |
| **Intrinio Startup** | ✅ *"Commercial Use and Display Rights"* | $333/$666/$999 | Partial coverage only |
| **Finnhub** | ❌ bans *"derived results"*; commercial is **$3,500/mo** | — | *"Citing Finnhub makes FMP look cheap"* |

**The governing asymmetry, from `fmp-gap-build-vs-buy.md`:** a migration is **30–44 dev-days
minimum** for reduced scope (150–290 for full), and *"you still have an FMP contract at the end of
it"* — because product revenue segmentation is **FMP-only among every vendor researched**, and
news, congress and transcripts have no free or cheap licensed source.

**Self-sourcing is worse:** ~205 dev-days ≈ 10 months of not shipping ≈ ~$82,000 of opportunity
cost ≈ **ten years of FMP fees.** And FMP §6.3 requires deleting cached data on termination, so
~28 of those days exist *only because you are cancelling*.

## 2. OHLC.dev — do not use

- Sold through **RapidAPI**; terms name **no company, no jurisdiction, no operator, no data licensors**.
- Never mentions exchange fees.
- **110,000 requests/month** on the top $100 plan — orders of magnitude below this app's volume.
- No fundamentals, news, analyst, 13F, insider or congressional coverage.

Exactly the trap the repo already documents: *"A display right from a vendor who doesn't hold one
is worth nothing."*

---

## 3. 🔴 The contradiction that must be fixed first

Laith's email: *"we … **changed your commitment from annual to monthly**."*

The Order Form says:

```
Initial Subscription Term : Annual
Renewal Term              : Annual
Billing Frequency         : Monthly
```

**He appears to have changed the *billing frequency*, not the *commitment*.** The document governs,
and as written it is a 12-month auto-renewing term. §9.2(a) does grant termination *"upon thirty
(30) notice … for any reason"* — but §4.1 says fees are *"non-cancelable and non-refundable
regardless of any early termination"* and §9.3 requires paying *"all Fees accrued and unpaid."*

§9.3 makes the **full remaining term** payable only on FMP-declared breach, which implies a
9.2(a) exit simply stops the monthly billing. That reading is probably right. **$13,245 is too much
to rest on "probably."** Ask for `Initial Subscription Term: Monthly` on the document.

## 4a. 🎯 REVISED CUT — drop Mutual Funds + Commodities + **Indexes**, keep ETF

Audited 2026-09-02 against the source. **Key structural fact:** FMP's **ETF package contains no
price endpoints** — only `ETF Holdings / Information / Country Allocation / Asset Exposure /
Sector Weighting / Disclosure`. ETF *prices* come from **Historical & Intraday**, exactly like any
equity (`get_etf_core`, `etf_service.py:855`, touches only quote + chart). So **SPY/QQQ/DIA/GLD/USO
are priced by a package we are keeping regardless.**

| Drop | List | Annual | **Monthly** |
|---|---:|---:|---:|
| Nothing | $29,500 | $13,245 | $1,104 |
| Mutual Funds | $27,000 | $12,123 | $1,010 |
| + Commodities | $24,500 | $11,001 | $917 |
| **+ Indexes (keep ETF)** | **$21,500** | **$9,654** | **$804** |
| + ETF as well | $18,500 | $8,307 | $692 |

*(actual discount is 44.9% — $13,245 ÷ $29,500 — not the stated 55%)*

### Between ETF and Indexes, drop **Indexes**

1. **Reachability.** `ETFDetailView` is reached from search (`etf` *and* `fund` types), watchlist,
   whale 13F holdings, trade groups, related taps and push. `IndexDetailView` is reached from
   3 Home tiles and one push route — **never from search** (`_get_asset_type` has no index branch).
2. **Recoverability.** SPY tracks `^GSPC` within 0.01% (measured). **ETF holdings have no
   substitute at any price** — they cannot be derived from prices.
3. **Failure mode.** Dropping Indexes = a label nuance. Dropping ETF ships a **false green
   "Well Diversified" badge** (`ETFDetailModels.swift:151` — `weight=0 → .low`) over an empty
   "Ingredients" strip, contradicting the backend's own honest "data isn't available" message.
4. **The Indexes package is thin.** `*-constituent` fetches 503 rows for a `len()`, and
   503/3000/30 are already hardcoded in `_INDEX_PROFILES`. Index P/E comes from Supabase
   `sector_benchmarks`, not FMP.

### Re-point work (~1 day)

`_PULSE_SYMBOLS` (`home_dashboard_service.py:80`) → SPY/QQQ/DIA/GLD/USO + BTC via CoinGecko; plus
`MARKET_INDEX_SYMBOL` in `updates_insight_sweeper.py:78` and `widget_movers_service.py:106`.
Tap-through improves: tiles route to `ETFDetailView` (7 chart ranges, performance, holdings) instead
of `IndexDetailView` (largely a static profile dict).

⚠️ **Quality caveats to state honestly in the UI:** QQQ tracks the Nasdaq-**100**, not the
Composite — relabel the tile. **USO tracks oil poorly** (futures roll drag). SPY/DIA are excellent.
⚠️ **Do not drop Indexes and Commodities without editing `_PULSE_SYMBOLS`** — the strip would fall
back to `BTCUSD` alone, whose price is in no package either.

### Corrections to `fmp-order-form-checklist.md`

- **`sp500-constituent` is NOT the sector-benchmark universe builder** — that is stale. The live
  builder is the static `backend/data/benchmark_universe.json`. `compute_and_persist_all_sectors`
  has **no caller**; `compute_all_benchmarks` is retired from scheduling and falls back to
  `_FALLBACK_SECTOR_TICKERS`. Downgrade it from Tier 2.
- **Commodities:** 7 of 14 symbols (wheat, corn, soybeans, coffee, sugar, cocoa, cotton) are
  **already orphaned** — no navigation path from anywhere in the app.

## 4b. What to cut — Mutual Funds only (superseded by 4a)

FMP split the user's single requested category **"ETF & Mutual Fund APIs"** into **two** priced
packages: ETF $3,000 and **Mutual Funds Holdings $2,500**.

**Verified: the app calls zero mutual-fund endpoints.** `grep -E '(etf|fund)' fmp.py` returns
exactly three strings — `etf/info`, `etf/holdings`, `etf/sector-weightings`.

Dropping it: **$2,500 off list → $27,000 → ~$1,010/mo** at the same discount. Modest, but it is
provable, costs nothing, and it is the cleanest ask on the table.

Indexes and Commodities are **kept** per the user's decision — no re-point to SPY/QQQ/DIA or
GLD/USO, so Home's Market Pulse and the index/commodity detail screens are untouched.

## 5. Crypto — the gap FMP cannot fill, and the answer you already own

`BTCUSD` price appears in **no package**. Market News *does* include **Crypto News API**
(confirmed in the package 9 description), so crypto *news* is covered.

**CoinGecko Basic ($29/mo) is already in the launch budget and already integrated**
(`backend/app/integrations/coingecko.py`). Crypto price should move there rather than being
negotiated into the FMP package.

⚠️ **Verify before relying on it:** `crypto_service.py:721,740-742` currently sources crypto price
from FMP `quote`/`batch-quote`, not CoinGecko — CoinGecko supplies supply/FDV/volume metadata.
Moving price to CoinGecko is a real (small) task, not a config change.

## 6. Remaining asks for the counter

1. **Term** — `Initial Subscription Term: Monthly` on the Order Form (§3).
2. **Drop Mutual Funds Holdings** — $2,500, zero usage (§4).
3. 🔴 **AI Prompt use named as a Permitted Use.** Exhibit A: *"Licensee is authorized only for those
   Permitted Uses expressly identified in the applicable Order Form, and no other Permitted Uses
   shall be implied."* §6 permits AI prompts *"provided that AI Prompt use is identified as a
   Permitted Use on the Order Form."* **It is not.** The headline paid feature pipes FMP data into
   Gemini. Also resolve §6's *"solely for Licensee's internal business purposes"* against reports
   generated for paying users.
4. **Name the external-display category.** "End-User Display Rights" is not one of Exhibit A's
   numbered Permitted Uses (1 Internal / 2 Access-Restricted External / 3 Public External).
5. **US-only pricing.** Every package is priced *"60+ Global Exchanges"* / *"global coverage"*;
   the app uses **zero** non-US symbols — no FX, no `.L`/`.TO`/`.HK`, no `exchange=`/`country=`.
6. **"Data Delay: EOD"** on Historical & Intraday — does that mean today's intraday bars are
   unavailable until after the close? Still unanswered, and it decides whether there is any
   intraday price at all.
7. **"Cycle Times"** — referenced 4×, defined 0× in 17 pages. Ask for the table.
8. **1,000 monthly unique users** on the display licence — §2.6 escalates fees automatically on
   exceeding and lowers them only at renewal. What is the next tier and its price?
9. **§9.3 deletion within 30 days** of *"all copies, caches, extracts and stored instances"* vs
   frozen report snapshots and user-exported PDFs that cannot be recalled. Needs a carve-out.
10. **Sub-licence warranty** for Market News (publisher IP) and Analyst Estimates (which
    aggregator — I/B/E/S, Zacks, FactSet?). The repo's own rule: *hold the incumbent to the same
    standard as a prospective replacement.*

## 7. Walk-away position

If it will not reach ~$600/mo with a monthly commitment and AI use licensed, **delay launch rather
than sign $13,245 against zero revenue.** The Premium key still works for development, and
Content Rights is the only App Store field gated on this. Switching vendors is not the alternative
— the research says it costs more and delivers less.

---

## Deliverable

Write the counter-email to `documents/legal/fmp-negotiation-reply.md` (append as Round 4),
ordered: term contradiction → mutual funds → licence gaps → the unanswered questions. Update
`documents/legal/fmp-order-form-checklist.md` with the package-level mapping now that the real
package names are known.

## Verification

- Confirm the ETF-only cut is safe: `grep -E '(etf|fund)' backend/app/integrations/fmp.py`
  returns only the three `etf/*` paths.
- Confirm crypto price path before promising CoinGecko: read `crypto_service.py:721-742` and
  `integrations/coingecko.py` for an existing price field.
- Do **not** answer App Store Content Rights until asks 3 and 4 are resolved in writing.
