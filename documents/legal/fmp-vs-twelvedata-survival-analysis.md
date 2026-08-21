<!-- Generated 2026-08-21 by a 10-agent analysis across the codebase + vendor docs. -->

> ⚠️ **UNVERIFIED.** This document was produced by a synthesis agent, and the adversarial
> critique step that was supposed to check it **failed** (session usage limit) — so no second
> pass has challenged its verdicts. Its own §0 lists three unresolved questions, one of which
> (does Twelve Data expose sector/industry?) would change the conclusion if answered the other
> way. Treat the direction as sound and individual cells as claims to spot-check before acting.

# FMP Commercial Licence vs. Twelve Data Migration â Decision Document

**Prepared:** 2026-08-21 Â· **App:** Caydex (AI Value Investor) Â· **Scope:** every FMP-dependent user-visible surface in the codebase, assessed against verified Twelve Data coverage.

**Read this first:** three things in the source research were *not resolved*, and one of them determines whether a migration is even theoretically possible. They are listed in Â§0 and repeated in-line wherever they affect a verdict. Do not sign or cancel anything until Â§0 items 1 and 2 are settled.

---

## 0. Open questions that block a clean decision

| # | Question | Why it matters | Status in the research |
|---|---|---|---|
| **1** | **Does Twelve Data have a company-profile endpoint exposing `sector` and `industry`?** | `/stable/profile` is consumed by **25 backend modules across ~30 call sites** â it is the app's classification spine. Sector+industry drive *every* benchmark comparison, the whale/portfolio/watchlist enrichment, moat scoring, IP intel and trending sectors. | **NOT RESEARCHED â neither confirmed nor denied.** The fundamentals audit enumerated `/statistics`, `/dividends`, `/splits`, `/earnings`, statements and analysis endpoints, and did *not* list a profile endpoint. Partial substitutes exist (`/statistics` â market_cap, beta, shares outstanding, float, % held by insiders/institutions; `/stocks` catalog â name, exchange, country, type; `/logo`). **Sector, industry, description, CEO, employees, IPO date and averageVolume are unaccounted for.** If sector/industry is genuinely absent, the entire benchmark differentiator dies and the migration is a non-starter. |
| **2** | **FMP's actual commercial (Build / Enterprise) price** | It is the number the whole decision turns on. | **UNKNOWN â quote-only.** Only the Personal Use column is publicly extractable ($0 / $22 / $59 / $149 per month, billed annually). FMP's terms state that *displaying or redistributing* FMP data requires a specific Data Display and Licensing Agreement â so a shipping consumer app likely needs this regardless of tier. |
| **3** | **Does Twelve Data expose a stock-peers equivalent?** | Feeds Related Tickers, Related ETFs, report peer set, competitor validation. | **NOT RESEARCHED.** Curated fallback tables already exist in `etf_service.py` and would absorb some of the loss. |

Additional unresolved items (each flagged again in the matrix): TD index quotes (Â§âComing soon / contact salesâ), `/market_cap` lookback depth, whether `/earnings_calendar` carries EPS estimate/actual/surprise, the multi-symbol cap (~120, unverified), TD WebSocket coverage for commodities and indices, Pro-tier historical depth on statements, and whether TD's commodity catalog contains all 16 symbols the app ships.

**Not at stake in this decision:** CoinGecko (crypto supply/FDV/volume), FRED (macro), FINRA (short interest), ApeWisdom (social mentions) and Alternative.me (crypto F&G) are separate integrations and are unaffected either way.

---

## 1. Survival matrix

Three tables, ordered by severity. Verdicts are against a **pure Twelve Data swap** (no second vendor, no self-sourcing).

### 1a. DIES â no Twelve Data path at any tier

| Feature | FMP endpoints it needs | Twelve Data covers? | Verdict | What it would take to keep it |
|---|---|---|---|---|
| Whales tab â 13F fund/investor profiles (portfolio value, sector donut, Current Picks, Recent Trades, annual-return tile) | `institutional-ownership/dates`, `/extract`, `/holder-industry-breakdown`, `/holder-performance-summary` | **No.** `/institutional_holders` (Ultra, 1,500 cr/symbol) returns 10 rows, one quarter, no CIK, no prior-quarter position, **no reverse lookup** â you cannot ask "what does Berkshire hold". 13F appears 0 times in TD's docs. | **DIES** | SEC EDGAR 13F Data Sets + custom diff/analytics engine (6â10 wks, see Â§3), or keep FMP Ultimate, or Kaleidoscope (quote-only) |
| Whales tab â congressional (politician) profiles, trade groups, STOCK Act ranges | `senate-latest`, `house-latest` | **No.** `congress`/`senate`/`politician` appear **0 times** in TD's docs. No political data product exists. | **DIES** | FMP (already have it), or Quiver **Commercial tier only** (self-serve tiers are explicitly marked *"No Commercial Use Rights"*), or House ZIP + Senate scrape (4â8 wks, fragile) |
| Whale hydration background job (writes `whales`, `whale_holdings`, `whale_trades`, `whale_alerts`, `whale_sector_allocations`, `whale_trade_groups`) | all 6 above | No | **DIES** | Same as above. Note the failure mode: downstream surfaces go **stale, then silently empty**, with no error surfacing anywhere |
| Home App-Exclusive Signals â **Congressional Buys** card | `senate-latest`, `house-latest` | No | **DIES** | Same as congressional above |
| Home App-Exclusive Signals â **Whale Accumulation** card | (reads hydration output) | No | **DIES** | Same as 13F above |
| Home signal drill-down â "who bought this ticker" sheet (Pro/Max-gated) | `senate-latest`, `house-latest`, `institutional-ownership/extract` | No | **DIES** | Same |
| Tracking > Assets alerts â "Whales Bought / Sold" | (reads `whale_trades`) | No | **DIES** | Same |
| Push â daily **Smart Money** digest, whale/congress leg | `institutional-ownership/extract`, `senate-latest`, `house-latest` | No | **DIES** | Same |
| Push â daily **Smart Money** digest, insider leg | `insider-trading/search` | **No, in practice.** `/insider_transactions` (Pro, 200 cr) has **no transaction code, no signed shares, no price, no security title**. Direction lives only in a free-text `description`, and **50% of rows (177/354 on AAPL) have it empty**. Zero open-market Purchases classifiable in the entire AAPL sample. | **DIES** | SEC `form345` Insider Transactions Data Sets + live Form 4 XML polling (2â4 wks), or FMP |
| Whale alert banner (Whales tab) | (reads `whale_alerts`) | No | **DIES** | Same as 13F/congress |
| Holders tab â Smart Money **Insiders** (12-mo buy/sell flow bars, net-flow verdict, RSU/option filtering) | `insider-trading/search` | No â see above. Buy/sell flow is **arithmetically impossible** on TD's feed. | **DIES** | SEC Form 4 self-sourcing, or FMP |
| Holders tab â Smart Money **Institutions** (8-quarter 13F net-share flow, split-adjusted) | `institutional-ownership/symbol-positions-summary` | No â TD gives one quarter, no history, no deltas | **DIES** | SEC 13F Data Sets + your own quarter diff + split adjustment |
| Holders tab â Smart Money **Congress** tab + congressional Recent Activities rows | `house-disclosure`, `senate-disclosure`, `house-latest`, `senate-latest` | No | **DIES** | FMP or Quiver Commercial |
| Holders tab â **Recent Activities** unified feed (13F deltas + Form 4 + congress) | all 7 ownership endpoints | No (all three legs) | **DIES** | All three self-sourcing projects, or FMP |
| Watchlist alerts â "Insider Bought / Sold" cards | `insider-trading/search` | No | **DIES** | SEC Form 4, or FMP |
| AI report â **Insider Activity** section (flow chart, transactions table, insider vital score) | `insider-trading/search` | No | **DIES** | SEC Form 4, or FMP |
| AI report â **Key Management** founder/major-holder beneficial ownership | `acquisition-of-beneficial-ownership` (SC 13D/G) | **No.** Only path in the app exposing a founder's full stake (the documented ORCL/Ellison 1.157B / 43% case). Form 4 `securitiesOwned` is not a substitute â it's one account's post-trade balance. | **DIES** | SC 13D/G parsing is unstructured HTML/text â high effort, low reliability. Realistically **buy** |
| AI report â **Company Guidance** (raised/maintained/lowered + verbatim CFO/CEO quote) | `earning-call-transcript-dates`, `earning-call-transcript` | **No.** "transcript" appears **0 times** in TD's docs and in all 224 endpoint paths. | **DIES** | FMP Ultimate, Kaleidoscope (quote), or Finnhub **as a paid add-on above $3,500/mo**. **No free source exists.** |
| AI report Moat radar â **Switching Costs** (NRR/NDR) and **Network Effects** (user counts) pillars | same transcript pair | No | **DIES** (falls back to ungrounded Gemini) | Same as above |
| "How {TICKER} Makes Money" revenue-breakdown card (Financials tab) | `revenue-product-segmentation` | **No.** Regex over TD's full OpenAPI spec for `product_(revenue\|segment\|sales)\|revenue_by\|segment_revenue\|business_segment` returns **zero hits**. Geography exists only as three scalars (`foreign_sales`/`domestic_sales`) inside `/cash_flow/consolidated` (Ultra+) â not a substitute. | **DIES** | **No vendor in the research covers product segmentation except FMP.** This dataset is genuinely FMP-distinctive here |
| AI report â **Revenue Engine** segments + `revenue_vital` (1 of 10 scored dimensions) | `revenue-product-segmentation` | No | **DIES** | Same. Scorer renormalizes the vital out, so the persona score silently shifts |
| Per-ticker **News tab** (stock detail) + AI article enrichment | `news/stock` | **No.** There is no news endpoint. `/press_releases` (free) is issuer-published PR wire only â no third-party publishers, no aggregation, no attribution. | **DIES** | FMP (11 news endpoints, from Starter $22/mo) is the cheapest real option. Finnhub free tier is US company news, 1-yr history â but **"Personal Use. Terms apply."** |
| **Market news feed** â Updates tab default scope + Search "Latest News" strip | `news/general-latest`, `news/stock` | No | **DIES** | Same |
| **Crypto News tab** | `news/crypto` | No | **DIES** | Same |
| **Index News tab** (S&P/Dow/Nasdaq) | `news/stock` + constituent lists | No â **dies twice**: no news endpoint AND no constituents endpoint | **DIES** | Same, plus a constituent source |
| **ETF News tab** | `news/stock` | No | **DIES** | Same |
| AI chat news grounding + research agent's `fetch_more_news` tool | `news/stock` | No | **DIES** | Same |
| Index Detail â **"Constituents"** Key Statistics row + index profile count | `sp500-constituent`, `dowjones-constituent`, `nasdaq-constituent` | **No.** TD's complete market-data section is exactly six endpoints (`/time_series`, `/time_series/cross`, `/quote`, `/price`, `/eod`, `/market_movers/{market}`). No `/indices` catalog, no constituents endpoint anywhere in the 187-path OpenAPI spec. **No workaround inside their API.** | **DIES** | Low actual cost: the list is used only for `len()`, and a hardcoded `_INDEX_PROFILES` fallback (503/3000/30) already exists. **Ship the fallback.** Index membership is also a licensed dataset (S&P DJI) â do not scrape it for a commercial app |
| **Industry** performance snapshot â widget "moved with the Consumer Electronics industry" catalyst | `industry-performance-snapshot` | No â TD has no sector/industry performance endpoint at all | **DIES** (industry level) | No substitute. Sector level *is* recoverable (see 1b) |
| Home "Heavy Traffic" universe backbone â `most-actives` | `most-actives` | **No.** `/market_movers` `direction` has exactly two values: `gainers`, `losers`. The strings "most active", "most_active", "actives" return **zero matches** across TD's entire docs corpus. You cannot rank the universe by volume through their API. | **DIES** (as a universe source; card degrades â see 1b) | No substitute |

