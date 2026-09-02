# FMP Order Form — pre-signing checklist

Hold the Order Form against this. Generated from a full sweep of `backend/app/integrations/fmp.py`
(2026-08-25): **61 distinct REST paths** actually called, plus 2 WebSocket hosts.

⚠️ **Read the Order Form as a POSITIVE list.** It names what you get. It will not say "and we did
not remove X." Verify every line below is named or covered by a named dataset group.

⚠️ **ToS §4.3** fixes pricing for the initial term; **§1.4** says all sales are final, no refunds.
There is no "we tried it and it didn't work" exit. Month-to-month caps the loss at one month.

---

## 🔴 Tier 1 — if these are missing, do not sign

| Path | Why |
|---|---|
| `historical-price-eod/full` | 3M/6M/1Y/5Y/ALL charts, technical analysis, volatility σ, benchmarks. 11 callers |
| `historical-chart/{1min,5min,15min,30min,1hour}` | 1D + 1W charts. **1D defaults to `5min`** — a 15min-only grant breaks the default chart |
| `profile` | Most-called endpoint in the app (20 callers). Also the fallback for `price`, `change`, `marketCap`, `volAvg`, `beta` |
| `income-statement` · `balance-sheet-statement` · `cash-flow-statement` | Every fundamentals screen + the AI report |
| `search-symbol` · `search-name` | Ticker search. Without it users cannot find anything |

## 🟠 Tier 2 — a named feature dies without each

| Path | Feature lost |
|---|---|
| `ratios` · `ratios-ttm` · `key-metrics` · `key-metrics-ttm` | Health Check, valuation, industry benchmarks |
| `financial-growth` | Peer revenue-growth column (called raw, no wrapper — easy to miss on a list) |
| `revenue-product-segmentation` | Revenue breakdown screen |
| `shares-float` · `stock-peers` | Float stats; Related Tickers |
| `dividends` · `splits` | Dividend history; **splits stop the Whale tab reading a 10:1 split as a multi-billion-share buy** |
| `earnings` · `earnings-calendar` | Earnings timeline + **"Earnings Shockers"**, a marketed Home differentiator |
| `analyst-estimates` · `price-target-consensus` · `grades` | Wall Street targets, Future Forecast, Analyst Ratings. ⚠️ **Take all three or none** — a half-drop fabricates a confident "HOLD" on every stock |
| `news/stock` · `news/general-latest` · `news/crypto` | News tab, Updates feed, AI summary corpus |
| `institutional-ownership/*` (6 paths) | The whole Whale / 13F tab |
| `insider-trading/search` | Insider tab |
| `senate-latest` · `house-latest` · `senate-disclosure` · `house-disclosure` | Congressional trades — a marketed differentiator |
| `acquisition-of-beneficial-ownership` | SC 13D/G block in the AI report |
| `etf/info` · `etf/holdings` · `etf/sector-weightings` | ETF detail screen |
| `sp500-constituent` | ⚠️ **DOWNGRADED 2026-09-02.** It is *not* the benchmark universe builder — that is the static `backend/data/benchmark_universe.json`. `compute_and_persist_all_sectors` has **no caller**; `compute_all_benchmarks` is retired from scheduling and falls back to `_FALLBACK_SECTOR_TICKERS`. Safe to drop |
| `historical-market-capitalization` | Shareholder-yield card |

## 🟡 Tier 3 — confirm status, removable if priced

| Path | Note |
|---|---|
| `quote` · `batch-quote` | Being removed (real-time). Confirm what replaces them |
| `stock-price-change` | Quote-family. Whale return tile + report macro. Derivable from history |
| `sector-performance-snapshot` · `industry-performance-snapshot` · `biggest-gainers` · `biggest-losers` · `most-actives` | ⚠️ **He said these "were not in the original scope."** All five are called today (`fmp.py:732,827,850,859,868`). If not entitled they **403 in production** — settle this |
| `dowjones-constituent` · `nasdaq-constituent` | Row counts only, already hardcoded fallbacks. Safe to drop |
| `earning-call-transcript` · `-dates` | **Agreed dropped** |

## ⚪ Dead — never call these, do not pay for them

`company-outlook` · `sec_filings` · `social-sentiments/change` · `social-sentiments/historical` ·
`stock-news-sentiments-rss-feed` · `insider-trading/statistics` — zero callers, verified.

Also: **no mutual-fund endpoint has ever been called.** If a fund SKU appears, remove it.

---

## Symbol entitlements — ask explicitly

There is **no index, commodity or crypto endpoint in the codebase.** All three ride `quote` /
`batch-quote` / `historical-price-eod` / `historical-chart` with special symbols. Confirm these are
included and not separately priced:

