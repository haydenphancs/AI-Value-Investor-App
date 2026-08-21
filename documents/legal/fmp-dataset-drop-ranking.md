# FMP dataset drop ranking

> ⚠️ **Read the critique at the bottom BEFORE acting on §4.** The adversarial pass found **two
> ship-blockers in this document's own recommended email** — most importantly that dropping
> Analyst Grades while keeping price targets fabricates a confident "HOLD" verdict on every
> stock, including in the paid report's narrative. §4 as written must not be sent unmodified.

# FMP Dataset Drop Ranking â $600â700/mo Reduction

**Ground rule for this document:** every claim below is traced to a call site. Where a trace measured something in production (call counts, DB coverage, live probes), that measurement is cited, because measured facts beat architectural intuition â and in three cases here they invert it.

---

## 0. The headline finding, before the table

**No dataset in this package is uncalled.** All eight traces returned `actually_called: true`. There is no free money at the *dataset* level.

There is, however, a meaningful amount of free money at the *endpoint* and *call-site* level â dead sub-endpoints, dead routes, and endpoints whose output is already replicated by a hardcoded fallback. Section 2 lists them. Do those regardless of what FMP says, because several of them are also **cost traps that get worse if you drop the parent dataset** (see Â§2.6).

---

## 1. The drop list, in order (safest first)

| # | Dataset | What the user loses | Feeds AI report? | Drop cost | Eng to remove cleanly |
|---|---|---|---|---|---|
| 1 | **Index Constituents â `dowjones-constituent`, `nasdaq-constituent`** â ï¸ *not* `sp500-constituent` | **Nothing.** One `len()` on a profile card that already falls back to hardcoded 30 / 3,000, with a passing test pinning it (`test_index_empty_constituents_falls_back_without_pinning_zero`) | No | **FREE** | ~0.25 d |
| 2 | **Earnings Call Transcripts** (`earning-call-transcript`, `-dates`) | The "Company Guidance" badge + verbatim CFO quote on the paid report. That's the whole list | Yes â but every consumer already has a shipped fallback the prompt names by default | **CHEAP** | 0.5 d |
| 3 | **Advanced Market Metrics** (`biggest-gainers`, `biggest-losers`, `most-actives`, `industry-performance-snapshot`, `sector-performance-snapshot`) | Two Home cards: "Today's Top Movers" and "Heavy Traffic". Carousel shrinks 3 â 1 | Effectively no â one optional Gemini tool that already sentinels | **CHEAP** | 0.5 d |
| 4 | **Commodity Market Data** (GCUSD, CLUSD + 12 more) | 2 of 6 Home Market Pulse tiles; `CommodityDetailView` (already unreachable from search); 3 of ~14 macro factors | Yes â 3 factors in 1 of 10 scoring dimensions; the module backfills itself | **CHEAP** | 1.5 d |
| 5 | **Index quotes** (`^GSPC`, `^IXIC`, `^DJI`, `^VIX`, `^TNX`) | Home's first row + all of `IndexDetailView` â **unless** you re-point to SPY/QQQ/DIA, which costs nothing product-wise | Yes â `^VIX`/`^TNX`, 2 of 10 macro symbols, both behind skip-the-factor guards | **MODERATE** | 3 d |
| 6 | **ETF & Mutual Fund** (`etf/info`, `etf/holdings`, `etf/sector-weightings`) | Top-10 holdings ("what am I actually buying?"), concentration meter, sector breakdown, asset-class truth | **No** â zero refs in `services/agents/` | **MODERATE** | 2 d |
| 7 | **Analyst Ratings â `/stable/grades`** | The whole Analyst Ratings section + Upgrades/Downgrades sheet; 1 of 4 Updates alert kinds; widget cause badge; report consensus + momentum strip | Yes â `wall_street` vital, 3â7% persona weight, already renormalizes out | **MODERATE** | 1.5 d |
| 8 | **Analyst Estimates & Price Target** (`analyst-estimates`, `price-target-consensus`) | Report's Wall Street targets **and** its entire Future Forecast module; forward-EPS bars; P/E (FWD) | Yes â **`forecast` is 24% of Cathie Wood's and 24% of Peter Lynch's weight vector** | **MODERATE** | 2 d |
| 9 | **Market Calendar** (`earnings-calendar` market-wide + `dividends`) | **"Earnings Shockers" â 1 of 3 marketed Home differentiators**, no substitute anywhere; 2 default-on push kinds; 1 of 4 tracking alert kinds; widget's top-priority mover explanation | Barely â one `dividend_status` string | **EXPENSIVE** | 3 d |

---

## 2. Already dead â free money, do this regardless

None of these require an FMP conversation. They are dead code, dead routes, or fetches nothing reads.

1. **The entire "Mutual Fund" half of the ETF & Mutual Fund package is 100% unused.** Grepping `(etf|fund)` across `fmp.py` returns exactly three strings â `etf/info`, `etf/holdings`, `etf/sector-weightings`. There is no mutual-fund endpoint anywhere in the integration layer. **Ask FMP to confirm you aren't paying for a fund SKU you have never called.**

2. **`etf/sector-weightings` is redundant with data you already fetch.** Its only consumer is a placeholder that the `/holdings-risk` call overwrites on card expand â and that path builds sectors from `etf_info["sectorsList"]` instead. The code says it outright (`etf_service.py:1405`: *"Both return the same data"*). Dropping this one endpoint costs approximately nothing and needs no iOS change.

3. **`industry-performance-snapshot` produces dead output on its stock-overview path.** FMP removed the `sector` field from the response, so `_build_sector_industry`'s `same_sector` filter is always `[]` and `industry_rank` is always `"--"`. And the only view that renders it, `TickerDetailSectorIndustrySection.swift`, is referenced exactly once in the entire Swift tree â inside its own `#Preview`. It is never mounted.

4. **Four macro symbols are fetched hourly and read by nothing.** `SIUSD`, `EURUSD`, `USDJPY`, `USDCNY` go into `_fetch_macro_indicators_uncached`, but `_build_macro_risk_factors_from_indicators` only reads `CLUSD`, `GCUSD`, `HGUSD`, `^VIX`, `^TNX`, `DXY`. That's **8 wasted FMP calls per hour**, forever.

5. **Two dead routes making live FMP calls.** `GET /stocks/{ticker}/financials-full` has zero Swift callers and makes an **uncached** `analyst-estimates` call. `GET /api/v1/home/feed` â `APIEndpoint.getHomeFeed` is defined but never constructed anywhere â carries a dead `earnings-calendar` fallback *and* a dead `^GSPC` quote fallback.

6. â ï¸ **The cost trap you must fix before dropping #3.** `stock_overview_service.py:399` calls `get_sector_performance()` **live and uncached** on every overview cache miss â the hottest screen in the app. Today that's 1 FMP call. The moment the snapshot endpoint goes away, the ETF fallback fires and it becomes **12 calls** (1 batch-quote + 11 EOD histories) per miss. Deleting lines 399â400 / 422â425 / 574 / 576 costs nothing (industry_rank is dead, sector_perf is already inside the 24h fundamentals cache) and prevents the drop from *raising* your bill.