**Subtotal: ~30 features die outright.**

---

### 1b. DEGRADES â technically reachable, but shallower, slower, materially more expensive, or requiring substantial rebuild

| Feature | FMP endpoints | Twelve Data covers? | Verdict | What it would take |
|---|---|---|---|---|
| Home **Market Pulse** strip (S&P 500 / Nasdaq / Dow tiles) | `batch-quote`, `quote` on `^GSPC`/`^IXIC`/`^DJI` | **Uncertain, leaning no.** TD's `/indices` page carries a **"Coming soon"** badge with a *Request / Contact sales* CTA (contrast `/commodities`, which says "Start now"). `symbol_search` returns **no `instrument_type=Index` rows** for SPX, GSPC, NDX, DJI or IXIC â SPX resolves to *Spirax Group plc*, DJI to *Global X Dow 30 ETF*. The plumbing exists (`Index` is a valid `instrument_type`; `/exchanges?type=index` returns data), so this is uncertain rather than a flat no. | **DEGRADES** | Substitute ETF proxies SPY/QQQ/DIA (these **do** work on TD). Visible change: users see an ETF price (~$6xx) instead of an index level (6,xxx), and DIA vs DJI diverge on dividends. **Do not plan on index quotes without written confirmation from TD sales** |
| Index Detail screen (level, Key Statistics, Performance, Valuation/Sector Rotation/Macro snapshots) | `quote`, `historical-price-eod/full`, constituents, `sector-performance-snapshot` | Partial at best â see above | **DEGRADES heavily / at risk of DYING** | ETF-proxy rework of `IndexDetailViewModel` + `index_service.py`, hardcoded constituent counts, sector performance rebuilt from 11 SPDR quotes |
| Home-screen widget **market band** (`^GSPC`/`^IXIC`/`^DJI` in one `batch-quote`) | `batch-quote` | Same index problem | **DEGRADES** | ETF proxies. The band labels are already server-supplied (`widget_movers_service.py:110-117`) so no app update is needed to change them |
| Home **"Today's Top Movers"** scanner card | `biggest-gainers`, `biggest-losers`, `most-actives`, `profile` (batch) | Partial. `/market_movers/{stocks\|etf}` gainers/losers exists â **Pro tier, 100 credits per request**. The quality gate (`isEtf`/`isFund`, marketCap â¥ $250M, averageVolume) currently comes from `profile` batch. | **DEGRADES** | Depends entirely on **Â§0 item 1**. If `/statistics` (50 cr/symbol, **not batchable**) is the substitute, a 200-symbol universe costs **10,000 credits** â ~4 min of your entire Ultra budget per build, and **infeasible at Pro** (16 min against a 20-min cache) |
| Home **"Heavy Traffic"** (RVOL) card | `most-actives` + `profile` batch | `most-actives` absent; RVOL itself computable from `/quote` (`volume` + `average_volume`) | **DEGRADES** | Universe narrows to gainers/losers only â exactly the starvation the round-robin at `home_dashboard_service.py:1188-1200` was written to prevent. **Accuracy caveat:** TD's own support article states the default US feed is sourced from venues representing **~5% of total US consolidated trading volume**. An RVOL computed off a 5%-of-consolidated feed is not comparable to a SIP-based one |
| Holders tab â **Shareholder Breakdown** (insiders % / institutions % / Top-10 Institutions) | `institutional-ownership/extract-analytics/holder`, `symbol-positions-summary`, `insider-trading/search` | Partial. `/institutional_holders` gives top-10 + `percent_held` (Ultra, **1,500 cr/symbol**, ~1.7 symbols/min); `/statistics` gives `percent_held_by_insiders` / `_by_institutions` | **DEGRADES (severely)** | Rebuild against a 10-row single-quarter snapshot with no category field. Cost makes any fan-out impossible; on-demand + 24h cache only |
| Overview snapshot #5 â **Insiders & Ownership** | same three | Ownership % rows survive via `/statistics`; **Insider Activity 12M** and **Institutional Activity 12M** require transactions/deltas | **DEGRADES** | Two of five metrics die; the radar spoke shifts |
| AI report â **Hidden Market Signals** module | congress endpoints + holders | Congress leg dies. Short-interest leg is FINRA (unaffected) | **DEGRADES** | Module returns None when both halves are absent â iOS hides it silently, not an error |
| AI report â **Fundamentals & Growth** 2Ã2 (Profitability / Growth / Valuation / Health) | `income-statement`, `balance-sheet`, `cash-flow`, `key-metrics`, `ratios`, `ratios-ttm`, `key-metrics-ttm`, `revenue-product-segmentation` | Statements: yes (Pro, **100 cr each**; full history requires Ultra). **`ratios` has no equivalent** â no `/ratios`, `/financial_ratios` or `/key_metrics` path exists in TD's 187-path spec. `/statistics` (50 cr) is a **point-in-time snapshot with no `period` or date parameters** â no historical ratio series | **DEGRADES (major rebuild)** | Recompute every ratio from raw statements. Historical per-year ratios additionally need `/market_cap` history (Ultra). Absent from TD anywhere: **quick ratio, cash ratio, interest coverage, debt/EBITDA, asset/inventory/receivables turnover, DSO, ROIC** |
| AI report â **persona quality score (0-100)**, rating, recommendation, fair-value verdict | `ratios` (annual) is the **SOLE source of 13 scoring inputs** | Same as above | **DEGRADES (major rebuild)** | All 13 are recomputable in principle (margins/ROE/ROA/D-E/current ratio from statements; PE/PB/PS/P-FCF/EV-EBITDA from `/statistics` snapshot or price Ã share count; **interest coverage must be derived from the income statement â TD has no such field anywhere**). â ï¸ Note the existing failure mode you'd inherit: an empty `ratios` leg sets all 13 to `None` **silently, unrefunded**, the score falls back to the LLM's own self-rating, and the report is still charged 20 credits and cached |
| Growth section card + chart (EPS/Revenue/NI/OpInc/FCF, YoY+QoQ) | `income-statement`, `cash-flow-statement` | Yes, at 100 cr each Ã 4 legs (annual + quarterly, income + cash flow) = **400 cr/ticker** vs FMP's 5-call fan-out | **DEGRADES (cost)** | Straight rewrite; math is already in-app |
| Profit Power card + report Profitability drill-down | `income-statement`, `cash-flow-statement` | Same | **DEGRADES (cost)** | Same |
| Health Check card (D/E, Current Ratio, ROE, P/E, Altman Z) | `ratios-ttm`, `key-metrics-ttm`, `balance-sheet`, `income-statement` | `/statistics` carries `total_debt_to_equity_mrq`, `current_ratio_mrq`, `return_on_equity_ttm`, `trailing_pe`. Altman Z needs TTM EBIT/Revenue from 4 quarterly income statements | **DEGRADES** | Rebuild; Altman Z path is fine |
| Overview tab **Snapshots** (5-spoke radar + star rows) | `key-metrics-ttm`, `ratios-ttm`, `key-metrics`, `ratios`, all three statements | Same rebuild | **DEGRADES** | Widest blast radius in fundamentals â 4 snapshot services + the report's 4 cards share this substrate |
| **"vs sector / vs industry"** benchmarks â every asterisk, band and percentile app-wide | 10 FMP calls per constituent (`income`, `cash-flow`, `ratios`, `key-metrics`, `balance` Ã annual+quarterly) | Rebuildable â **if** Â§0 item 1 resolves favourably | **DEGRADES â AT RISK OF DYING** | ~650 cr/constituent (6 statement calls + statistics). **S&P 500 universe = 325,000 credits â 126 min at Ultra's 2,584 cr/min.** Failure mode is graceful but silent: benchmarks just vanish and the cards lose the comparison the app is positioned on |
| AI report â **Industry & Competitive Moat** pillars + peer medians | `income-statement`, `balance-sheet`, `ratios`, `key-metrics` + transcripts | Statements yes; ratios rebuilt; transcript pillars die | **DEGRADES** | â ï¸ **The industry-moat recompute is not affordable at any published TD tier.** It is already ~140k FMP calls / 60â90 min; at ~650 cr per ticker-pass that is on the order of **10M+ credits â 100+ hours** at Ultra |
| AI report â TAM figure + `tam_source_quote` | transcripts (tier 1) â FRED/BEA proxy (tier 2) | Tier 1 dies | **DEGRADES** | FRED proxy already implemented and unaffected; TAM stays 0.0 and iOS hides the column when neither resolves |
| Earnings card (quarterly EPS/Revenue actual-vs-estimate, beat/miss, EPSâRevenue toggle) | `earnings` (per-symbol), `analyst-estimates` | `/earnings` (Grow, 20 cr) is **EPS only** â `{date, time, eps_estimate, eps_actual, difference, surprise_prc}`. **No revenue actual, no revenue estimate.** | **DEGRADES** | Revenue actual from quarterly `/income_statement` (100 cr); revenue estimate from `/revenue_estimate` (**Ultra**, 20 cr). â ï¸ Do not lose the bandwidth win documented at `earnings_service.py:471-484` â the per-symbol endpoint replaced a global calendar fan-out that was ~99% of the app's FMP bandwidth (4.3 GB/mo) |
| Earnings **push notifications** (upcoming + result) | `earnings-calendar` (**ONE market-wide call per day**) | `/earnings_calendar` (Grow, 40 cr) exists with `start_date`/`end_date`/`exchange`/`country` | **DEGRADES â pending an unknown** | â ï¸ **UNCERTAIN:** the research did not enumerate `/earnings_calendar`'s response fields. If it lacks `eps_estimate`/`eps_actual`, the surprise pass becomes per-symbol: 60 symbols Ã 20 cr = 1,200 cr/day instead of 40. Verify before designing |
| Home **"Earnings Shockers"** signal card | `earnings-calendar` + `batch-quote` | Same uncertainty | **DEGRADES** | Same |
| Watchlist **Analyst Ratings** alert card | `grades` per watchlist ticker | `/analyst_ratings/us_equities` (**Ultra, 200 cr/symbol**) | **DEGRADES (cost)** | ~12 tickers/min at Ultra. This is the only unbounded-by-watchlist-size analyst call in the app |
| ETF Detail screen â NAV, AUM, expense ratio, holdings count, index tracked, top holdings, sector mix, concentration | `etf/info`, `etf/holdings`, `etf/sector-weightings` | `/etfs/world/{summary\|performance\|risk\|composition}` exist â **Ultra+/Enterprise+ at 200â800 credits per request** | **DEGRADES (severe cost)** | Note FMP's own `etf/info` is already plan-gated and the app already falls through to a static `_ETF_REFERENCE` table for popular ETFs, so part of this screen is *already* partly static |
| ETF Holdings & Risk section | `etf/info`, `etf/holdings` | Same | **DEGRADES** | Same |
| ETF Profile section | `etf/info` + `profile` fallback | Same + Â§0 item 1 | **DEGRADES** | Same |
| Sector Performance snapshot (Index Detail) + widget "moved with the sector" | `sector-performance-snapshot` | No endpoint â **but** the app already ships the workaround: `_sector_perf_from_etfs` maps 11 SPDR ETFs (XLK/XLF/XLV/â¦) to sector names | **DEGRADES â recoverable** | Promote the existing ETF fallback to the primary path: **11 credits per snapshot** on TD. Cheap. Industry level (see 1a) has no equivalent |
| Related tickers / Related ETFs / report peer set / competitor hallucination guard | `stock-peers` + `profile` | **Â§0 item 3 â not researched** | **UNCERTAIN â likely DEGRADES** | Curated `_RELATED_ETFS` table already exists. The competitor validation guard (reject LLM-proposed peers with `mktCap <= 0`) can be rebuilt on `/statistics.market_capitalization` |
| Updates "Explain the move" | `historical-price-eod/full` (Ï) + news (catalyst) | Ï survives; catalyst text dies with news | **DEGRADES** | Volatility tiering keeps working; the grounded catalyst reverts to a bare percentage |
| Watchlist / portfolio classification enrichment (sector, industry, country, marketCap, beta) â Portfolio Insights HHI diversification | `profile` | **Â§0 item 1** | **DEGRADES â AT RISK** | If sector/industry is unavailable, diversification scoring is meaningless |
| Trending Sectors (Research tab) | `profile` (batch, bucket by sector) | **Â§0 item 1** | **AT RISK** | Dies without sector labels |
| IP / patent intelligence (Intangible Assets pillar) | `profile` (sector/industry relevance gate, employees, companyName) | **Â§0 item 1** | **AT RISK** | The relevance gate is sector/industry-driven |
| Sector aggregates (market-cap-weighted composites) | `profile` (`mktCap` weight) | `/statistics.market_capitalization` covers the weight; sector label is Â§0 item 1 | **DEGRADES** | â ï¸ Note the existing fragility: `sector_aggregates_service.py:251` reads the legacy `mktCap` key and only works because `_normalize_profile` back-aliases it |