- Indices: `^GSPC` `^IXIC` `^DJI` `^VIX` `^TNX`
- Commodities: `GCUSD` `SIUSD` `CLUSD` `NGUSD` `HGUSD` `PLUSD` `PAUSD` `ZWUSD` `ZCUSD` `ZSUSD` `KCUSD` `SBUSD` `CCUSD` `CTUSD`
- Crypto: `BTCUSD`-style pairs

**No non-US symbols are used anywhere** — no FX pairs, no `.L`/`.TO`/`.HK` suffixes, no
`exchange=`/`country=` params. Do not pay for international coverage.

---

## Package-level mapping (the quote sells 12 packages, not endpoints)

Added 2026-09-02, once the real Order Form arrived. The endpoint tiers above still govern *what
must survive*; this table is what you actually tick on the Order Form.

| Package | List | Covers | Keep? |
|---|---:|---|---|
| Fundamentals | $3,000 | income/balance/cash-flow, ratios(-ttm), key-metrics(-ttm), financial-growth, revenue-product-segmentation | ✅ |
| Company Information | $2,000 | `profile` (20 callers), shares-float, stock-peers, historical-market-cap | ✅ |
| **Historical and Intraday** | $2,500 | EOD + intraday 1min–4hr. ⚠️ marked **"Data Delay: EOD"** — unresolved | ✅ |
| Analyst Estimates | $2,000 | analyst-estimates, price-target-consensus, grades | ✅ |
| Earnings Calendar | $1,500 | earnings, earnings-calendar, dividends, splits | ✅ |
| Market News | $2,000 | news/stock, news/general-latest, **news/crypto** | ✅ |
| Institutional Ownership | $2,000 | the 6 `institutional-ownership/*` paths | ✅ |
| Insider & Senate | $2,000 | insider-trading/search, senate/house latest + disclosure, acquisition-of-beneficial-ownership | ✅ |
| **ETF** | $3,000 | `etf/{info,holdings,sector-weightings}` — **no price endpoints; ETF prices come from Historical & Intraday** | ✅ keep — see below |
| **Mutual Funds Holdings** | $2,500 | nothing the app calls | ❌ **drop** |
| **Indexes** | $3,000 | `^`-symbols + `*-constituent` | ⚠️ droppable via SPY/QQQ/DIA |
| **Commodities** | $2,500 | the 14 USD codes | ⚠️ droppable via GLD/USO |
| Display licence | $1,500 | 1,000 monthly unique users | ✅ required |

**If only one of ETF / Indexes can go, drop Indexes.** `ETFDetailView` is reachable from search,
watchlist, whale 13F, trade groups and push; `IndexDetailView` is reachable from 3 Home tiles and
**not from search at all**. SPY tracks `^GSPC` within 0.01%; ETF holdings have no substitute at any
price. And dropping ETF ships a false green "Well Diversified" badge
(`ETFDetailModels.swift:151` — `weight=0 → .low`) until its empty state is fixed.

⚠️ **Commodities:** 7 of 14 symbols (wheat, corn, soybeans, coffee, sugar, cocoa, cotton) have
**no navigation path from anywhere in the app** — already orphaned.

⚠️ **Crypto is in no package.** `BTCUSD` price must move to CoinGecko, which already returns
`current_price` (`integrations/coingecko.py:351`). Crypto *news* IS covered, inside Market News.

⚠️ **Do not drop Indexes + Commodities without editing `_PULSE_SYMBOLS`** — Market Pulse would fall
back to `BTCUSD` alone.

## Commercial terms — must be IN the Order Form, not just in email

- [ ] **Month-to-month**, with notice period and what happens to access on cancellation
- [ ] **Bandwidth**: the number, what happens at the cap (throttle / overage / hard stop), the overage rate, and an 80% alert
- [ ] **Data handling (a)(b)(c)** — caching to 24h; figures frozen into saved reports indefinitely; users exporting PDFs that leave your infrastructure permanently
- [ ] **ToS §6.3 carve-out** — §6.3 requires deleting all data *including cached* on termination plus signing Exhibit A. That is in direct conflict with (c). Get it written that reports and PDFs already delivered to users may remain after termination
- [ ] 🔴 **Display rights** — written confirmation that the Order Form itself grants end-user display rights for the remaining datasets and **no separate Data Display and Licensing Agreement is required**. *The App Store Content Rights answer depends on this. Do not answer it in ASC until this is in writing.*

## Before billing starts

Ask whether the new entitlement can be enabled for a few days first. A paper list will not catch
"historical-chart is included, but only 15min and above." Running the real app against the real key
is the only check that finds a 403 you did not predict.