7. **Not dead, but the largest remaining bandwidth hotspot:** `tracking_service.py:680` downloads the **entire market's** 14-day forward earnings calendar on every cold Updates feed build, behind only a 30-second per-user cache. It scales linearly with user count. Routing it through the once-daily market-wide fetch the earnings sender already performs cuts most of your remaining calendar bandwidth **without dropping any feature**.

---

## 3. The line I would not cross

**Market Calendar (market-wide `earnings-calendar`) â do not drop.** "Earnings Shockers" is one of three explicitly marketed Home App-Exclusive Signals, and no other data path in the backend can produce market-wide EPS surprises. Add two default-on push categories going permanently silent *behind settings toggles that stay visible and control nothing* â dead switches on a shipped build are worse than a removed row. You would be cutting a third of your named differentiator to save on a dataset that barely touches the paid product.

**Analyst Estimates â do not drop while the report is the product.** The Future Forecast module loses its entire forward half, and the `forecast` vital goes unmeasured. That vital carries **24% of Cathie Wood's and 24% of Peter Lynch's weight vector**, and persona differentiation *is* what the 20-credit report sells. A "Bloomberg Terminal for Novice Investors" that cannot show a forward estimate is a different product.

**Three adjacency landmines â get written confirmation before signing anything:**

- **`/stable/earnings?symbol=X`** is documented in the same Calendar/Earnings section as `earnings-calendar`. If it goes too, `earnings_service` falls to its degraded branch comparing GAAP `epsDiluted` against non-GAAP `epsAvg` â the code's own warning says this *"can look like a big beat/miss when GAAP and non-GAAP diverge."* That ships **silently wrong surprise percentages** into the paid report's "Beat N of M" record. Far worse than an empty state.
- **`/stable/splits`** is also a Calendar-section endpoint, and it is the only thing stopping the **Whales/13F tab â your other marketed differentiator** â from reading a 10:1 split as a multi-billion-share purchase.
- **`sp500-constituent`** shares a wrapper with the Dow/Nasdaq constituents you *are* dropping. It is the universe builder for `sector_benchmarks` and `sector_aggregates`, i.e. every "vs sector/industry average" number in the app and the report. `compute_and_persist_all_sectors` has **no fallback** and returns 0.

**Borderline, cross only with the remediation budget: `/stable/grades`.** It has the best cost-per-pixel argument in the package (see Â§4), but dropping it without ~1.5 days of empty-state work ships a **fabricated "HOLD" consensus on every stock in a financial product** â see Â§5's honesty note.

---

## 4. Recommended cut â the FMP email

Ask for these five to be removed, and ask the two questions at the bottom before agreeing to anything else.

> **Please remove the following from our plan:**
>
> 1. **Earnings Call Transcripts** (`earning-call-transcript`, `earning-call-transcript-dates`) â *We fetch ~18,400 transcript calls per quarterly batch to populate a peer benchmark that, in our production database, covers **2 of 148 industries**; every other consumer already runs on a fallback path.*
> 2. **Index Constituents â Dow Jones and Nasdaq only** (`dowjones-constituent`, `nasdaq-constituent`) â *We consume these lists solely for a row count that is already hardcoded and tested. **We need to retain `sp500-constituent`** â please confirm these are separately provisionable.*
> 3. **ETF Holdings and ETF Sector Weightings** (`etf/holdings`, `etf/sector-weightings`) â *`sector-weightings` is fully redundant with `sectorsList` on `etf/info`, which we would like to keep. Also please confirm we are not being billed for any mutual-fund endpoints â we have never called one.*
> 4. **Advanced Market Metrics** (`biggest-gainers`, `biggest-losers`, `most-actives`, `industry-performance-snapshot`, `sector-performance-snapshot`) â *`industry-performance-snapshot` no longer returns the `sector` field we depend on, and we can rebuild sector performance from SPDR ETF quotes on our existing entitlements.*
> 5. **Analyst Grades** (`/stable/grades`) â *This endpoint ignores the `limit` parameter and returns the full multi-thousand-row history on every call (verified: `limit=5` and `limit=100` return byte-identical 1,787-row payloads for AAPL), and we issue one call per watchlist ticker. It is our worst cost-per-row endpoint by a wide margin.*
>
> **Two questions before we finalise:**
>
> - **Are `^`-prefixed index symbols and USD-suffixed commodity symbols (e.g. `^GSPC`, `GCUSD`) separately priced line items, or are they included in the base quote/historical entitlement we are keeping?** We access them through `/stable/quote`, `/stable/batch-quote` and `/stable/historical-price-eod` â the same endpoints we use for equities â so we need to know whether removing them changes the price at all.
> - **If we request a symbol our plan does not cover inside a `batch-quote` call alongside covered symbols, do you omit that row or reject the whole request?** This determines whether we need engineering work before the change lands.

That second question is not cosmetic. Index and commodity symbols currently ride in the **same `batch-quote` chunk as up to 200 watchlist tickers**, and `_fetch_chunk` swallows a chunk failure into `[]`. If FMP 403s a mixed batch, **every stock quote in it is lost**, blanking the entire home-screen widget and the Updates sweeper's price signals. Three call sites to clean, and only if the answer is "we reject."

---

## 5. What this does **not** save

Be skeptical of your own drop list. Three honest caveats:

**(a) Half this list may be a symbol entitlement, not a line item.** There is **no commodity wrapper in `fmp.py` at all** â `grep -i commodit app/integrations/` returns zero hits. There is no index endpoint either. Both ride `/stable/quote`, `/stable/batch-quote` and `/stable/historical-price-eod` â the endpoints you are *keeping*. `fmp.py:1160` records a live verification: *"identical field set across AAPL, SPY, ^GSPC, BTCUSD, GCUSD"* and *"Index symbols ride along for free."*

So **cutting Commodity Market Data (1.5 d) and Index quotes (3 d) may save literally $0** while costing 4.5 engineering days and a Home screen redesign. **Do not do this work until FMP names a number.** These are the two rows on the list most likely to be cosmetic.

**Likely to move the price:** Earnings Call Transcripts (typically a separately-priced add-on), ETF & Mutual Fund (a named FMP package), Advanced Market Metrics (a named section), Analyst family.
**Likely cosmetic:** Index quotes, Commodity symbols, Index Constituents (a rounding error on any quote).

**(b) Several cuts save a plan tier, not call volume.** The ETF bundle is 12h-cached in memory *and* persisted to Supabase tier-2, shared across the detail, `/holdings-risk` and `/profile` endpoints â **3 calls per ETF symbol per 12 hours**. If FMP prices on volume rather than SKU, this cut buys you nothing. Same for transcripts on the report path (2 calls per ticker per 24h, shared across personas via `ticker_data_cache`); the real transcript volume is the quarterly peer batch, not user traffic.

**(c) The counterintuitive one: dropping *half* the analyst family is more dangerous than dropping all of it.** `has_coverage` is `bool(total_analysts > 0 or target_consensus_price > 0)` â an OR across both endpoints.