**Subtotal: ~30 features degrade, several of them materially.**

---

### 1c. SURVIVES â direct or near-direct Twelve Data equivalent

| Feature | FMP endpoints | Twelve Data covers? | Verdict | What it would take |
|---|---|---|---|---|
| Real-time price header on stock detail (price, change, %, Open/Prev Close/High/Low/Volume/Avg Volume/52-wk) | `quote` | **Yes** â `/quote`, 1 cr/symbol, **on every plan including free Basic**. Returns open/high/low/close/volume, previous_close, change, percent_change, average_volume, is_market_open, 52-week block | **SURVIVES** | Field remap. Note the existing `changes_percentage` alias hack (`stocks.py:743-745`) becomes unnecessary â TD uses `percent_change`. â ï¸ 5%-of-consolidated-volume caveat applies to `volume` |
| TTM EPS / P-E in the quote header | `income-statement` (quarter, 4) | `/statistics.valuations_metrics.trailing_pe` directly, or recompute from quarterly income (100 cr) | **SURVIVES** | Simpler than today. This endpoint is currently uncached and re-fetches on every poll â worth fixing either way |
| Main price chart â all 7 ranges, all intervals, candlestick/line, extended hours, MA/RSI/MACD overlays | `historical-chart/{1min,5min,15min,30min,1hour}`, `historical-price-eod/full` | **Yes** â `/time_series`, 1 cr/symbol, outputsize 1â5000. Interval enum: `1min, 5min, 15min, 30min, 45min, 1h, 2h, 4h, 8h, 1day, 1week, 1month` | **SURVIVES** | Rename `1hour`â`1h`, `4hour`â`4h` (both already dead in `resolve_interval` anyway). â ï¸ **US intraday history starts 2022-12-15** (daily goes back to 1980) â fine for 1D/1W, silently short for anything deeper. `_fetch_all_daily`'s 5000-point pagination maps cleanly to `outputsize` |
| Extended-hours toggle | `historical-chart` + `extended_hours` | `prepost=true` on `/quote` and `/time_series` â **Pro/Venture+** | **SURVIVES at Pro** | Tier requirement, not a code problem |
| Per-asset-class session window (24h crypto/gold vs 09:30-16:00 equity) | `historical-chart/5min`, `/1hour` | Yes (logic is app-side in `asset_class.py`) | **SURVIVES** | Rewrite the hardcoded FMP USD-suffix set (`GCUSD`, `CLUSD`, â¦) to TD's symbology |
| Commodity Detail screen (14 commodities, unit suffixes, all 7 ranges, Key Stats, Performance, siblings) | `quote` + `historical-*` on USD-suffixed codes | **Yes** â commodities are first-class symbols through `/quote`, `/price`, `/time_series`. Catalog verified live: XAU/USD, XAG, WTI/USD, HG1, URALS/USD, with categories Precious Metal / Industrial Metal / Energy / Agricultural / Livestock. **Grow+ tier.** Real-time, 24/7, daily from 1980, intraday from 2020-01-09 | **SURVIVES** | **Symbol remapping layer required** â GCUSDâXAU/USD, CLUSDâWTI/USD, etc. â ï¸ **UNCERTAIN:** the research did not enumerate TD's catalog against your specific 16 (Natural Gas, Platinum, Palladium, Wheat, Corn, Soybeans, Coffee, Sugar, Cocoa, Cotton). Verify each |
| Live streaming price WebSocket (5 detail screens) | FMP stock + crypto sockets, `quote` for prev-close reference | **Full WebSocket at Pro/Venture+** | **SURVIVES (probably)** | â ï¸ **UNCERTAIN:** TD WS coverage for commodities and indices was not verified. Note this is also **currently unproven on FMP** â the local key has no WS entitlement per `project_detail_screens_deep_check_2026_08` |
| Batch quotes â watchlist strips, Updates tabs, portfolio insights, price alerts, tracking, peer quotes | `batch-quote` | **Yes** â comma-separated `symbol` on `/quote`, `/price`, `/time_series`, `/eod`, plus a true mixed-endpoint `POST /batch` with per-request error isolation | **SURVIVES** | â ï¸ Batching saves a round trip but **saves zero credits** â each symbol still bills 1. And the cap is **~120 symbols (unverified)** vs FMP's 300 chunk size; a 414 "Parameter Too Long" error is documented. Re-chunk before relying on a number |
| Price alerts (create + recurring evaluation) | `batch-quote` | Yes | **SURVIVES** | Keep the creation-time baseline seeding â it's load-bearing |
| Holdings sparklines (Assets/Tracking tab) | `historical-chart/5min`, `batch-quote` | Yes | **SURVIVES** | Session-span math is app-side |
| Technical Analysis gauge + detail sheet (18 indicators, daily + weekly, pivots, Fibonacci, S/R) | `historical-price-eod/full` (~600 days) | **Yes** â `/time_series?interval=1day`. TD also ships native technical indicators on the free Basic tier | **SURVIVES** | â ï¸ Called with `^GSPC` and `GCUSD` by the Index/Commodity Analysis tabs â index symbols are the Â§0 problem, commodity symbols need remapping |
| AI report Price Action card (hourly close switch on a large move) | `historical-chart/1hour`, `quote`, `historical-price-eod/full` | Yes (`interval=1h`) | **SURVIVES** | Intraday depth limit is irrelevant here (recent windows only) |
| Updates "Explain the move" volatility Ï precompute | `historical-price-eod/full` | Yes | **SURVIVES** | Straight swap |
| Universal ticker/crypto search (Search tab, add-to-watchlist, holding picker, report target picker, follow-a-ticker) | `search-symbol`, `search-name` | **Yes** â `/symbol_search` (used in the research to probe SPX/DJI). outputsize caps at 120 | **SURVIVES** | All the interesting logic (when-issued/warrant dedup, crypto-shadowing guard, per-row try/except, overfetch Ã3) is app-side and portable |
| Company-name search fallback ("apple" â AAPL) | `search-name` | `/symbol_search` matches names natively | **SURVIVES** | Likely simpler â one endpoint instead of two |
| Signal of Confidence card + report Capital Allocation (per-quarter buyback + dividend yield at that quarter's market cap) | `historical-market-capitalization`, `cash-flow`, `income`, `dividends`, `earnings` | **Yes** â `/market_cap` history (**Ultra**, 5 cr) returns a dated `market_cap[]{date, value}` series; `/dividends` (Grow, 20 cr) | **SURVIVES at Ultra** | â ï¸ **UNCERTAIN:** `/market_cap` documents **no maximum lookback** and defaults `outputsize` to **10 records**. This card needs ~6 years of quarter-ends. Pagination behaviour at scale is undocumented â verify before committing. The point-in-time treatment is the differentiator here; the degraded path (scale every quarter by today's cap) is what you're explicitly avoiding |
| ETF Dividend History screen (100 payments) + ETF detail dividend snapshot | `dividends` | `/dividends` (Grow, 20 cr), `range` last/next/1mâ5y/full | **SURVIVES** | Field remap |
| Analyst Ratings card (consensus badge, distribution, low/avg/high targets + upside, momentum bars, Upgrades/Downgrades list) | `grades`, `price-target-consensus` | **Yes, at Ultra only.** `/analyst_ratings/us_equities` (200 cr) returns date, firm, analyst_name, rating_change, rating_current, rating_prior, price_target_current/prior; `/price_target` (75 cr) returns high/median/low/average/current; `/recommendations` (100 cr) returns Strong Buyâ¦Sell counts + 0-10 score | **SURVIVES at Ultra** | Good shape match. The 24-month recency window and `has_coverage=False` honesty guard stay app-side. â ï¸ US equities only on the 200-cr endpoint |
| AI report **Wall Street Consensus** card + Wall Street vital score | `grades`, `price-target-consensus` | Same | **SURVIVES at Ultra** | Same |
| Widget "why it moved" â analyst-action cause tag | `grades` (headline ticker only) | `/analyst_ratings/us_equities`, 200 cr for 1 symbol | **SURVIVES** | The deliberate 1-call-not-200 scoping is what makes this affordable |
| Widget "why it moved" â earnings cause tag | `earnings-calendar` | `/earnings_calendar` (40 cr), hourly-cached | **SURVIVES** (subject to the field uncertainty above) | Keep the `earnings_available` flag â a 429 must not read as a checked negative |
| Watchlist **Earnings Alert** card (next 14 days) | `earnings-calendar` | `/earnings_calendar` with `start_date`/`end_date` | **SURVIVES** | Straight swap |
| "E" earnings markers on the price chart | `earnings` | `/earnings` (20 cr) â dates included | **SURVIVES** | Straight swap |
| AI report **Future Forecast** (gapless actualsâconsensus, analyst counts, CAGR/EPS-growth headline) | `analyst-estimates`, `earnings` | **Yes, at Ultra only.** `/earnings_estimate` (EPS avg/low/high + analyst count), `/revenue_estimate`, `/growth_estimates` â 20 cr each | **SURVIVES at Ultra** | Three endpoints instead of one. The 10-year pull for the off-screen prior-FY anchor maps to `outputsize` |
| AI report earnings beat/miss track record ("Beat 6 of 8" + surprise chips) | `earnings`, `analyst-estimates` | `/earnings` carries `difference` and `surprise_prc` natively | **SURVIVES** | Simpler than today |
| Forward P/E (Overview valuation block) | `analyst-estimates` | `/statistics.valuations_metrics.forward_pe` **directly** | **SURVIVES** | Simpler than today â no `epsAvg`/`estimatedEpsAvg` dual-spelling handling needed |
| Instant first paint (name + price before the shimmer clears) | `profile` + `quote` | Quote yes; name from `/stocks` catalog or `/statistics` | **SURVIVES** | Keep the `FMPUnavailableException` rather than serving a fabricated $0.00 |
| Ask Cay AI â mid-conversation financial statement pulls | `income-statement`, `balance-sheet`, `cash-flow` | Yes (Pro, 100 cr each) | **SURVIVES (cost)** | â ï¸ 12 quarters Ã 3 statements = 300 cr per tool call. Note the ChatViewModel label branch for `get_financials`/`get_income_statement` appears **dead** â no backend emits those names |
| Research agent's `fetch_dividend_history` tool | `dividends` | `/dividends` | **SURVIVES** | Straight swap |
| Downloaded report PDF | (none â renders frozen dict) | N/A | **INHERITS** | Whatever the report becomes, the PDF becomes |
| Crypto detail chart + quotes | `historical-chart`, `quote` on `BTCUSD` etc. | TD covers crypto natively (`/cryptocurrencies` catalog, `/quote`, `/time_series`) | **SURVIVES** | Symbol remap (`BTCUSD` â `BTC/USD`) |

**Already dead â no action needed either way:** `sec_filings` (defined, never called), `company-outlook` (defined, never called), `insider-trading/statistics` (defined, never called), `GET /stocks/{ticker}/fundamentals` and `/financials-full` (live routes, verified no iOS caller â but *uncached quota spend* if any external caller exists), `GET /home/feed` (registered, no Swift caller, and it **fabricates a synthetic sparkline** on failure, which every live path deliberately refuses to do). Worth deleting regardless of this decision.

---

## 2. The honest headline

**A pure Twelve Data swap kills about a third of the app and guts the specific third you built the product on.**

Counted against the dependency map:

- **~30 user-visible features die outright** with no path back inside Twelve Data's API at any price.
- **~30 degrade** â reachable but shallower, materially more expensive, or requiring a rebuild of the derivation math.
- **~30 survive** cleanly â essentially the price/chart/quote/search/technical layer, plus fundamentals-if-you-rewrite-the-ratio-math, plus analyst data if you pay for Ultra.

**Of the 40 features the dependency map explicitly flags `is_differentiator: true`, 15 die outright.** Every single one of those 15 is in ownership/alternative data or news:

> 13F whale profiles Â· congressional whale profiles Â· the whale hydration job Â· Congressional Buys signal Â· Whale Accumulation signal Â· the "who bought this" drill-down Â· the 8-quarter institutional flow chart Â· the Congress tab Â· the unified Recent Activities feed Â· the report's Insider Activity section Â· Whales Bought/Sold alerts Â· the Smart Money push digest Â· whale/congress logo+name enrichment Â· per-ticker news Â· market news.