- Drop **`grades` only** â `target_consensus_price > 0` keeps `has_coverage` True â the honest empty state never fires â `_compute_consensus(0,0,0,0,0)` returns `HOLD` â **a fabricated confident "HOLD" verdict on every stock in the app**, including the paid report's "one-year forecast" line.
- Drop **`price-target-consensus` only** â `total_analysts > 0` keeps `has_coverage` True â `PriceTargetRange` renders with low/avg/high = 0.0, and `String(format: "$%.2f", targetPrice)` has no nil guard â **"$0.00 target, +0.0% upside" on every stock**.
- Drop **both** â both terms are zero â `has_coverage` False â the `noAnalystCoverageCard` that already ships fires correctly.

This is precisely the fabricated-verdict defect the `has_coverage` flag was written to prevent; a half-drop reopens it *globally* instead of for a handful of uncovered tickers. If FMP sells grades and estimates as one Analyst SKU, that is arguably **good news for correctness** â but it costs you the Future Forecast module, which Â§3 says you should not spend. Either take the full analyst family and rebuild forecast from historical growth, or take `grades` alone and **budget the 1.5 days to split `has_coverage` into `has_ratings` / `has_price_target` first.** Do not ship the half-drop unremediated. Two further gaps survive either way: the report's momentum strip (`ReportConsensusBar.swift:81`, `428-450`) is **ungated** and will render "0 Upgrades Â· 0 Maintains Â· 0 Downgrades" forever, and `_refresh_wall_street_block` (`collector.py:3495-3520`) will **overwrite real targets already frozen into persisted reports with nulls**, because it skips only when the analyst object is `None`, not when it is present-but-empty.

**(d) Two cuts require a copy change, not just a code change.** Dropping commodities makes `plan_features.py:294` false â *"Stocks, ETFs, crypto, indices and commodities â¦ on every plan"* is a user-facing promise in the plan sheet and part of your App Store 5.1.1(v) posture. And dropping transcripts leaves the Company Guidance badge reading **"maintained" for every ticker forever** unless you hide the row â turning a removed data source into a silently wrong claim on a paid screen.",
    "critique": "## Verified corrections â 7 findings, 2 of them ship-blocking

I traced every claim below to the current source. Line numbers are from files I read this session.

---

### C1 â SHIP-BLOCKER. Row 7's "already renormalizes out" is false *under the cut the email actually asks for*, and Â§5(c)'s remediation list is incomplete

**Wrong claim (table row 7):** "Feeds AI report? Yes â `wall_street` vital, 3â7% persona weight, **already renormalizes out**."

**Verified** at `backend/app/services/agents/ticker_report_data_collector.py:3206`:

```python
analyst_signal = (
    target_price > 0
    or (strong_buy + buy + hold + sell + strong_sell) > 0
    or (upgrades + downgrades + maintains) > 0
)
...
if not analyst_signal:
    ws_score_value = None
```

The email (Â§4 item 5) keeps `price-target-consensus`, so `target_price > 0` holds â `analyst_signal` is **True** â `ws_score_value` is computed, never `None`. **The renormalization path the MODERATE rating rests on never fires.** It only fires on a *full* analyst-family drop.

Three consequences Â§5(c) does not mention, none fixable by the two iOS gates it prescribes:

1. `consensus_rating = "hold"` (confirmed: `analyst_service.py:174-176`, `_compute_consensus` returns `AnalystConsensus.HOLD` when `total == 0`) is written into `wall_street_vital` and **persisted into the report row**, not just rendered.
2. `narrative_prompts.py:1313` builds `rating_line = f"Consensus rating: {rating}{dist_str}"` with `dist_str` empty. Stage B is fed **"Consensus rating: hold"** as a bare fact â so Cay AI writes investor-voice prose asserting a Wall Street hold consensus on every ticker. A UI gate cannot reach this.
3. `ws_status` can only be `"warning"`/`"critical"` when `consensus_rating` is `sell`/`strong_sell`, which only grades produce. The Wall Street vital becomes **structurally incapable of ever flagging a bearish analyst view.**

**Corrections:**
- Row 7 â "wall_street stays **measured** and is silently redefined as pure target-upside; it does *not* renormalize out unless the whole analyst family goes."
- Â§5(c) remediation list must add: (i) `_build_wall_street_sections` must treat "targets but no grades" as ratings-unmeasured and emit no `consensus_rating`; (ii) `narrative_prompts` must emit "no rating coverage" rather than `hold`.
- **Â§4 must not send item 5 as written.** Â§3 correctly says "cross only with the remediation budget," but Â§4 lists it as a plain ask with no sequencing. Either hold it out of the email or state that the de-provisioning date must follow the remediation deploy.

**Also: the email's own justification argues for the wrong fix.** It cites cost-per-row. But `analyst_service.py:55` is `_CACHE_TTL = 300` with **no Supabase tier-2 at all** â a violation of the project's own two-tier invariant â and `tracking_service` issues one uncached `get_grades` *per watchlist ticker*. The volume problem is a missing cache layer, not the feature. Â§4 never offers that alternative even though the Analyst-Estimates trace makes exactly that argument for the sibling endpoint.

---

### C2 â SHIP-BLOCKER. Â§2.6's "free money" edit deletes the cache it cites as its own justification, and fabricates a number on the most-viewed screen

**Wrong claim (Â§2.6):** "Deleting lines 399â400 / 422â425 / **574** / 576 costs nothing (industry_rank is dead, **sector_perf is already inside the 24h fundamentals cache**)."

Line 574 *is* the 24h-cached fetch. `stock_overview_service.py:574` is `self.fmp.get_sector_performance(),  # 11` inside `_get_fundamentals`' task list, surfacing at line 622 as `"sector_perf": _list(11)`. The sentence deletes the very thing it names as the reason deletion is safe.

Downstream, verified end-to-end:
`fund.get("sector_perf")` (:872) â `_build_sector_industry` (:926) â `sector_performance` on the company-profile payload (:1649) â `CompanyProfileDTO.sectorPerformance` (`Models/StockOverviewResponseModels.swift:303`) â **`Views/Organisms/TickerDetailCompanyProfileSection.swift:60-64`, the "Sector Performance (1Y)" row** â which *is* mounted, via `TickerDetailOverviewContent.swift:34`.

The doc conflates two different views. `TickerDetailSectorIndustrySection.swift` is genuinely preview-only (its sole reference is line 194, its own `#Preview` â Â§2.3 is correct about that). `TickerDetailCompanyProfileSection` is live and renders the sector row.

And it does not blank. `sectorPerformance` is a non-optional `Double` and `sectorPerformanceColor = sectorPerformance >= 0 ? bullish : bearish`, so an empty list renders **"+0.00%" in green on every stock** â a fabricated value on the app's hottest screen, exactly the failure class Â§3/Â§5(d) polices elsewhere.

**Corrected edit:** delete **399â400** and the *sector* half of **422â423** only (that alone removes the uncached fan-out per overview miss). **Keep 574.** Line 576 / `_list(13)` / 424â425 (industry_perf) can go â `industry_rank` really is dead.

**Two arithmetic corrections in the same section:**
- Post-drop cost is **13 calls, not 12**: `_make_request` raises on 403 (`raise_for_status`, fmp.py:267), `_latest_perf_snapshot` propagates, `get_sector_performance` catches at :733 and *then* runs the 12-call ETF fallback.
- `fmp.get_sector_performance()` has **no internal cache** (fmp.py:719-806) â `index_service` and `widget_movers_service` each keep their own. Left alone, the ETF fallback inside `_get_fundamentals` recomputes a *market-wide* figure 13 calls per *distinct ticker* per 24h. Add a module-level cache in `fmp.py` before the AMM drop lands, or the drop **raises** call volume.