Roughly 13 more differentiators degrade materially. **About 12 of 40 differentiators come through intact.**

### Does the paid AI report survive?

**No â not as the thing users pay 20 credits for.** Of its ~14 modules:

- **Dies outright:** Revenue Engine segments (product segmentation exists nowhere at TD), Company Guidance (no transcripts anywhere at TD), Insider Activity section, Key Management beneficial ownership (13D/G), the news corpus that grounds the narrative, and both transcript-derived moat pillars (Switching Costs, Network Effects).
- **Degrades:** Hidden Market Signals (loses the congress half; keeps FINRA short interest), TAM (loses the grounded transcript quote, falls to the FRED proxy), the Fundamentals & Growth 2Ã2 and the persona quality score (both require rebuilding ratio math from raw statements, and `revenue_vital` â 1 of 10 scored dimensions â dies entirely).
- **Survives:** Price Action, Future Forecast, earnings track record, Wall Street Consensus, Capital Allocation â the last three only at TD's **Ultra** tier ($329â999/mo individual).

The report also silently loses accuracy in a way users can't see: the scorer renormalizes missing vitals out, and the existing code path already tolerates a totally empty fundamentals leg by falling back to the LLM's own self-rating â **still charging 20 credits, still caching the result.** A migration that thins the vital set makes that pre-existing hazard worse.

### Do the "App-Exclusive Signals" survive?

**No. All three die.**

- Congressional Buys â TD has zero congressional data. The strings `congress`, `senate`, `politician` appear zero times in their entire documentation.
- Whale Accumulation â depends on the hydration job, which depends on four 13F endpoints TD does not have.
- Earnings Shockers â the only survivor, and it's the one that isn't exclusive.

The card is called **App-Exclusive Signals** because the data behind it is exclusive. Take the data away and it becomes a card that shows one earnings-surprise list.

### Is Twelve Data at least cheaper?

Almost certainly not, once you price the tiers you'd actually need. `/market_cap` history, all analyst estimates, price targets, all ratings endpoints, institutional holders and ETF analytics are **Ultra ($329â999/mo individual) / Enterprise ($1,099/mo business)**. Market movers force at least Pro. And you would *still* be buying a second vendor for news, transcripts, congress and 13F.

The credit economics are also hostile to this codebase's shape. Rate limits are **credits per minute**, and the expensive calls are exactly the ones you fan out:

| Operation | TD credit cost | At Ultra (2,584 cr/min) |
|---|---|---|
| One full fundamentals pull (income + balance + cash flow + statistics) | 350 cr/ticker | ~7 tickers/min |
| One full report (statements + statistics + estimates + targets + ratings) | 800â1,000 cr/ticker | ~2.6 reports/min |
| Institutional holders, one ticker | 1,500 cr | **1.7 tickers/min** |
| 200-symbol scanner universe via `/statistics` | 10,000 cr | ~4 min per build |
| Sector benchmark recompute, S&P 500 | ~325,000 cr | **~126 min** |
| Industry-moat recompute (already ~140k FMP calls today) | ~10M+ cr | **100+ hours â not affordable at any published tier** |

---

## 3. The hybrid option â what you can self-source, what you must buy

### 3a. Genuinely self-sourceable from SEC EDGAR (free, verified live in the research)

| Dataset | Free source | Realistic effort | Honest caveats |
|---|---|---|---|
| **13F institutional holdings** | [Form 13F Data Sets](https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets) â flattened quarterly ZIPs, coverage 2013Q3 â May 2026, 75â95 MB each. Plus `data.sec.gov/submissions/CIK*.json` (verified: returns Berkshire's 39 Ã 13F-HR, 274 Ã Form 4, 5 Ã 13F-HR/A, 8 Ã SCHEDULE 13G) | **6â10 weeks** for ingest + reconciliation + a maintainable pipeline | This is the *raw information table*. You get positions; you do **not** get: quarter-over-quarter diffs, holder performance summaries, or industry breakdowns. Those are FMP's **derived analytics** and you'd rebuild all three. Industry breakdown needs a sector label per ticker (see Â§0 item 1 â the same problem). Performance summary needs price history per position per quarter across 45 registry whales Ã 100â500 positions. â ï¸ **13F identifies securities by CUSIP, not ticker.** CUSIPâticker mapping at scale is the main hidden cost, and CUSIP identifiers are themselves commercially licensed. **This was not covered by the research â verify the licensing before building.** Also: filer-name normalization, amendment/restatement handling (13F-HR/A), and the 45-day filing lag your `latest_filed_13f_quarter()` logic already accounts for |
| **Insider Form 4 (with real transaction codes)** | [Insider Transactions Data Sets](https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2025q3_form345.zip) â verified HTTP 200, quarterly parsed TSVs of Forms 3/4/5 | **2â4 weeks** for backfill + live | The quarterly ZIPs are already parsed, which makes backfill easy. But your app shows *recent* activity and sends push notifications, so you need near-real-time: poll `data.sec.gov` submissions or EDGAR full-text search and parse Form 4 XML yourself. Compliance: **10 requests/second and a declared User-Agent with a contact email** are required. Upside: this gives you *better* data than Twelve Data's product â real transaction codes (P/S/A/M/F/G), prices, signed amounts â which is exactly what makes buy/sell flow computable |

### 3b. Technically self-sourceable, but you probably shouldn't

| Dataset | Free source | Effort | Honest assessment |
|---|---|---|---|
| **Congressional trading** | House: `disclosures-clerk.house.gov/public_disc/financial-pdfs/2025FD.zip` (verified HTTP 200). Senate: `efdsearch.senate.gov/search/` (302; requires in-session terms acceptance) | **4â8 weeks, then permanent maintenance** | The House ZIP has an XML *index*, but the Periodic Transaction Reports themselves are **scanned PDFs** â you need OCR. The Senate has **no bulk download and no API**; it is scrape-only behind a terms gate. This normalization work *is* the product Quiver and FMP sell. Budget for it breaking |
| **SC 13D/G beneficial ownership** (Key Management founder stakes) | EDGAR filings | **High, low reliability** | 13D/G are unstructured HTML/text, not XBRL. There is no parsed SEC dataset for them the way there is for Forms 3/4/5. Realistically: buy |

### 3c. Must be bought â no free source exists

| Dataset | Options | Notes |
|---|---|---|
| **Earnings call transcripts** | FMP Ultimate Â· Kaleidoscope (quote-only) Â· Finnhub **as a paid add-on above their $3,500/mo All-In-One** â their own footnote says All-In-One does not include transcripts | **No free source. Full stop.** |
| **Company news + AI-enrichable article corpus** | FMP (11 news endpoints, available from Starter $22/mo) Â· Finnhub free tier (US company news, 1-yr history, 60 calls/min) â but marked **"Personal Use. Terms apply."**, which a paid consumer app is not | FMP is the cheapest legitimate option here by a wide margin |
| **Product-level revenue segmentation** | **FMP only, among every vendor researched** | No alternative was identified. If you keep the "How {TICKER} Makes Money" card and the report's Revenue Engine, you keep FMP |
| **Analyst ratings / price targets / estimates** | FMP Â· TD Ultra ($329â999) Â· Finnhub | Available from multiple vendors, all at a premium tier |
| **Index constituents** | Nobody free-and-licensable | Low real cost to you: it's used only for a `len()` count and an index-news symbol list, and a hardcoded fallback already exists. **Ship the fallback.** Do not scrape index membership for a commercial app â it's licensed by the index providers |

### 3d. Vendors to avoid for this use case

- **Quiver Quantitative** ($30/mo Hobbyist, $75/mo Trader) â cheapest congress data anywhere, but **both self-serve tiers are explicitly marked "No Commercial Use Rights"** on their own pricing page. A paid consumer app is commercial use. Commercial tier is quote-only.
- **Finnhub free tier** â same "Personal Use" restriction.
- **Kaleidoscope** â genuinely the strongest SEC-native stack (13F with quarterly position diffs back to 1999, Form 4 with automated buy/sell classification, transcripts with structured Q&A, sub-minute webhooks, 99.9% SLA) â but **no published price anywhere**, contact-sales only, and **no congressional data**. Worth a quote call; not something you can plan around today.

### 3e. Realistic hybrid architecture, if you go this way

```
Market data + charts + quotes + search   â Twelve Data Grow/Pro   (works well, cheap)
Fundamentals (statements)                â Twelve Data Pro         (+ rebuild all ratio math)
Analyst / estimates / targets / ratings  â Twelve Data Ultra       (forced tier)
13F + Form 4                             â SEC EDGAR, self-hosted  (6-10 wks + 2-4 wks)
Congress                                 â FMP or Quiver Commercial
Transcripts                              â FMP or Kaleidoscope
News                                     â FMP
Product revenue segmentation             â FMP (only source)
Index quotes                             â ETF proxies (SPY/QQQ/DIA)
Index constituents                       â hardcoded fallback
```

Read that stack honestly: **you still have an FMP contract at the end of it**, plus a Twelve Data Ultra subscription, plus 8â14 weeks of EDGAR engineering, plus permanent ownership of a CUSIP-mapping and filer-normalization pipeline. That is not a cost reduction. It is a cost increase with more moving parts.

---

## 4. Negotiating leverage â the blunt version

**Your leverage is weak, and a migration bluff would be counterproductive.**

Here is why, stated plainly so you don't walk into a call believing otherwise:

1. **FMP's sales team knows what Twelve Data sells.** Data vendors track each other's catalogs closely. Saying "we're evaluating a move to Twelve Data" invites the reply "Twelve Data has no 13F filings, no congressional disclosures, no earnings transcripts, no news endpoint, and no product revenue segmentation â which of your features are you planning to delete?" That is checkable in thirty seconds, and once they've checked it, every other thing you say is discounted.

2. **The datasets you'd be leaving are the ones nobody else bundles.** The research found exactly one vendor covering all six of your alternative-data needs â FMP. Finnhub covers them too, but its only published commercial number is **$3,500/month**, with transcripts as an extra on top. Citing Finnhub as an alternative *makes FMP look cheap*. Do not raise it as a threat; if it comes up, be honest that you priced it and it's not competitive for an indie.

3. **The parts of TD you'd actually use are cheap and unremarkable.** Real-time US quotes are on TD's *free* tier. Nobody at FMP is worried about losing quote traffic.

### What you *can* credibly say

Everything below is true and verifiable, which is what makes it usable:

- **"I'm an indie developer with a pre-revenue consumer iOS app, and I need the Data Display and Licensing Agreement to ship compliantly. What does that look like at my scale?"** â This is the honest framing, and it's the one most likely to surface a startup/indie tier that isn't on the public page.
- **"My bandwidth profile is unusually low for the feature set."** This is genuinely true and documented: the `earnings_service` migration from the market-wide calendar to the per-symbol endpoint cut what was ~99% of your FMP bandwidth (4.3 GB/mo, ~10k calls) to a fraction. FMP's commercial tiers are **bandwidth-tiered** (Build = 100 GB, Enterprise = 1 TB+ per 30 days), so "I belong at the bottom of the Build tier, not in Enterprise" is a concrete, defensible argument with an engineering receipt behind it.
- **"I've cut dead endpoints."** `sec_filings`, `company-outlook`, `insider-trading/statistics`, `/fundamentals` and `/financials-full` are verified unused. Removing them is a small good-faith gesture and reduces your quota surface.
- **"Can we scope the agreement to the endpoint families I actually use?"** You use a well-defined subset. A narrower licence is a legitimate ask.
- **"What does renewal look like?"** Ask now, in writing. The worst outcome is a manageable year-one price followed by an unmanageable year two after you've deepened the dependency.
- **Ask for annual prepay pricing and a usage- or MAU-scaled ramp.**

### Your real BATNA is a scope cut, not a vendor swap

This is the one genuinely strong card you hold, and it happens to be true:

> "If the commercial licence doesn't work at my scale, my fallback isn't switching vendors â it's cutting the whale, congressional and news surfaces and dropping to a lower FMP tier."

That is credible because it is *actually what you would do*, it is cheap for you to execute, and it costs FMP a customer's upgrade rather than a customer's data problem. It also doesn't require you to claim anything a five-minute check would disprove. Use that one.

**Do not say:** "we'll move to Twelve Data." **Do not say:** "we have another vendor lined up." **Do not** imply the migration is scoped or underway.

---

## 5. Recommendation

### Pay FMP for the commercial data-display licence. Do not migrate.

**Reasoning, in order of weight:**

1. **15 of your 40 flagged differentiators die on a pure swap, and they are concentrated in exactly the two areas the product is positioned on** â smart-money/ownership data and the AI report's grounded narrative. The App-Exclusive Signals card loses two of its three signals. The 20-credit paid report loses 5â6 of ~14 modules outright.

2. **Twelve Data is not cheaper for this app.** The endpoints you need force **Ultra** ($329â999/mo individual, or Enterprise at $1,099/mo). FMP Ultimate personal is $149/mo. Even at an unknown commercial multiple, you'd be comparing one FMP contract against a TD Ultra subscription *plus* a second vendor for news, transcripts, congress and 13F. The credit economics are additionally hostile: your industry-moat recompute is not affordable at any published TD tier, and your sector-benchmark recompute would take ~2 hours of a 2,584-credit/min budget.

3. **The migration cost is a quarter of a year you don't get back.** `/profile` alone is consumed by 25 backend modules. Ten services would need ratio math rebuilt from raw statements. Commodity and crypto symbologies both change. Index handling needs an ETF-proxy strategy. The entire ownership stack needs rewriting against EDGAR. Realistically **3â6 months** of an indie developer's full attention, during which nothing else ships â and at the end of it you *still* have an FMP contract for product segmentation, transcripts and news.

4. **You likely need the licence anyway.** FMP's terms state that displaying or redistributing their data requires a specific Data Display and Licensing Agreement. If Caydex is already live and showing FMP data, this isn't a purchasing decision so much as a compliance one.

### Do these before you sign

1. **Resolve Â§0 item 1 in writing** â ask Twelve Data directly whether they expose company profile data with `sector` and `industry`. Not because you're migrating, but because you should never be in a position where you don't know your own alternatives. Ten-minute task; it also makes your FMP conversation more honest.
2. **Get FMP's actual commercial quote** with your real numbers: current call volume, bandwidth per 30 days (post-`earnings_service` fix), MAU, and revenue. Lead with the licence question, not the price question.
3. **Ask about renewal terms and any startup/indie programme** in the same email.

### Do these regardless of the outcome

- **Delete the verified-dead surfaces**: `sec_filings`, `company-outlook`, `insider-trading/statistics`, `GET /fundamentals`, `GET /financials-full`, `GET /home/feed`. The last one is the priority â it's a live route with **no iOS caller** that **fabricates a synthetic sparkline on failure**, which every current path deliberately refuses to do. It costs quota on any external caller and it can lie to users.
- **Write down the ordered de-scope list now, before you need it**, so a bad quote doesn't force improvisation:
  1. Congressional surfaces (most expensive per user-visible feature)
  2. 13F whale surfaces
  3. Earnings transcripts (Company Guidance + two moat pillars degrade to the Gemini fallback that already exists)
  4. News (last â it touches five screens)

### The one hedge worth funding

**Move Form 4 insider data to SEC EDGAR yourself â while still paying FMP, not as a migration.**

It's the best-value item on the whole board: the SEC's `form345` datasets are already parsed, verified reachable, free forever, and give you *strictly better* data than any paid alternative except FMP (real transaction codes, prices, signed amounts â the things Twelve Data's 200-credit endpoint conspicuously lacks). Budget 2â4 weeks. It removes one of your most-used FMP endpoint families from the dependency map and gives your next renewal conversation something real behind it.

13F self-sourcing is the same idea at 3Ã the effort and with a CUSIP-licensing question attached (Â§3a). Worth scoping after Form 4 proves the pattern; not worth starting cold.

---

### Confidence and limits of this document

- Every coverage claim above traces to the research provided: Twelve Data's own machine-readable docs (`llms-full.txt`, per-endpoint `.md` files), their 187-path OpenAPI spec, and live probes against their public demo key. Negative claims (no 13F, no congress, no transcripts, no news, no most-actives, no ratios, no product segmentation) rest on zero-hit greps across the full docs corpus **and** the complete endpoint enumeration â those are decisive, not inferred.
- Every codebase claim traces to the dependency map's file-and-line citations.
- **Unresolved and flagged in-line:** TD company profile / sector / industry (Â§0.1 â blocking), FMP commercial pricing (Â§0.2 â blocking), TD stock-peers (Â§0.3), TD index quotes, `/market_cap` lookback depth, `/earnings_calendar` field list, the ~120-symbol batch cap, TD WebSocket coverage for commodities and indices, Pro-tier statement history depth, TD's commodity catalog vs your specific 16 symbols, CUSIP licensing for 13F self-sourcing, and Finnhub/Kaleidoscope mid-tier pricing.
- **One accuracy caveat that survives any decision:** Twelve Data's own support article states its default US feed covers approximately **5% of total US consolidated trading volume**. Prices are real-time, but volume figures and last-trade prints will not match a SIP feed. Your Heavy Traffic (RVOL) card and every Avg Volume row would be computed off that. Full-market coverage requires a licensed arrangement through their sales team.",
    "critique": null
  },
  "workflowProgress": [
    {
      "type": "workflow_phase",
      "index": 1,
      "title": "Map"
    },
    {
      "type": "workflow_phase",
      "index": 2,
      "title": "Vendor"
    },
    {
      "type": "workflow_phase",
      "index": 3,
      "title": "Synthesize"
    },
    {
      "type": "workflow_phase",
      "index": 4,
      "title": "Verify"
    },
    {
      "type": "workflow_agent",
      "index": 1,
      "label": "map:market-data",
      "phaseIndex": 1,
      "phaseTitle": "Map",
      "agentId": "a6e93d9040246cb9c",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787332420920,
      "queuedAt": 1787332417600,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "quotes, charts, index and commodity market data",
      "promptPreview": "You are analysing a repository READ-ONLY. Do NOT edit, write, commit, or run any
mutating command. Repo root: /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. The FMP integration is backend/app/integrations/fmp.py,
services in backend/app/services/, API routes in backend/app/api/v1/endpoints/, iOS in
frontend/ios/ios/. Cite concrete file:line evidence for every claim. If you cannot verify
sometâ¦",
      "lastProgressAt": 1787332922086,
      "tokens": 217745,
      "toolCalls": 75,
      "durationMs": 501164,
      "resultPreview": "{"domain":"quotes, charts, index and commodity market data","items":[{"user_visible_feature":"Real-time price header on the stock detail screen â price, $ change, % change, Open / Previous Close / Day High / Day Low / Volume / Avg Volume / 52-week range / P-E / EPS / shares outstanding rows in Key Statistics.","backend_service":"No dedicated service â the endpoint fans out directly to FMPClient (bâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 2,
      "label": "map:fundamentals",
      "phaseIndex": 1,
      "phaseTitle": "Map",
      "agentId": "a58edacd5165451fc",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787332421058,
      "queuedAt": 1787332417600,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "fundamentals and derived scoring",
      "promptPreview": "You are analysing a repository READ-ONLY. Do NOT edit, write, commit, or run any
mutating command. Repo root: /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. The FMP integration is backend/app/integrations/fmp.py,
services in backend/app/services/, API routes in backend/app/api/v1/endpoints/, iOS in
frontend/ios/ios/. Cite concrete file:line evidence for every claim. If you cannot verify
sometâ¦",
      "lastProgressAt": 1787332948840,
      "tokens": 173692,
      "toolCalls": 65,
      "durationMs": 526479,
      "resultPreview": "{"domain":"fundamentals and derived scoring","items":[{"user_visible_feature":"AI Ticker Report â \"Fundamentals & Growth\" deep-dive module (2x2 card grid: Profitability / Growth / Valuation / Health, each tappable into a history or rich-chart sheet). This is inside the app's headline PAID feature (20 credits per generation).","backend_service":"backend/app/services/agents/ticker_report_data_collâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 3,
      "label": "map:ownership",
      "phaseIndex": 1,
      "phaseTitle": "Map",
      "agentId": "a2766e2d50d4a7853",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787332422966,
      "queuedAt": 1787332417600,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "ownership_and_alternative_data",
      "promptPreview": "You are analysing a repository READ-ONLY. Do NOT edit, write, commit, or run any
mutating command. Repo root: /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. The FMP integration is backend/app/integrations/fmp.py,
services in backend/app/services/, API routes in backend/app/api/v1/endpoints/, iOS in
frontend/ios/ios/. Cite concrete file:line evidence for every claim. If you cannot verify
sometâ¦",
      "lastProgressAt": 1787332885736,
      "tokens": 162612,
      "toolCalls": 64,
      "durationMs": 462770,
      "resultPreview": "{"domain":"ownership_and_alternative_data","items":[{"user_visible_feature":"Tracking > Whales tab â 13F fund/investor profiles (portfolio value, sector exposure donut, Current Picks holdings list, Recent Trades timeline, annual-return stat tile, behavior/sentiment summaries)","fmp_endpoints":["institutional-ownership/dates","institutional-ownership/extract","institutional-ownership/holder-industrâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 4,
      "label": "map:analyst-earnings",
      "phaseIndex": 1,
      "phaseTitle": "Map",
      "agentId": "ae9d853674c2f468e",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787332423776,
      "queuedAt": 1787332417600,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "analyst, earnings, transcripts, calendar",
      "promptPreview": "You are analysing a repository READ-ONLY. Do NOT edit, write, commit, or run any
mutating command. Repo root: /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. The FMP integration is backend/app/integrations/fmp.py,
services in backend/app/services/, API routes in backend/app/api/v1/endpoints/, iOS in
frontend/ios/ios/. Cite concrete file:line evidence for every claim. If you cannot verify
sometâ¦",
      "lastProgressAt": 1787332962528,
      "tokens": 183905,
      "toolCalls": 96,
      "durationMs": 538752,
      "resultPreview": "{"domain":"analyst, earnings, transcripts, calendar","items":[{"fmp_endpoints":["grades","price-target-consensus"],"user_visible_feature":"Analyst Ratings card on the ticker Analysis tab â consensus badge (STRONG BUYâ¦STRONG SELL), rating distribution bar, low/avg/high price target range + % upside, 12-month momentum bars, and a tappable Upgrades/Downgrades action list.","backend_service":"backend/â¦"
    },
    {
      "type": "workflow_agent",
      "index": 5,
      "label": "map:profile-search-news-etf",
      "phaseIndex": 1,
      "phaseTitle": "Map",
      "agentId": "a1a91042d3d2a9710",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787332423066,
      "queuedAt": 1787332417600,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "company profile, search, news, ETF (+ SEC filings, company-â¦",
      "promptPreview": "You are analysing a repository READ-ONLY. Do NOT edit, write, commit, or run any
mutating command. Repo root: /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. The FMP integration is backend/app/integrations/fmp.py,
services in backend/app/services/, API routes in backend/app/api/v1/endpoints/, iOS in
frontend/ios/ios/. Cite concrete file:line evidence for every claim. If you cannot verify
sometâ¦",
      "lastProgressAt": 1787333013143,
      "tokens": 181656,
      "toolCalls": 78,
      "durationMs": 590076,
      "resultPreview": "{"domain":"company profile, search, news, ETF (+ SEC filings, company-outlook, sector/industry performance)","items":[{"fmp_endpoints":["profile"],"user_visible_feature":"LOAD-BEARING VERDICT: /stable/profile is the single most widely-consumed FMP endpoint in the codebase. fmp.py has only 2 literal call sites (fmp.py:316 get_company_profile, fmp.py:2020 get_company_profiles_batch), but 25 distinctâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 6,
      "label": "td-market-data",
      "phaseIndex": 2,
      "phaseTitle": "Vendor",
      "agentId": "a2d68ff839f87e726",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787333016689,
      "queuedAt": 1787333015059,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Twelve Data (twelvedata.com) API endpoint coverage for US eâ¦",
      "promptPreview": "Research TWELVE DATA (twelvedata.com) actual API endpoint coverage for: real-time and delayed
US equity quotes, batch quotes, intraday time series (1min/5min/15min/30min/1hour/4hour), end-of-day
historical prices, market capitalisation history, index quotes and index constituents, market movers
(gainers/losers/most active), commodity prices, and ETF quotes.
Use their API documentation (twelvedata.â¦",
      "lastProgressAt": 1787333449686,
      "tokens": 134431,
      "toolCalls": 41,
      "durationMs": 432997,
      "resultPreview": "{"area":"Twelve Data (twelvedata.com) API endpoint coverage for US equity/ETF/commodity/index market data","covered":["Real-time US equity quotes â GET /quote (https://api.twelvedata.com/quote?symbol=AAPL). Docs: https://twelvedata.com/docs/llms/market-data/quote.md. Cost 1 credit per symbol. Returns open/high/low/close/volume, previous_close, change, percent_change, average_volume, is_market_openâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 7,
      "label": "td-fundamentals",
      "phaseIndex": 2,
      "phaseTitle": "Vendor",
      "agentId": "a73ccfa71f3ac6d52",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787333016891,
      "queuedAt": 1787333015059,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Twelve Data (twelvedata.com) â FUNDAMENTALS API coverage",
      "promptPreview": "Research TWELVE DATA (twelvedata.com) API coverage for FUNDAMENTALS: income statement,
balance sheet, cash flow, key metrics/statistics, financial ratios, revenue segmentation by product or
geography, dividends, splits, earnings history, earnings calendar, analyst estimates, analyst price
targets, and analyst ratings/grades.
Use twelvedata.com/docs. For each say COVERED / NOT COVERED / UNCERTAIN wâ¦",
      "lastProgressAt": 1787333290774,
      "tokens": 118801,
      "toolCalls": 24,
      "durationMs": 272612,
      "resultPreview": "{"area":"Twelve Data (twelvedata.com) â FUNDAMENTALS API coverage","covered":["Income statement â COVERED â GET /income_statement (annual/quarter via `period`; 100 credits/symbol). TIER: Pro (individual) / Venture (business) and above; FULL historical access requires Ultra / Enterprise. Also GET /income_statement/consolidated (as-reported raw, 100 credits, Ultra/Enterprise+). Docs: https://twelvedâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 8,
      "label": "td-alt-data",
      "phaseIndex": 2,
      "phaseTitle": "Vendor",
      "agentId": "a36657534408d7cb6",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787333016808,
      "queuedAt": 1787333015059,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Twelve Data alternative/ownership data coverage audit + venâ¦",
      "promptPreview": "Research whether TWELVE DATA (twelvedata.com) covers ALTERNATIVE / OWNERSHIP data at all:
Form 13F institutional holdings, institutional holder analytics, insider transactions (SEC Form 4),
congressional/senate/house trading disclosures, earnings call transcripts, and company news.
Use twelvedata.com/docs and their product pages. Be decisive: if these datasets are absent from their
documentation, â¦",
      "lastProgressAt": 1787333634337,
      "tokens": 147200,
      "toolCalls": 64,
      "durationMs": 616176,
      "resultPreview": "{"area":"Twelve Data alternative/ownership data coverage audit + vendor sourcing for the gaps (13F, institutional analytics, insider Form 4, congressional trading, earnings transcripts, company news)","covered":["Insider transactions â PARTIAL. `/insider_transactions` exists under docs section 'Regulatory'. Pro plan (individual) / Venture (business) = $99/mo, 200 API credits per symbol. VERIFIED Lâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 9,
      "label": "synthesize",
      "phaseIndex": 3,
      "phaseTitle": "Synthesize",
      "agentId": "adae0f30dfebb98b8",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787333638632,
      "queuedAt": 1787333636271,
      "attempt": 1,
      "promptPreview": "You are producing a decision document for an indie iOS developer (app: Caydex) who must decide
whether to pay FMP's commercial data-display licence or migrate to Twelve Data.

CODEBASE DEPENDENCY MAP (FMP endpoint -> feature -> screen), as JSON:
[
 {
  "domain": "quotes, charts, index and commodity market data",
  "items": [
   {
    "user_visible_feature": "Real-time price header on the stock detâ¦",
      "lastProgressAt": 1787334139449,
      "tokens": 154352,
      "toolCalls": 0,
      "durationMs": 500816,
      "resultPreview": "# FMP Commercial Licence vs. Twelve Data Migration â Decision Document

**Prepared:** 2026-08-21 Â· **App:** Caydex (AI Value Investor) Â· **Scope:** every FMP-dependent user-visible surface in the codebase, assessed against verified Twelve Data coverage.

**Read this first:** three things in the source research were *not resolved*, and one of them determines whether a migration is even theoreticallâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 10,
      "label": "critique",
      "phaseIndex": 4,
      "phaseTitle": "Verify",
      "agentId": "aa6cc7d82c03ceaa4",
      "model": "claude-opus-5",
      "state": "error",
      "startedAt": 1787334144463,
      "queuedAt": 1787334141465,
      "attempt": 1,
      "promptPreview": "Adversarially review this decision document for an indie developer. Your job is to find where it is
WRONG or OVERCONFIDENT, not to praise it.

DOCUMENT:
# FMP Commercial Licence vs. Twelve Data Migration â Decision Document

**Prepared:** 2026-08-21 Â· **App:** Caydex (AI Value Investor) Â· **Scope:** every FMP-dependent user-visible surface in the codebase, assessed against verified Twelve Data covâ¦",
      "lastProgressAt": 1787334144946,
      "error": "You've hit your session limit Â· resets 11:50am (America/Denver)",
      "tokens": 0,
      "toolCalls": 0,
      "durationMs": 482
    }
  ],
  "totalTokens": 1474394,
  "totalToolCalls": 507
}