---

### C3 â (c) The email names "Advanced Market Metrics" as a section, and `/stable/stock-price-change` is very likely inside it

`/stable/stock-price-change` sits in FMP's Market Performance documentation alongside `biggest-gainers`, `most-actives` and the two `*-performance-snapshot` endpoints the email asks to remove. Its callers:

- `whale_service.py:3175` `_compute_ticker_cagr` â the whale's annualized-return tile on the **Whales/13F tab â the marketed differentiator Â§3 says to protect.**
- `ticker_report_data_collector.py:889` â the change windows for **all ten** `_MACRO_SYMBOLS`, i.e. every deterministic macro risk factor, not just the commodity ones.

The doc flags adjacency risk for Market Calendar (`/earnings`, `/splits`) but not here â an asymmetry with no justification.

**Correction:** add `/stable/stock-price-change` to Â§3's adjacency landmines, and add it to the email item 4 as an explicit retain ("please confirm `stock-price-change` is not part of this section"), mirroring the `sp500-constituent` carve-out.

---

### C4 â (a) Row 2 "That's the whole list" is wrong, and Â§5 omits the cost that this drop *creates*

Row 2 says the transcript loss is the Company Guidance badge + quote, "**That's the whole list**." Its own trace lists four more consumers (Moat TAM Priority-1 source, Switching Costs NRR driver, Network Effects `platform_user_count` driver, Switching Costs peer average). Defensible wording: "the only **user-visible** loss."

More materially: the trace states the Switching Costs pillar moves from deterministic to the Phase-3D **grounded-Gemini** fallback for ~100% of reports instead of ~93%. Â§5 lists three caveats about what the cuts don't save and omits this one â an FMP line item is partly converted into Gemini web-grounded calls. Add as Â§5(e).

---

### C5 â (b) Nothing is wrongly marked dead, but Â§2's savings arithmetic is overstated in three places

- **Â§2.4 is factually right** â `_MACRO_SYMBOLS` (collector:153-162) contains `SIUSD, EURUSD, USDJPY, USDCNY`, and only `CLUSD/GCUSD/HGUSD/^VIX/^TNX/DXY` are ever `by_sym.get(...)`. But "**8 wasted FMP calls per hour, forever**" is wrong: `_macro_snapshot_cache` is lazy with a 1h TTL and there is **no background pre-warm**, so the ceiling is 8/hr and the floor is 0. And the module's own comment (collector:169-171) says *"No dollar cost either way (FMP is flat-rate) â this saves rate-limit budget + report latency, not grounding $."* Restate as rate-limit headroom, not dollars.
- **`DXY` is neither `^`-prefixed nor `USD`-suffixed**, so Â§4's second question does not cover the FX symbols at all â yet `DXY` drives a live macro factor (collector:6184). Add FX symbols to that question.
- **Â§2.5**: `financials-full` is confirmed dead (zero Swift references) but makes **nine** uncached FMP calls, not one.

---

### C6 â (a) Row 5's "what the user loses" understates the index-quote drop

Table row 5 says "Home's first row + all of `IndexDetailView`." Missing from that column, per its own trace: the Updates `__MARKET__` insight card's price-move trigger **and** the market-wide-dislocation anti-stampede guard (`updates_materiality.py:566`), the widget's market band + "the whole market moved" attribution leg, and the daily `^GSPC` Ï precompute. Also absent: `IndexDetailView` renders **"$0.00 +0.00%"** because `current_price`/`price_change`/`price_change_percent` are required floats defaulted by `_q()`. By the doc's own standard (Â§3, Â§5d), a fabricated value belongs in the loss column, not a footnote.

---

### C7 â (d) Â§2.2's "needs no iOS change" holds only while `etf/info` survives, and the email makes that fragile

`_build_sectors_from_info` (etf_service.py:1399) reads `etf_info["sectorsList"]`; `_build_sector_weights` (:1717) reads the dropped endpoint and feeds the collapsed card's `top_sectors` (:959). Confirmed redundant â **conditional on retaining `etf/info`.**

Email item 3 asks FMP to remove two endpoints from a package it names "ETF & Mutual Fund". If the vendor scopes the package rather than the endpoints, `etf/info` goes with it and **all three misinformation defects in the ETF trace fire at once**: bond/gold ETFs drawn as 100% equities, index funds tagged "Active", and a missing expense ratio scoring the *maximum* fee points. Make retention of `etf/info` a stated hard condition, not a parenthetical.

**Scope inconsistency in the same row:** table row 6 prices "sector breakdown, asset-class truth" (an `etf/info` drop) at MODERATE / 2 d, but the email only asks for `holdings` + `sector-weightings`. The emailed cut is closer to **0.5 d** â guard `ETFTopHoldingsRow` and `ETFSectorsView`'s fabricated "Others 100%" bar. The table and the email are pricing two different cuts.

---

### Checked and clear (no correction needed)

- `get_index_constituents` (fmp.py:874) and `get_sp500_constituents` (fmp.py:832) are separate wrappers, but the former maps `^GSPC â sp500-constituent`. Â§3's warning is correct.
- `_compute_consensus(0,0,0,0,0) â HOLD`, `has_coverage = bool(total_analysts > 0 or target_consensus_price > 0)`, `targetPrice: Double` non-optional with `String(format: "$%.2f", targetPrice)` and no nil guard â Â§5(c)'s three-way analysis is accurate as far as it goes. `TickerAnalysisContent.swift:35-40` even carries a comment naming the exact defect the half-drop reopens.
- `FMPClient.request_failures` is a shared failure counter, but the whale hydrator constructs its **own** `FMPClient()` (main.py, inside `_run_whale_hydration_job`), so a 403-ing de-provisioned endpoint on the request path cannot poison its outage detector. No cross-dataset contamination â but this is a reason to **delete** dropped call sites rather than soft-stub them, since the Market Calendar trace's "soft drop" option would leave 403s incrementing a counter with that documented semantic."
  },
  "workflowProgress": [
    {
      "type": "workflow_phase",
      "index": 1,
      "title": "Trace"
    },
    {
      "type": "workflow_phase",
      "index": 2,
      "title": "Rank"
    },
    {
      "type": "workflow_phase",
      "index": 3,
      "title": "Verify"
    },
    {
      "type": "workflow_agent",
      "index": 1,
      "label": "trace:transcripts",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a0e04ba1bcc62ceaf",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346291169,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Earnings Call Transcripts (FMP `earning-call-transcript`, `â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346634887,
      "tokens": 138441,
      "toolCalls": 44,
      "durationMs": 343717,
      "resultPreview": "{"dataset":"Earnings Call Transcripts (FMP `earning-call-transcript`, `earning-call-transcript-dates`)","actually_called":true,"fmp_endpoints":["earning-call-transcript-dates","earning-call-transcript"],"call_chain":"Wrapper: `FMPClient.get_earning_call_transcript()` â backend/app/integrations/fmp.py:563-627. It makes TWO calls: `earning-call-transcript-dates?symbol=` (line 591) to resolve the latâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 2,
      "label": "trace:commodity",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a79a7909785defe0f",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346290442,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Commodity Market Data (FMP commodity quotes/charts via USD-â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346678661,
      "tokens": 153774,
      "toolCalls": 79,
      "durationMs": 387813,
      "resultPreview": "{"dataset":"Commodity Market Data (FMP commodity quotes/charts via USD-suffixed futures symbols: GCUSD, CLUSD, SIUSD, HGUSD, NGUSD, PLUSD, PAUSD, ZWUSD, ZCUSD, ZSUSD, KCUSD, SBUSD, CCUSD, CTUSD)","actually_called":true,"reasoning":"METHOD NOTE â there is NO commodity wrapper in fmp.py. `grep -rn -i commodit app/integrations/` returns ZERO hits. Commodity data rides entirely on the generic equity eâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 3,
      "label": "trace:etf",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a022a242b96109c63",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346289719,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "ETF & Mutual Fund (etf/holdings, etf/info, etf/sector-weighâ¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346602736,
      "tokens": 136640,
      "toolCalls": 46,
      "durationMs": 311888,
      "resultPreview": "{"dataset":"ETF & Mutual Fund (etf/holdings, etf/info, etf/sector-weightings)","actually_called":true,"fmp_endpoints":["etf/info","etf/holdings","etf/sector-weightings"],"call_chain":"FMP wrappers are the only three ETF/fund paths in the whole integration layer: `FMPClient.get_etf_info` (/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App/backend/app/integrations/fmp.py:1072 â \"etf/info\"), `get_eâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 4,
      "label": "trace:index",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a5bc393772aef356b",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346292076,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Index Market Data â (a) index QUOTES/history (^GSPC, ^IXIC,â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346663802,
      "tokens": 152293,
      "toolCalls": 60,
      "durationMs": 371725,
      "resultPreview": "{"dataset":"Index Market Data â (a) index QUOTES/history (^GSPC, ^IXIC, ^DJI, plus ^VIX and ^TNX) and (b) index CONSTITUENTS (dowjones-constituent, nasdaq-constituent)","actually_called":true,"user_visible_features":["Home screen \"Market Pulse\" strip â 3 of its 6 tiles are S&P 500 / Nasdaq / Dow (^GSPC / ^IXIC / ^DJI), each with price, % change and a 30-point sparkline (home_dashboard_service.pyâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 5,
      "label": "trace:movers",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a42cdc8cba2e27069",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346289470,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Advanced Market Metrics (biggest-gainers, biggest-losers, mâ¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346648304,
      "tokens": 154453,
      "toolCalls": 64,
      "durationMs": 357456,
      "resultPreview": "{"dataset":"Advanced Market Metrics (biggest-gainers, biggest-losers, most-actives, industry-performance-snapshot, sector-performance-snapshot)","actually_called":true,"fmp_endpoints":["/stable/biggest-gainers","/stable/biggest-losers","/stable/most-actives","/stable/industry-performance-snapshot","/stable/sector-performance-snapshot"],"user_visible_features":["Home > Daily Scanners > \"Today's Toâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 6,
      "label": "trace:analyst-ratings",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "ae2805a5b2580ec4b",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346289183,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Analyst Ratings & Price Targets â the FMP `/stable/grades` â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346605568,
      "tokens": 147464,
      "toolCalls": 62,
      "durationMs": 316385,
      "resultPreview": "{"dataset":"Analyst Ratings & Price Targets â the FMP `/stable/grades` endpoint (individual analyst rating actions: upgrades/downgrades/initiations/maintains, with per-action firm name, previous/new grade and previous/new price target)","actually_called":true,"fmp_endpoints":["grades"],"call_chain":"WRAPPER: FMPClient.get_grades(ticker, limit) -> GET {FMP_BASE_URL}/grades?symbol=X&limit=N â /Usersâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 7,
      "label": "trace:analyst-estimates",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "afcf64ef3f73561fe",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346606044,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Analyst Estimates & Price Target (FMP /stable/analyst-estimâ¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787347028401,
      "tokens": 163297,
      "toolCalls": 78,
      "durationMs": 422355,
      "resultPreview": "{"dataset":"Analyst Estimates & Price Target (FMP /stable/analyst-estimates, /stable/price-target-consensus)","actually_called":true,"fmp_endpoints":["analyst-estimates","price-target-consensus"],"call_chain":"WRAPPERS: backend/app/integrations/fmp.py:485 get_analyst_estimates(\"analyst-estimates\"); :504 get_price_target_consensus(\"price-target-consensus\"). (NOTE: :496 get_grades hits a DIFFEREâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 8,
      "label": "trace:calendar",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a604c6d6faf4563ea",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346609007,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Market Calendar â /stable/earnings-calendar (market-wide) +â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787347085212,
      "tokens": 178098,
      "toolCalls": 96,
      "durationMs": 476205,
      "resultPreview": "{"dataset":"Market Calendar â /stable/earnings-calendar (market-wide) + /stable/dividends","actually_called":true,"fmp_endpoints":["/stable/earnings-calendar (market-wide, from/to window) â fmp.py:511 get_earnings_calendar()","/stable/dividends?symbol=X â fmp.py:1121 get_dividend_history()","ADJACENCY RISK â /stable/earnings?symbol=X â fmp.py:525 get_earning_calendar_full() + fmp.py:630 get_historâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 9,
      "label": "rank",
      "phaseIndex": 2,
      "phaseTitle": "Rank",
      "agentId": "a0b711b75db9bd183",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787347089128,
      "queuedAt": 1787347087221,
      "attempt": 1,
      "promptPreview": "Produce a DROP RANKING for an indie developer cutting FMP datasets to lower a $600-700/mo quote.

TRACES (JSON):
[
 {
  "dataset": "Earnings Call Transcripts (FMP `earning-call-transcript`, `earning-call-transcript-dates`)",
  "actually_called": true,
  "fmp_endpoints": [
   "earning-call-transcript-dates",
   "earning-call-transcript"
  ],
  "call_chain": "Wrapper: `FMPClient.get_earning_call_traâ¦",
      "lastProgressAt": 1787347246325,
      "tokens": 125042,
      "toolCalls": 0,
      "durationMs": 157195,
      "resultPreview": "# FMP Dataset Drop Ranking â $600â700/mo Reduction

**Ground rule for this document:** every claim below is traced to a call site. Where a trace measured something in production (call counts, DB coverage, live probes), that measurement is cited, because measured facts beat architectural intuition â and in three cases here they invert it.

---

## 0. The headline finding, before the table

**No datâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 10,
      "label": "critique",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "aa0e9bd8ba21cd681",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787347250340,
      "queuedAt": 1787347248093,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "sed -n '45,60p' app/services/analyst_service.py; echo "=== â¦",
      "promptPreview": "Adversarially review this drop ranking. Find verdicts that are WRONG or dangerous.

RANKING:
# FMP Dataset Drop Ranking â $600â700/mo Reduction

**Ground rule for this document:** every claim below is traced to a call site. Where a trace measured something in production (call counts, DB coverage, live probes), that measurement is cited, because measured facts beat architectural intuition â and in â¦",
      "lastProgressAt": 1787347677387,
      "tokens": 186273,
      "toolCalls": 41,
      "durationMs": 427046,
      "resultPreview": "## Verified corrections â 7 findings, 2 of them ship-blocking

I traced every claim below to the current source. Line numbers are from files I read this session.

---

### C1 â SHIP-BLOCKER. Row 7's "already renormalizes out" is false *under the cut the email actually asks for*, and Â§5(c)'s remediation list is incomplete

**Wrong claim (table row 7):** "Feeds AI report? Yes â `wall_street` vital, â¦"
    }
  ],
  "totalTokens": 1535775,
  "totalToolCalls": 570
}

---

# 🔍 ADVERSARIAL CRITIQUE

## Verified corrections â 7 findings, 2 of them ship-blocking

I traced every claim below to the current source. Line numbers are from files I read this session.

---

### C1 â SHIP-BLOCKER. Row 7's "already renormalizes out" is false *under the cut the email actually asks for*, and Â§5(c)'s remediation list is incomplete

**Wrong claim (table row 7):** "Feeds AI report? Yes â `wall_street` vital, 3â7% persona weight, **already renormalizes out**."

**Verified** at `backend/app/services/agents/ticker_report_data_collector.py:3206`:

```python
analyst_signal = (
    target_price > 0
    or (strong_buy + buy + hold + sell + strong_sell) > 0
    or (upgrades + downgrades + maintains) > 0
)
...
if not analyst_signal:
    ws_score_value = None
```

The email (Â§4 item 5) keeps `price-target-consensus`, so `target_price > 0` holds â `analyst_signal` is **True** â `ws_score_value` is computed, never `None`. **The renormalization path the MODERATE rating rests on never fires.** It only fires on a *full* analyst-family drop.

Three consequences Â§5(c) does not mention, none fixable by the two iOS gates it prescribes:

1. `consensus_rating = "hold"` (confirmed: `analyst_service.py:174-176`, `_compute_consensus` returns `AnalystConsensus.HOLD` when `total == 0`) is written into `wall_street_vital` and **persisted into the report row**, not just rendered.
2. `narrative_prompts.py:1313` builds `rating_line = f"Consensus rating: {rating}{dist_str}"` with `dist_str` empty. Stage B is fed **"Consensus rating: hold"** as a bare fact â so Cay AI writes investor-voice prose asserting a Wall Street hold consensus on every ticker. A UI gate cannot reach this.
3. `ws_status` can only be `"warning"`/`"critical"` when `consensus_rating` is `sell`/`strong_sell`, which only grades produce. The Wall Street vital becomes **structurally incapable of ever flagging a bearish analyst view.**

**Corrections:**
- Row 7 â "wall_street stays **measured** and is silently redefined as pure target-upside; it does *not* renormalize out unless the whole analyst family goes."
- Â§5(c) remediation list must add: (i) `_build_wall_street_sections` must treat "targets but no grades" as ratings-unmeasured and emit no `consensus_rating`; (ii) `narrative_prompts` must emit "no rating coverage" rather than `hold`.
- **Â§4 must not send item 5 as written.** Â§3 correctly says "cross only with the remediation budget," but Â§4 lists it as a plain ask with no sequencing. Either hold it out of the email or state that the de-provisioning date must follow the remediation deploy.

**Also: the email's own justification argues for the wrong fix.** It cites cost-per-row. But `analyst_service.py:55` is `_CACHE_TTL = 300` with **no Supabase tier-2 at all** â a violation of the project's own two-tier invariant â and `tracking_service` issues one uncached `get_grades` *per watchlist ticker*. The volume problem is a missing cache layer, not the feature. Â§4 never offers that alternative even though the Analyst-Estimates trace makes exactly that argument for the sibling endpoint.

---

### C2 â SHIP-BLOCKER. Â§2.6's "free money" edit deletes the cache it cites as its own justification, and fabricates a number on the most-viewed screen

**Wrong claim (Â§2.6):** "Deleting lines 399â400 / 422â425 / **574** / 576 costs nothing (industry_rank is dead, **sector_perf is already inside the 24h fundamentals cache**)."

Line 574 *is* the 24h-cached fetch. `stock_overview_service.py:574` is `self.fmp.get_sector_performance(),  # 11` inside `_get_fundamentals`' task list, surfacing at line 622 as `"sector_perf": _list(11)`. The sentence deletes the very thing it names as the reason deletion is safe.

Downstream, verified end-to-end:
`fund.get("sector_perf")` (:872) â `_build_sector_industry` (:926) â `sector_performance` on the company-profile payload (:1649) â `CompanyProfileDTO.sectorPerformance` (`Models/StockOverviewResponseModels.swift:303`) â **`Views/Organisms/TickerDetailCompanyProfileSection.swift:60-64`, the "Sector Performance (1Y)" row** â which *is* mounted, via `TickerDetailOverviewContent.swift:34`.

The doc conflates two different views. `TickerDetailSectorIndustrySection.swift` is genuinely preview-only (its sole reference is line 194, its own `#Preview` â Â§2.3 is correct about that). `TickerDetailCompanyProfileSection` is live and renders the sector row.

And it does not blank. `sectorPerformance` is a non-optional `Double` and `sectorPerformanceColor = sectorPerformance >= 0 ? bullish : bearish`, so an empty list renders **"+0.00%" in green on every stock** â a fabricated value on the app's hottest screen, exactly the failure class Â§3/Â§5(d) polices elsewhere.

**Corrected edit:** delete **399â400** and the *sector* half of **422â423** only (that alone removes the uncached fan-out per overview miss). **Keep 574.** Line 576 / `_list(13)` / 424â425 (industry_perf) can go â `industry_rank` really is dead.

**Two arithmetic corrections in the same section:**
- Post-drop cost is **13 calls, not 12**: `_make_request` raises on 403 (`raise_for_status`, fmp.py:267), `_latest_perf_snapshot` propagates, `get_sector_performance` catches at :733 and *then* runs the 12-call ETF fallback.
- `fmp.get_sector_performance()` has **no internal cache** (fmp.py:719-806) â `index_service` and `widget_movers_service` each keep their own. Left alone, the ETF fallback inside `_get_fundamentals` recomputes a *market-wide* figure 13 calls per *distinct ticker* per 24h. Add a module-level cache in `fmp.py` before the AMM drop lands, or the drop **raises** call volume.

---

### C3 â (c) The email names "Advanced Market Metrics" as a section, and `/stable/stock-price-change` is very likely inside it

`/stable/stock-price-change` sits in FMP's Market Performance documentation alongside `biggest-gainers`, `most-actives` and the two `*-performance-snapshot` endpoints the email asks to remove. Its callers:

- `whale_service.py:3175` `_compute_ticker_cagr` â the whale's annualized-return tile on the **Whales/13F tab â the marketed differentiator Â§3 says to protect.**
- `ticker_report_data_collector.py:889` â the change windows for **all ten** `_MACRO_SYMBOLS`, i.e. every deterministic macro risk factor, not just the commodity ones.

The doc flags adjacency risk for Market Calendar (`/earnings`, `/splits`) but not here â an asymmetry with no justification.

**Correction:** add `/stable/stock-price-change` to Â§3's adjacency landmines, and add it to the email item 4 as an explicit retain ("please confirm `stock-price-change` is not part of this section"), mirroring the `sp500-constituent` carve-out.

---

### C4 â (a) Row 2 "That's the whole list" is wrong, and Â§5 omits the cost that this drop *creates*

Row 2 says the transcript loss is the Company Guidance badge + quote, "**That's the whole list**." Its own trace lists four more consumers (Moat TAM Priority-1 source, Switching Costs NRR driver, Network Effects `platform_user_count` driver, Switching Costs peer average). Defensible wording: "the only **user-visible** loss."

More materially: the trace states the Switching Costs pillar moves from deterministic to the Phase-3D **grounded-Gemini** fallback for ~100% of reports instead of ~93%. Â§5 lists three caveats about what the cuts don't save and omits this one â an FMP line item is partly converted into Gemini web-grounded calls. Add as Â§5(e).

---

### C5 â (b) Nothing is wrongly marked dead, but Â§2's savings arithmetic is overstated in three places

- **Â§2.4 is factually right** â `_MACRO_SYMBOLS` (collector:153-162) contains `SIUSD, EURUSD, USDJPY, USDCNY`, and only `CLUSD/GCUSD/HGUSD/^VIX/^TNX/DXY` are ever `by_sym.get(...)`. But "**8 wasted FMP calls per hour, forever**" is wrong: `_macro_snapshot_cache` is lazy with a 1h TTL and there is **no background pre-warm**, so the ceiling is 8/hr and the floor is 0. And the module's own comment (collector:169-171) says *"No dollar cost either way (FMP is flat-rate) â this saves rate-limit budget + report latency, not grounding $."* Restate as rate-limit headroom, not dollars.
- **`DXY` is neither `^`-prefixed nor `USD`-suffixed**, so Â§4's second question does not cover the FX symbols at all â yet `DXY` drives a live macro factor (collector:6184). Add FX symbols to that question.
- **Â§2.5**: `financials-full` is confirmed dead (zero Swift references) but makes **nine** uncached FMP calls, not one.

---

### C6 â (a) Row 5's "what the user loses" understates the index-quote drop

Table row 5 says "Home's first row + all of `IndexDetailView`." Missing from that column, per its own trace: the Updates `__MARKET__` insight card's price-move trigger **and** the market-wide-dislocation anti-stampede guard (`updates_materiality.py:566`), the widget's market band + "the whole market moved" attribution leg, and the daily `^GSPC` Ï precompute. Also absent: `IndexDetailView` renders **"$0.00 +0.00%"** because `current_price`/`price_change`/`price_change_percent` are required floats defaulted by `_q()`. By the doc's own standard (Â§3, Â§5d), a fabricated value belongs in the loss column, not a footnote.

---

### C7 â (d) Â§2.2's "needs no iOS change" holds only while `etf/info` survives, and the email makes that fragile

`_build_sectors_from_info` (etf_service.py:1399) reads `etf_info["sectorsList"]`; `_build_sector_weights` (:1717) reads the dropped endpoint and feeds the collapsed card's `top_sectors` (:959). Confirmed redundant â **conditional on retaining `etf/info`.**

Email item 3 asks FMP to remove two endpoints from a package it names "ETF & Mutual Fund". If the vendor scopes the package rather than the endpoints, `etf/info` goes with it and **all three misinformation defects in the ETF trace fire at once**: bond/gold ETFs drawn as 100% equities, index funds tagged "Active", and a missing expense ratio scoring the *maximum* fee points. Make retention of `etf/info` a stated hard condition, not a parenthetical.

**Scope inconsistency in the same row:** table row 6 prices "sector breakdown, asset-class truth" (an `etf/info` drop) at MODERATE / 2 d, but the email only asks for `holdings` + `sector-weightings`. The emailed cut is closer to **0.5 d** â guard `ETFTopHoldingsRow` and `ETFSectorsView`'s fabricated "Others 100%" bar. The table and the email are pricing two different cuts.

---

### Checked and clear (no correction needed)

- `get_index_constituents` (fmp.py:874) and `get_sp500_constituents` (fmp.py:832) are separate wrappers, but the former maps `^GSPC â sp500-constituent`. Â§3's warning is correct.
- `_compute_consensus(0,0,0,0,0) â HOLD`, `has_coverage = bool(total_analysts > 0 or target_consensus_price > 0)`, `targetPrice: Double` non-optional with `String(format: "$%.2f", targetPrice)` and no nil guard â Â§5(c)'s three-way analysis is accurate as far as it goes. `TickerAnalysisContent.swift:35-40` even carries a comment naming the exact defect the half-drop reopens.
- `FMPClient.request_failures` is a shared failure counter, but the whale hydrator constructs its **own** `FMPClient()` (main.py, inside `_run_whale_hydration_job`), so a 403-ing de-provisioned endpoint on the request path cannot poison its outage detector. No cross-dataset contamination â but this is a reason to **delete** dropped call sites rather than soft-stub them, since the Market Calendar trace's "soft drop" option would leave 403s incrementing a counter with that documented semantic."
  },
  "workflowProgress": [
    {
      "type": "workflow_phase",
      "index": 1,
      "title": "Trace"
    },
    {
      "type": "workflow_phase",
      "index": 2,
      "title": "Rank"
    },
    {
      "type": "workflow_phase",
      "index": 3,
      "title": "Verify"
    },
    {
      "type": "workflow_agent",
      "index": 1,
      "label": "trace:transcripts",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a0e04ba1bcc62ceaf",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346291169,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Earnings Call Transcripts (FMP `earning-call-transcript`, `â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346634887,
      "tokens": 138441,
      "toolCalls": 44,
      "durationMs": 343717,
      "resultPreview": "{"dataset":"Earnings Call Transcripts (FMP `earning-call-transcript`, `earning-call-transcript-dates`)","actually_called":true,"fmp_endpoints":["earning-call-transcript-dates","earning-call-transcript"],"call_chain":"Wrapper: `FMPClient.get_earning_call_transcript()` â backend/app/integrations/fmp.py:563-627. It makes TWO calls: `earning-call-transcript-dates?symbol=` (line 591) to resolve the latâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 2,
      "label": "trace:commodity",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a79a7909785defe0f",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346290442,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Commodity Market Data (FMP commodity quotes/charts via USD-â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346678661,
      "tokens": 153774,
      "toolCalls": 79,
      "durationMs": 387813,
      "resultPreview": "{"dataset":"Commodity Market Data (FMP commodity quotes/charts via USD-suffixed futures symbols: GCUSD, CLUSD, SIUSD, HGUSD, NGUSD, PLUSD, PAUSD, ZWUSD, ZCUSD, ZSUSD, KCUSD, SBUSD, CCUSD, CTUSD)","actually_called":true,"reasoning":"METHOD NOTE â there is NO commodity wrapper in fmp.py. `grep -rn -i commodit app/integrations/` returns ZERO hits. Commodity data rides entirely on the generic equity eâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 3,
      "label": "trace:etf",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a022a242b96109c63",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346289719,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "ETF & Mutual Fund (etf/holdings, etf/info, etf/sector-weighâ¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346602736,
      "tokens": 136640,
      "toolCalls": 46,
      "durationMs": 311888,
      "resultPreview": "{"dataset":"ETF & Mutual Fund (etf/holdings, etf/info, etf/sector-weightings)","actually_called":true,"fmp_endpoints":["etf/info","etf/holdings","etf/sector-weightings"],"call_chain":"FMP wrappers are the only three ETF/fund paths in the whole integration layer: `FMPClient.get_etf_info` (/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App/backend/app/integrations/fmp.py:1072 â \"etf/info\"), `get_eâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 4,
      "label": "trace:index",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a5bc393772aef356b",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346292076,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Index Market Data â (a) index QUOTES/history (^GSPC, ^IXIC,â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346663802,
      "tokens": 152293,
      "toolCalls": 60,
      "durationMs": 371725,
      "resultPreview": "{"dataset":"Index Market Data â (a) index QUOTES/history (^GSPC, ^IXIC, ^DJI, plus ^VIX and ^TNX) and (b) index CONSTITUENTS (dowjones-constituent, nasdaq-constituent)","actually_called":true,"user_visible_features":["Home screen \"Market Pulse\" strip â 3 of its 6 tiles are S&P 500 / Nasdaq / Dow (^GSPC / ^IXIC / ^DJI), each with price, % change and a 30-point sparkline (home_dashboard_service.pyâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 5,
      "label": "trace:movers",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a42cdc8cba2e27069",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346289470,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Advanced Market Metrics (biggest-gainers, biggest-losers, mâ¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346648304,
      "tokens": 154453,
      "toolCalls": 64,
      "durationMs": 357456,
      "resultPreview": "{"dataset":"Advanced Market Metrics (biggest-gainers, biggest-losers, most-actives, industry-performance-snapshot, sector-performance-snapshot)","actually_called":true,"fmp_endpoints":["/stable/biggest-gainers","/stable/biggest-losers","/stable/most-actives","/stable/industry-performance-snapshot","/stable/sector-performance-snapshot"],"user_visible_features":["Home > Daily Scanners > \"Today's Toâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 6,
      "label": "trace:analyst-ratings",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "ae2805a5b2580ec4b",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346289183,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Analyst Ratings & Price Targets â the FMP `/stable/grades` â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787346605568,
      "tokens": 147464,
      "toolCalls": 62,
      "durationMs": 316385,
      "resultPreview": "{"dataset":"Analyst Ratings & Price Targets â the FMP `/stable/grades` endpoint (individual analyst rating actions: upgrades/downgrades/initiations/maintains, with per-action firm name, previous/new grade and previous/new price target)","actually_called":true,"fmp_endpoints":["grades"],"call_chain":"WRAPPER: FMPClient.get_grades(ticker, limit) -> GET {FMP_BASE_URL}/grades?symbol=X&limit=N â /Usersâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 7,
      "label": "trace:analyst-estimates",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "afcf64ef3f73561fe",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346606044,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Analyst Estimates & Price Target (FMP /stable/analyst-estimâ¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787347028401,
      "tokens": 163297,
      "toolCalls": 78,
      "durationMs": 422355,
      "resultPreview": "{"dataset":"Analyst Estimates & Price Target (FMP /stable/analyst-estimates, /stable/price-target-consensus)","actually_called":true,"fmp_endpoints":["analyst-estimates","price-target-consensus"],"call_chain":"WRAPPERS: backend/app/integrations/fmp.py:485 get_analyst_estimates(\"analyst-estimates\"); :504 get_price_target_consensus(\"price-target-consensus\"). (NOTE: :496 get_grades hits a DIFFEREâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 8,
      "label": "trace:calendar",
      "phaseIndex": 1,
      "phaseTitle": "Trace",
      "agentId": "a604c6d6faf4563ea",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787346609007,
      "queuedAt": 1787346285053,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Market Calendar â /stable/earnings-calendar (market-wide) +â¦",
      "promptPreview": "READ-ONLY analysis of /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App. Do NOT edit anything.

PRODUCT VISION (from documents/System Design/SYSTEM_DESIGN_GUIDELINES.md):
"Bloomberg Terminal for Novice Investors â professional-grade financial analysis accessible
through AI-powered personas." The paid feature is a 20-credit AI research report. The marketed
differentiators are the Home "App-Exclusiâ¦",
      "lastProgressAt": 1787347085212,
      "tokens": 178098,
      "toolCalls": 96,
      "durationMs": 476205,
      "resultPreview": "{"dataset":"Market Calendar â /stable/earnings-calendar (market-wide) + /stable/dividends","actually_called":true,"fmp_endpoints":["/stable/earnings-calendar (market-wide, from/to window) â fmp.py:511 get_earnings_calendar()","/stable/dividends?symbol=X â fmp.py:1121 get_dividend_history()","ADJACENCY RISK â /stable/earnings?symbol=X â fmp.py:525 get_earning_calendar_full() + fmp.py:630 get_historâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 9,
      "label": "rank",
      "phaseIndex": 2,
      "phaseTitle": "Rank",
      "agentId": "a0b711b75db9bd183",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787347089128,
      "queuedAt": 1787347087221,
      "attempt": 1,
      "promptPreview": "Produce a DROP RANKING for an indie developer cutting FMP datasets to lower a $600-700/mo quote.

TRACES (JSON):
[
 {
  "dataset": "Earnings Call Transcripts (FMP `earning-call-transcript`, `earning-call-transcript-dates`)",
  "actually_called": true,
  "fmp_endpoints": [
   "earning-call-transcript-dates",
   "earning-call-transcript"
  ],
  "call_chain": "Wrapper: `FMPClient.get_earning_call_traâ¦",
      "lastProgressAt": 1787347246325,
      "tokens": 125042,
      "toolCalls": 0,
      "durationMs": 157195,
      "resultPreview": "# FMP Dataset Drop Ranking â $600â700/mo Reduction

**Ground rule for this document:** every claim below is traced to a call site. Where a trace measured something in production (call counts, DB coverage, live probes), that measurement is cited, because measured facts beat architectural intuition â and in three cases here they invert it.

---

## 0. The headline finding, before the table

**No datâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 10,
      "label": "critique",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "aa0e9bd8ba21cd681",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787347250340,
      "queuedAt": 1787347248093,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "sed -n '45,60p' app/services/analyst_service.py; echo "=== â¦",
      "promptPreview": "Adversarially review this drop ranking. Find verdicts that are WRONG or dangerous.

RANKING:
# FMP Dataset Drop Ranking â $600â700/mo Reduction

**Ground rule for this document:** every claim below is traced to a call site. Where a trace measured something in production (call counts, DB coverage, live probes), that measurement is cited, because measured facts beat architectural intuition â and in â¦",
      "lastProgressAt": 1787347677387,
      "tokens": 186273,
      "toolCalls": 41,
      "durationMs": 427046,
      "resultPreview": "## Verified corrections â 7 findings, 2 of them ship-blocking

I traced every claim below to the current source. Line numbers are from files I read this session.

---

### C1 â SHIP-BLOCKER. Row 7's "already renormalizes out" is false *under the cut the email actually asks for*, and Â§5(c)'s remediation list is incomplete

**Wrong claim (table row 7):** "Feeds AI report? Yes â `wall_street` vital, â¦"
    }
  ],
  "totalTokens": 1535775,
  "totalToolCalls": 570
}