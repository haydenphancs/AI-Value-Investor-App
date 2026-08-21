# Build-or-buy: all 16 FMP datasets

> ⚠️ **PARTIALLY VERIFIED.** The **licensing critique ran and found 13 corrections** — appended at
> the bottom, and several change a verdict. The **effort critique failed twice on transient 529
> server errors**; its checks were done by hand instead and are summarised below. Treat the big
> effort numbers (fundamentals 25d, sector/industry 28d, ownership 42d) as **optimistic floors**,
> not estimates.
>
> **Hand-checked effort findings (2026-08-21):**
> - *Historical market cap, 2d* — **holds.** Verified one consumer only
>   (`signal_of_confidence_service.py:353`), and `weightedAverageShsOut` is already parsed at
>   `:484`. Minor risk: the code notes FMP returns `weightedAverageShsOut: 0` on some records.
> - *Movers/sector performance, 0.5d* — **holds.** `batch-quote` returns `changePercentage` and
>   `volume`; `_SECTOR_ETFS` and the batch path already exist.
> - *Sector/industry taxonomy, 28d* — **optimistic.** Verified **47 service modules touch
>   `sector`, 36 touch `industry`**, and BOTH benchmark tables are *indexed on the industry
>   string* (`idx_sector_benchmarks_industry_lookup`, `idx_industry_moat_benchmarks_lookup`).
>   A taxonomy migration means re-deriving both tables plus an LLM classifier plus a labelled
>   eval harness. Budget 40–60+, with an unknowable tail.
> - *Ownership rewrite, 42d* — **optimistic.** ~7,000 lines across the whale pipeline, 141 FMP
>   references, and a schema column literally named `whales.fmp_name`.

# Build-or-Buy Verdict â All 16 FMP Datasets (Solo Indie, Caydex)

## 0. The finding that reframes the whole exercise

**FMP is a bundle, and you cannot cancel a bundle partially.** Every dev-day spent self-sourcing saves exactly **$0/mo** until the *last* dataset is off it. Three datasets in the bundle â consensus EPS estimates, forward earnings dates, and market news â have **no free source at any effort level, structurally, forever**. So you will be paying a vendor on the day you launch and on the day you cancel FMP.

That means the real question is not *"FMP vs. free"*. It is **"FMP at $600â700/mo vs. a narrower vendor stack at maybe $400â550/mo"** â and that is a purchasing exercise measured in emails, not a 200-day engineering project.

Self-sourcing only pays when you can get *all the way* to zero FMP dependencies, which is a 9â14 month solo program.

---

## 1. The 16-row verdict table

| # | Dataset | Free/self-source? | How | Licence OK for display? | Effort (dev-days) | VERDICT |
|---|---|---|---|---|---|---|
| 1 | **Real-Time Market Data** (quote, batch-quote) | **No** | No free path exists. Lever is real-time vs. 15-min delayed, not free vs. paid | **No** â every free tier is personal-use (Alpha Vantage, Tiingo, Finnhub, Alpaca, Yahoo). Real-time consolidated = CTA/UTP fee schedules | 4 (vendor swap only) | **BUY** |
| 2 | **Historical Market Data** (daily EOD Â· intraday bars Â· historical market cap) | **Partly** | EOD: buy backfill once, self-accrete daily forever. Intraday: aggregate your own licensed ticks. Mkt cap: price Ã shares | EOD needs **no exchange licence** (>1 trading day old) but every free vendor's ToS refuses. Self-computed bars/cap are your own derived work â fully clear | 19 (8 + 9 + 2) | **MIXED** â BUY (EOD backfill, one-time) / SELF-COMPUTE (intraday, mkt cap) |
| 3 | **Index Market Data** (^GSPC ^IXIC ^DJI Â· ^TNX Â· ^VIX) | **Partly** | Levels â swap to SPY/QQQ/DIA and relabel. ^TNX â FRED `DGS10`. ^VIX â realized vol from SPY returns | **â ï¸ Index levels are separately licensed IP.** FRED's `SP500` carries an explicit S&P DJI reproduction prohibition; `VIXCLS` carries a Cboe copyright. `DGS10` is **clean** (US Govt work, no notice) | 9 (3 + 2 + 4) | **MIXED** â SELF-COMPUTE (ETF swap) / FREE-SOURCE (TNX) / **DROP** (VIX) |
| 4 | **Commodity Market Data** (WTI, NatGas Â· metals, ags) | **Yes** | WTI â FRED `DCOILWTICO`; NatGas â `DHHNGSP` (both EIA, public domain). Metals â physically-backed ETFs (GLD/SLV/PPLT/PALL) | Clean. FRED series carry **no** copyright notice. ETFs are ordinary securities â zero commodity-exchange IP | 7 (3 + 4) | **MIXED** â FREE-SOURCE (energy) / SELF-COMPUTE (metals) / **DROP** (copper, wheat â futures-based proxies lie) |
| 5 | **Advanced Market Metrics** (movers, sector performance) | **Yes** | Sort your own batch-quote rows by changePercentage / volume; sector perf via the `_SECTOR_ETFS` SPDR map already in the code | Fully clear â derived data you computed | **0.5** | **SELF-COMPUTE** |
| 6 | **Fundamental Financial Statements** (income, balance, cash flow, ratios, key-metrics, Â±TTM) | **Yes** | SEC EDGAR XBRL `companyfacts`. **Ratios + key-metrics are a pure function of the statements + a quote you already fetch â 4 of 7 endpoints evaporate** | **Best licence in the evaluation.** SEC: "may be copied or further distributed... without the SEC's permission." No contract, nothing to breach | 25 | **FREE-SOURCE** |
| 7 | **Revenue Product Segmentation** | Technically yes | Quarterly Financial Statement Data Sets `num.txt` (companyfacts strips dimensioned facts entirely â verified) | Clean licence. **Licence was never the constraint** | 20 (or 6â8 for a validated-subset middle path) | **DROP** (BUY if genuinely load-bearing) |
| 8 | **Company Profile & Executive** â *incl. sector/industry classification* | **Yes, painfully** | 109 of 428 refs already in `get_batch_quotes_bulk`. Descriptions **generated** from 10-K Item 1. Sector/industry: author your own taxonomy, classify with your own LLM | SEC public domain; Logo.dev free tier OK with attribution; **GICS unavailable to a solo dev at any price** (no self-serve tier) | **42** (14 + 28) | **SELF-COMPUTE** |
| 9 | **Search & Directory** (search-symbol, search-name) | **Yes** | Three SEC files, not one: `company_tickers_exchange.json` + `company_tickers_mf.json` + `investment_company_series_class.csv` â 89.9% coverage with usable names | Clean (SEC). **â ï¸ Reject nasdaqtrader.com** â its own page says "internal non-commercial usage only" in one paragraph and "without restriction" in the next | 6 | **FREE-SOURCE** |
| 10 | **ETF & Fund Data** (holdings Â· info Â· sector weightings) | **Yes** | SEC Form N-PORT + OpenFIGI for ISINâticker. Sector weightings = a GROUP BY over holdings. Expense ratio = hand-curated table | EDGAR clean. **â ï¸ Issuer CSV/XLSX files (iShares, SSGA, Vanguard) are PROHIBITED** â personal, non-commercial on all three checked | 22 (14 + 5 + 3) | **FREE-SOURCE** â but see staleness gap |
| 11 | **Index Constituents** (S&P 500, DJIA membership) | **Yes** | Build your own universe: SEC `company_tickers.json` ranked by marketCap from batch quotes. Ship as "Caydex Large Cap 500" | **â ï¸ The membership LIST is the litigated subject matter** (*Dow Jones v. CBOT*, 1982 â found copyrightable; fair use won only because the index had no licensing market, which is no longer true) | 3 | **SELF-COMPUTE** |
| 12 | **Market Calendar** (forward earnings dates Â· actual EPS Â· consensus EPS Â· surprise % Â· dividends) | **No, on the parts that matter** | Only XBRL actual EPS is free (10d). Forward dates: EDGAR structurally cannot supply them. Consensus: private broker IP. Dividends: ~30â35% coverage via 8-K prose | Actuals clean (SEC). Consensus/forward/dividends all encumbered or absent | **10 useful / ~48 wasted** | **BUY** (SELF-COMPUTE the XBRL actuals only) |
| 13 | **Analyst Data** (estimates, price-target consensus Â· ratings/grades) | **Substitute only** | Estimates â your own trend model (the whole dependency is **two scalars**: `revenue_cagr`, `eps_cagr`). Ratings â no substitute possible | Substitute is your own derived work â clean, **but must be relabelled**. Real consensus is licensed per-vendor, no free path | 15 (11 + 4) | **MIXED** â SELF-COMPUTE (estimates, as a trend model) / **DROP** (ratings, targets, beat-history) |
| 14 | **Market News** | **No** | â | No free source with commercial display rights (the Marketaux trap) | n/a | **BUY** |
| 15 | **Earnings Call Transcripts** | **No** | <1% of 8-Ks carry them; both consumers already degrade | No free source | n/a | **DROP** |
| 16 | **Ownership & Political Disclosure** (Form 4 insider Â· 13F Â· Congressional) | **Yes** | SEC EDGAR + House/Senate disclosure portals | Free and redistributable, **except** Congressional carries the 5 U.S.C. Â§13107(c) statutory restriction | **~42** (13â16 each) | **FREE-SOURCE** â but it is a **rewrite, not an adapter** (~7,000 lines, 141 FMP refs, a column literally named `whales.fmp_name`) |

### Where a row splits (sub-verdicts)

| Parent | Sub-dataset | Effort | Verdict | The one thing that decides it |
|---|---|---|---|---|
| 2 | Daily EOD price history | 8 | BUY (one-time) | No exchange licence needed â so buy backfill **once**, self-accrete forever. Converts recurring â fixed. |
| 2 | Intraday bars | 9 | SELF-COMPUTE | Your need is shallow (5-min bars, ~3 days). A bar you computed from a tick you licensed is yours. |
| 2 | Historical market cap | 2 | SELF-COMPUTE | Only one consumer, and it only ever asks for **quarter-end** values â the daily `limit=2000` fetch is waste. |
| 3 | ^GSPC / ^IXIC / ^DJI | 3 | SELF-COMPUTE | ETF swap. Cost: users see ~600 not ~6,000. |
| 3 | ^TNX | 2 | FREE-SOURCE | `DGS10` verified clean; you already own `fred.py`. Loses intraday tick only. |
| 3 | ^VIX | 4 | **DROP** | Cboe copyright. Do **not** substitute VIXY/UVXY â roll decay makes that affirmative misinformation. |
| 4 | WTI + Henry Hub | 3 | FREE-SOURCE | Exact benchmark spot, not a proxy. Avoids the USO/UNG contango trap. |
| 4 | Gold/silver/platinum/palladium | 4 | SELF-COMPUTE | Physically-backed only. Copper (CPER) and wheat (WEAT) are futures-based â drop. |
| 8 | Profile fields | 14 | SELF-COMPUTE | 109/428 refs already in hand; description (82 refs) is *generated*, not sourced. |
| 8 | **Sector & industry taxonomy** | **28** | SELF-COMPUTE | **SIC tops out at 64.2% industry accuracy; 37 industries vanish; every equity REIT collapses to "REIT - Mortgage".** |
| 10 | N-PORT holdings | 14 | FREE-SOURCE | 82â143 days stale. **SPY is your most-viewed ETF and your stalest (143 days).** |
| 10 | ETF info (AUM, inception) | 5 | FREE-SOURCE | Expense ratio is **not in the filing** â hand-curate ~200 ETFs. |
| 10 | Sector weightings | 3 | SELF-COMPUTE | Cheapest win in the group once holdings exist â it's a GROUP BY. |
| 12 | Reported actual EPS (GAAP) | 10 | SELF-COMPUTE | Safe to **display**; never safe to **difference** against consensus. |
| 13 | Analyst estimates | 11 | SELF-COMPUTE | Feeds only two scalars; `compute_quality_score` already renormalizes missing vitals. |
| 13 | Ratings / price targets | 4 | **DROP** | Worth â¤7% of one persona's weight. Highest price, lowest scoring value in the whole bundle. |

---

## 2. Quick wins â under 1 dev-day with a clean licence

**Be warned: this list is almost empty, and that is the honest answer.** Exactly one dataset in sixteen is a genuine sub-day build.

**The one real build:**

| Item | Effort | Why it's free money |
|---|---|---|
| **Advanced Market Metrics** â movers + sector performance | **0.5d** | Sort S&P 500 batch-quote rows you already fetch; sector performance via the `_SECTOR_ETFS` SPDR map already in the code. Derived data, zero licence surface, and **better than FMP's version, which doesn't even expose the volume it ranks by.** |

**The zero-day items that are worth more than any code you could write this week:**

1. **Send FMP a written question â today.** *"Does my $600â700/mo plan grant the right to **display/redistribute** data to my app's end users (not merely for my own access)? Does that include S&P DJI / Nasdaq / Cboe **index values**, and analyst estimates?"* This is the exact trap that already burned you twice (FMP personal plan, Marketaux): permissive **access** wrapped in restrictive **display** terms. **If the answer is no, you have live exposure today and every other line in this document is premature.**
2. **Stop / never start any ETF issuer-file scraping.** iShares, SSGA and Vanguard all restrict site content to personal, non-commercial use. `etf-scraper` on PyPI works fine and conveys no rights. Effort: 0 days, because the correct action is to not build it.
3. **Do not use nasdaqtrader.com symbol files or Stooq.** Both look like public infrastructure. Nasdaq says "personal and non-commercial use only" and asserts its symbols are proprietary; Stooq publishes **no terms at all**, and "no terms" is not permission â you cannot cite a licence you cannot find during an App Store dispute.

**Next-cheapest tier (2â3 days each, clean licence, do these first post-launch):**

| Item | Effort | Note |
|---|---|---|
| Historical market cap â derive | 2d | Removes an FMP call **from inside a hot `asyncio.gather`**, and the weighted-average share count is arguably the *more correct* denominator. |
| ^TNX â FRED `DGS10` | 2d | `fred.py` already has `get_observations` + a 6h cache. Add `DGS2`/`DGS30`/`T10Y2Y` free while you're there. |
| WTI + NatGas â FRED | 3d | Deeper history than you have now (`DCOILWTICO` starts 1986). |
| Index constituents â own universe | 3d | Also replaces `get_sp500_constituents()`, which is itself an FMP call you'd lose on cancellation. |

---

## 3. The irreducible core â what you must pay for, forever

These are not sourcing problems. They are **rights** problems, and no amount of engineering touches them.

| Dataset | Why it is irreducible |
|---|---|
| **Real-time / delayed US equity quotes** | Exchange IP under the CTA and UTP national market system plans. Published fee schedules, no free path through them. Every free API is either personal-use by licence or IEX-only with a redistribution ban. |
| **Consensus EPS estimates** | Private sell-side broker research aggregated under negotiated per-provider entitlements (I/B/E/S, FactSet, Zacks). **No government body collects estimates â there is no EDGAR equivalent, and there structurally cannot be one.** A "free API" offering consensus is either sub-licensing under terms forbidding your display, or scraping a licensee, which transfers no rights. |
| **Forward earnings dates** | **Proven, not assumed:** AAPL, MSFT and NVDA file *zero* advance earnings-date 8-Ks. Item 2.02's triggering event is the issuance of the release, so the 8-K can never precede it. Full-text search over a quarter found ~37 advance announcements against >10,000 8-Ks. |
| **Market news** | Publisher IP. No free source with commercial display rights. |
| **Dividends (practically)** | ~30â35% free coverage via 8-K prose. MSFT files **zero** dividend 8-Ks while paying four a year; AAPL, IBM, KO, MMM, MCD file 1â2. A dividend calendar missing Microsoft and Apple is the exact failure mode you've been bitten by twice. *One genuine free lever: since T+1 settlement (2024-05-28), **ex-date = record date** for US regular-way, so that field is derivable by rule.* |
| **Analyst ratings / price targets** | Opinions, not measurements. Nothing in the fundamentals implies them. Droppable (â¤7% of one persona's weight) â but not buildable. |
| **Index LEVELS, if you insist on the name** | S&P DJI and Nasdaq license index values separately from price data. Escapable via the ETF swap; not escapable if you want to print "S&P 500: 6,047". |
| **EOD historical backfill** | One-time, not recurring â the only irreducible item that doesn't recur. |

**What this means:** even after 200+ dev-days, you cannot get to $0/mo. The best achievable end-state is a *narrower, cheaper* vendor stack â likely quotes + estimates + dividends + news.

---

## 4. Total effort to self-source everything possible

| Scope | Dev-days | Calendar (solo, 5 productive days/wk) |
|---|---|---|
| Every row with a defensible SELF-COMPUTE / FREE-SOURCE verdict | **~205** | **~10 months** |
| + the three the research says *don't* build (segmentation 20, dividends 18, forward dates 30) | **~273** | ~13 months |
| **Realistic**, applying a 1.5Ã solo-dev factor to every line over 10 days | **~290** | **~14 months** |

Why the 1.5Ã: the two largest lines (fundamentals 25d, sector/industry 28d) are explicitly *dominated by unknown long-tail work* â XBRL fallback chains across ASC 606 / ASU 2016-02 tag eras and across banks, insurers, REITs and utilities; a hand-labelled eval harness before you dare re-key two benchmark tables. Neither number is knowable until you're inside it. None of the estimates include QA, App Store review cycles, or this project's own mandatory hardening pass â and on a fintech surface a wrong number is a product-integrity incident, not a bug.

### Against a 2-week launch

**It does not fit. It is not close.** Ten working days against ~205 dev-days is a **20Ã overrun** â you could complete roughly 5% of the program.

Concretely, in 10 days you could finish: movers/sector performance (0.5d), historical market cap (2d), the two FRED swaps (5d), and your own index universe (3d). That is 10.5 days, it saves **$0/mo**, and it consumes the entire runway to launch.

And here is the arithmetic that should end the debate: $700/mo is **$8,400/yr**. Even valuing your own time at zero, 205 dev-days is **10 months of not shipping product**. Priced at a modest $400/day of opportunity cost, it is ~$82,000 â roughly **ten years of FMP**.

**One extra tax nobody budgets for:** FMP ToS **Â§6.3** requires deleting all Data on termination, *"INCLUDING DATA CACHED,"* with an audit right attached. That puts `industry_universe.json` (9,188 FMP-labelled tickers), `benchmark_universe.json`, every row in `sector_benchmarks` and `industry_moat_benchmarks`, and ~70 hardcoded FMP industry strings in scope. **28 of the 205 days exist only because you're cancelling.** (Â§5's confidentiality clause arguably conflicts; Â§6.3 is the specific data clause and names cached data explicitly. That is a lawyer's question, not a developer's.)

---

## 5. The realistic recommendation â ~10 days to launch, $600â700/mo in hand

### Do this in the next 10 days (â4 dev-days of code)

**Day 0, two hours, highest-value action in this entire document:**
Email FMP and get a written answer on **display/redistribution rights**, explicitly naming index values and analyst estimates. Every other decision is downstream of that answer, and if it comes back "access only," you are non-compliant *today* â at which point the correct move is to fix the licence before launch, not to optimise cost.

**Then, in priority order:**

| | Item | Effort | Why now |
|---|---|---|---|
| 1 | **Advanced Market Metrics** self-compute | 0.5d | Free, better than what you're replacing, no licence surface. Already settled. |
| 2 | **Disclosure plumbing** â `"as of HH:MM ET"` stamps + a delay-disclosure string on every price surface | 1d | You need this for compliance regardless, and it is the **prerequisite** for ever moving to cheaper delayed data. Also the honest fix for N-PORT's 82â143 day staleness if you ever ship ETF holdings. |
| 3 | **Historical market cap** â derive from quarter-end close Ã `weightedAverageShsOut` (already parsed at `signal_of_confidence_service.py:484`) | 2d | Removes a network call from a hot `gather`; makes the number more defensible, not less. |
| 4 | **Index relabel to SPY/QQQ/DIA** â *conditional* | 3d | **Only if the Day-0 answer on index values is "no."** In that case this jumps to #1 and everything else waits: it is live legal exposure, not a cost optimisation. |

**Do not start before launch:** fundamentals (25d), sector/industry (28d), ETF N-PORT (22d), or the whale/13F/congress rewrite (42d). Each is a multi-week program with silent-wrong-number failure modes, and none of them saves a dollar until *all sixteen* are done.

### Keep buying, without guilt

Everything else. FMP at $600â700/mo is buying you the thing you literally cannot build: consensus estimates, forward earnings dates, dividends at full coverage, news, and normalised cross-company segment data with clean labels. **Revenue segmentation is the single line item where FMP most clearly earns its fee** â it is exactly the expensive-to-normalise, cheap-to-license value-add.

### The 6â12 month sequence, if you still want off FMP

Order matters â it is chosen so each phase is independently useful and the riskiest work comes last.

- **Phase 1 (~10d):** FRED swaps (TNX, WTI, NatGas) + your own index universe. Cleanly licensed, self-contained, decouples macro/commodity entirely.
- **Phase 2 (~31d):** Fundamentals (25) + Search (6). The cleanest licence in the evaluation and the biggest single chunk of the bundle. **Run it in parallel against FMP as a diff oracle for one full quarter before cancelling** â that is the cheapest QA you will ever get, and it is only available while you're still paying.
- **Phase 3 (~42d):** Company profile + sector/industry taxonomy. Unavoidable before cancellation (Â§6.3). **Highest silent-degradation risk in the program:** `sector_benchmark_lookup.py` falls back from industry to sector rows, so every frozen historical report will quietly drop from industry-relative to sector-relative comparison with no error, no log line, and no test failure. Plan an explicit reconciliation pass and a marker on pre-migration reports.
- **Phase 4 (~64d):** ETF N-PORT + ownership/13F/congress. Only if those features prove load-bearing after launch.
- **Then re-price.** What's left is quotes + consensus + dividends + news â a much narrower list. Twelve Data Venture ($414â499/mo, published external-display rights) plus Massive/Polygon for dividends plus a news vendor may or may not beat $600â700. Do that arithmetic **then**, with real usage data, not now.

### The faster path to a lower bill, which is not code

If cost is the actual driver, spend a week on **emails, not engineering**. Get written commercial-display quotes from Twelve Data (Venture), Massive (ex-Polygon.io), Intrinio (Startup), EODHD commercial, Benzinga, and Zacks. Twelve Data Venture at $414/mo annual is the only provider found with **both** a published price and published external-display rights, and it would undercut FMP immediately â at zero dev-days.

**Two things to get in writing before signing Twelve Data:** (1) its individual pricing page gates analyst data at Pro while its business page shows it across all tiers â confirm the Venture entitlement explicitly; (2) **provenance** â its nine analyst endpoint names map one-to-one onto Yahoo Finance's `quoteSummary` modules (`earningsTrend`, `recommendationTrend`, `financialData.targetMeanPrice`, `upgradeDowngradeHistory`). That may be industry-standard naming. Ask them to name the licensor and warrant the sub-licence chain. **A display right from a vendor who doesn't hold one is worth nothing â which is precisely the failure you have already paid for twice.**

---

## 6. Where the research is genuinely uncertain

Flagging these because an optimistic estimate causes a missed launch, and an over-confident licence reading causes something worse.

| Item | Status |
|---|---|
| **Does FMP's own $600â700 plan grant display rights?** | **Unknown, and it is the single biggest open question in this document.** If not, the app may be non-compliant today. |
| Nasdaq.com / `api.nasdaq.com` earnings-calendar terms | Search-derived, **not** a verbatim fetch â nasdaq.com blocks automated access. Verify in a browser before relying on any reading. |
| spglobal.com index disclaimers | 403-blocked on repeated fetch; text is search-surfaced. **Needs a human eyeball** before you rely on it either way. |
| Invesco ETF terms | Terms page 404'd on direct fetch. Pattern across BlackRock / SSGA / Vanguard is uniform, so the inference is strong but unverified for Invesco specifically. |
| FMP Â§6.3 (delete cached data) vs. Â§5 (may retain obtained information) | Genuine textual conflict. Â§6.3 is the specific clause and carries an audit right, so a lawyer likely reads it as controlling â **but that is a legal call, not an engineering one.** |
| Fundamentals (25d) and sector/industry (28d) | Both explicitly pessimistic *and* explicitly long-tail-dominated. These are the two most likely to overrun. |
| The ~42d for insider/13F/congressional | Extrapolated from "13â16 days each" against a ~7,000-line existing pipeline with 141 FMP references. Treat as a floor, not an estimate. |
| N-PORT staleness on active/thematic ETFs | Measured at 82â143 days. For broad index ETFs, fine with an as-of label. **For active or high-turnover ETFs I would not display it at all** â an undated holdings table next to a live price is misinformation, not staleness. |",
    "licensing_critique": null,
    "effort_critique": null
  },
  "workflowProgress": [
    {
      "type": "workflow_phase",
      "index": 1,
      "title": "Research"
    },
    {
      "type": "workflow_phase",
      "index": 2,
      "title": "Synthesize"
    },
    {
      "type": "workflow_phase",
      "index": 3,
      "title": "Verify"
    },
    {
      "type": "workflow_agent",
      "index": 1,
      "label": "res:prices",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "aaf0e5ac853204912",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787349439489,
      "queuedAt": 1787349436939,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Price data",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦",
      "lastProgressAt": 1787350037664,
      "tokens": 120468,
      "toolCalls": 41,
      "durationMs": 597116,
      "resultPreview": "{"group":"Price data","datasets":[{"dataset":"Real-Time Market Data (quote, batch-quote) â US equities","free_or_self_source_exists":false,"licence_for_commercial_display":"NO free source grants it. Verified refusals, all primary-source: Alpha Vantage ToS Â§2.a grants use 'for personal, non-commercial use' and Â§2.a.iii classifies as commercial any plan to 'use or provide information accessed througâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 2,
      "label": "res:fundamentals",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a7ba54b7880611517",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787349439372,
      "queuedAt": 1787349436939,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Fundamentals",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦",
      "lastProgressAt": 1787349945748,
      "tokens": 119605,
      "toolCalls": 30,
      "durationMs": 505200,
      "resultPreview": "{"group":"Fundamentals","datasets":[{"dataset":"Fundamental Financial Statements (income statement, balance sheet, cash flow, key-metrics, key-metrics-ttm, ratios, ratios-ttm)","free_or_self_source_exists":true,"licence_for_commercial_display":"PUBLIC DOMAIN â unrestricted commercial display. Verified verbatim first-hand at https://www.sec.gov/about/privacy-information: \"Information presented on â¦"
    },
    {
      "type": "workflow_agent",
      "index": 3,
      "label": "res:profile-search",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a4cd908629bf30272",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787349440479,
      "queuedAt": 1787349436939,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Company identity",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦",
      "lastProgressAt": 1787350206605,
      "tokens": 154831,
      "toolCalls": 66,
      "durationMs": 766057,
      "resultPreview": "{"group":"Company identity","datasets":[{"dataset":"Company Profile & Executive (profile, company-outlook) â EXCLUDING sector/industry, which is carved out into its own row below","free_or_self_source_exists":true,"licence_for_commercial_display":"MIXED, all clean. (1) SEC EDGAR â public domain, verified verbatim at https://www.sec.gov/privacy under 'Website Dissemination': 'Information presented â¦"
    },
    {
      "type": "workflow_agent",
      "index": 4,
      "label": "res:etf-index-constituents",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a35e48d80ec1ec581",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787349440199,
      "queuedAt": 1787349436939,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "ETF and constituent lists",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦",
      "lastProgressAt": 1787350068832,
      "tokens": 129778,
      "toolCalls": 46,
      "durationMs": 628284,
      "resultPreview": "{"group":"ETF and constituent lists","datasets":[{"dataset":"ETF issuer daily holdings files (iShares / SPDR / Vanguard / Invesco website CSV+XLSX)","free_or_self_source_exists":true,"licence_for_commercial_display":"PROHIBITED. All major issuers restrict site content to personal, non-commercial use. iShares/BlackRock: content is 'solely for personal, non-commercial use. Accordingly, you may not câ¦"
    },
    {
      "type": "workflow_agent",
      "index": 5,
      "label": "res:calendar",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "aac8d68348931ff17",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787349439457,
      "queuedAt": 1787349436939,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Market Calendar",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦",
      "lastProgressAt": 1787350203634,
      "tokens": 136156,
      "toolCalls": 49,
      "durationMs": 763086,
      "resultPreview": "{"group":"Market Calendar","datasets":[{"dataset":"Earnings calendar â FORWARD (upcoming) earnings dates","free_or_self_source_exists":false,"source_url":"https://data.sec.gov/submissions/CIK0000320193.json","licence_for_commercial_display":"NO LAWFUL FREE OPTION. The one source that IS freely redistributable (SEC EDGAR) does not contain this data at all. Every source that does contain it is persoâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 6,
      "label": "res:analyst",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a7981c7e3f7c104d7",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787349439312,
      "queuedAt": 1787349436939,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Analyst data",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦",
      "lastProgressAt": 1787349877807,
      "tokens": 126760,
      "toolCalls": 46,
      "durationMs": 438494,
      "resultPreview": "{"group":"Analyst data","datasets":[{"dataset":"Analyst Estimates & Price Target Consensus (analyst-estimates, price-target-consensus)","free_or_self_source_exists":true,"licence_for_commercial_display":"For real sell-side consensus: NONE EXISTS at any price under a free licence â confirmed structurally, not assumed (see reasoning). For the self-computed substitute: unrestricted, because the inputâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 7,
      "label": "synthesize",
      "phaseIndex": 2,
      "phaseTitle": "Synthesize",
      "agentId": "a27c40ab13f5f93c6",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787350211394,
      "queuedAt": 1787350208694,
      "attempt": 1,
      "promptPreview": "Write a build-or-buy verdict table covering ALL 16 FMP datasets for a solo indie developer.

RESEARCH (JSON):
[
 {
  "group": "Price data",
  "datasets": [
   {
    "dataset": "Real-Time Market Data (quote, batch-quote) â US equities",
    "free_or_self_source_exists": false,
    "licence_for_commercial_display": "NO free source grants it. Verified refusals, all primary-source: Alpha Vantage ToS Â§â¦",
      "lastProgressAt": 1787350471757,
      "tokens": 120403,
      "toolCalls": 0,
      "durationMs": 260362,
      "resultPreview": "# Build-or-Buy Verdict â All 16 FMP Datasets (Solo Indie, Caydex)

## 0. The finding that reframes the whole exercise

**FMP is a bundle, and you cannot cancel a bundle partially.** Every dev-day spent self-sourcing saves exactly **$0/mo** until the *last* dataset is off it. Three datasets in the bundle â consensus EPS estimates, forward earnings dates, and market news â have **no free source at aâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 8,
      "label": "verify:licensing",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "af3cf1898b8658004",
      "model": "claude-opus-5",
      "state": "error",
      "startedAt": 1787350476531,
      "queuedAt": 1787350473486,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "echo "=== congress refs ===" && grep -rln "congress\|senateâ¦",
      "promptPreview": "Adversarially review this document for LICENSING errors â the developer has already been
burned twice by assuming API access equals display rights.
DOC:
# Build-or-Buy Verdict â All 16 FMP Datasets (Solo Indie, Caydex)

## 0. The finding that reframes the whole exercise

**FMP is a bundle, and you cannot cancel a bundle partially.** Every dev-day spent self-sourcing saves exactly **$0/mo** until tâ¦",
      "lastProgressAt": 1787350672837,
      "error": "You've hit your session limit Â· resets 4:50pm (America/Denver)",
      "tokens": 129305,
      "toolCalls": 2,
      "durationMs": 196305
    },
    {
      "type": "workflow_agent",
      "index": 9,
      "label": "verify:effort",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "a4aaba8b3aad3258f",
      "model": "claude-opus-5",
      "state": "error",
      "startedAt": 1787350475713,
      "queuedAt": 1787350473486,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "sed -n '370,395p' backend/app/services/analyst_service.py; â¦",
      "promptPreview": "Adversarially review this document for EFFORT and FEASIBILITY optimism.
DOC:
# Build-or-Buy Verdict â All 16 FMP Datasets (Solo Indie, Caydex)

## 0. The finding that reframes the whole exercise

**FMP is a bundle, and you cannot cancel a bundle partially.** Every dev-day spent self-sourcing saves exactly **$0/mo** until the *last* dataset is off it. Three datasets in the bundle â consensus EPS esâ¦",
      "lastProgressAt": 1787350627323,
      "error": "You've hit your session limit Â· resets 4:50pm (America/Denver)",
      "tokens": 165021,
      "toolCalls": 28,
      "durationMs": 151609
    }
  ],
  "totalTokens": 1202327,
  "totalToolCalls": 308
}

---

# ⚖️ LICENSING CRITIQUE — 13 corrections

## LICENSING REVIEW â 13 corrections

### A. Material errors (change a verdict or create live exposure)

**A1 â The "self-accrete EOD forever" thesis dies on FMP Â§6.3, which the doc applies to only half its own scope.**
Row 2 and its sub-verdict ("buy backfill **once**, self-accrete foreverâ¦ Converts recurring â fixed") accretes daily closes from your *currently licensed* quote feed â which is FMP. Governing clause, quoted in the research from FMP ToS Â§6.3 Data Deletion: *"Upon termination of this Agreement, Customer must delete all Data it has received from FMP under all applicable Order Forms, INCLUDING DATA CACHED, and sign the Data Deletion Agreement in Exhibit A. Customer agrees that FMP has the right to perform an auditâ¦"* Â§4 of the doc applies this to `industry_universe.json`, `benchmark_universe.json`, `sector_benchmarks`, `industry_moat_benchmarks` and ~70 industry strings â and stops there. Not covered but equally in scope: the accreted `eod_prices` table (row 2), `intraday_bars` (row 2 sub), derived historical market cap (row 2 sub), the movers/sector-performance caches (row 5), and the frozen `ticker_report_data` snapshots. **Correction:** accretion must run off the feed you will still hold *after* cancellation, never off FMP, or you need a written Â§6.3 carve-out for derived/aggregated values. The "28 of the 205 days exist only because you're cancelling" figure is understated for the same reason.

**A2 â "Derived work = fully clear" is overbroad and is repeated in four places.**
Row 2 ("Self-computed bars/cap are your own derived work â fully clear"), row 5 ("zero licence surface"), row 13, and Â§2. Self-computation clears the **exchange entitlement** (the marketdata.app basis); it does not clear the **source vendor's contract**. The research supplies the counter-examples verbatim: Finnhub â *"agree to not redistribute or share access to data or derived results from the data obtained from Finnhub"*; Alpha Vantage EULA Â§2.a.iii captures providing information *"directly or indirectly"*; FMP Â§6.3 names cached data. **Correction:** rephrase everywhere as "clear of exchange licensing; still governed by the source feed's derived-works terms."

**A3 â Â§2's only sub-day build has an undisclosed S&P and GICS dependency, and contradicts Â§2's own next tier.**
Â§2: *"Sort S&P 500 batch-quote rows you already fetchâ¦ Derived data, zero licence surface."* Three problems: (i) the S&P 500 **membership list** is what row 11 itself flags as the litigated subject matter â *Dow Jones & Co. v. Board of Trade*, 546 F.Supp. 113 (S.D.N.Y. 1982), compilations copyrightable, fair use turning on the index having no licensing market, a premise dead in 2026; (ii) `get_sp500_constituents()` is an FMP call, so the cached list is Â§6.3 data â Â§2's own next-cheapest tier says exactly this four rows later; (iii) sector performance via the Select Sector SPDR map labels output with **GICS sector names**, and GICS taxonomy strings are MSCI/S&P DJI product, which row 8 says is "unavailable to a solo dev at any price." **Correction:** the quick win must rank over the SEC-derived self-defined universe (row 11, 3d) and use sector labels you author. It is a 3.5-day item sequenced *after* row 11, not a 0.5-day one â which removes the doc's claim that exactly one sub-day build exists.

**A4 â CUSIP/ISIN is absent from row 10 entirely, and it is the one identifier trap in the ETF path.**
N-PORT positions carry only name, title, **CUSIP** and **ISIN** â no ticker, which is why the doc reaches for OpenFIGI. The research's finding the doc dropped: a US ISIN *embeds* the CUSIP (`US0003602069` = "US" + CUSIP `000360206` + check digit), so rendering the ISIN discloses a CUSIP Global Servicesâlicensed identifier. **Correction:** add a hard rule to row 10 and to Â§2's zero-day list â CUSIP and ISIN are internal join keys only, never rendered. Note also that OpenFIGI's grant (*FIGI identifiers "may be freely reproduced, distributedâ¦ for any purpose, commercial or non-commercial"*) covers the FIGI you get back; it does not launder the CUSIP you put in.

**A5 â Row 16 is marked FREE-SOURCE with the clause that could forbid the use buried as a trailing exception.**
5 U.S.C. Â§13107(c) prohibits obtaining or using the reports *"for any commercial purpose"*, with a carve-out for news and communications media dissemination to the general public. Whether a credit-metered fintech app sits inside that carve-out **is** the commercial-display question this document exists to answer, and it does not appear in Â§6. Separately: the research supplied no group covering Ownership/Political Disclosure at all, so row 16's licence cell is unsourced in this evidence base. **Correction:** split the row â Form 4 and 13F stay FREE-SOURCE (EDGAR); Congressional becomes FREE-SOURCE (conditional), moved into Â§6 pending counsel and a direct read of the House/Senate portal terms.

**A6 â The Day-0 FMP email omits news and identifiers, and applies the warranty test asymmetrically.**
Â§2/Â§5 name only "S&P DJI / Nasdaq / Cboe index values, and analyst estimates." Add: (i) **market news** â FMP redistributes publisher IP, yet Â§5 says "keep buying" for news without ever demanding the sub-licence-chain warranty Â§5 demands of Twelve Data (*"A display right from a vendor who doesn't hold one is worth nothing"*); (ii) **consensus provenance** â which aggregator (I/B/E/S, Zacks, FactSet) and warranted sub-licence; (iii) any **CUSIP/ISIN-bearing** fields; (iv) written Â§6.3 scope on derived and aggregated values. The incumbent is being held to a lower standard than the prospective replacement.

### B. Correct licences with conditions dropped

**B1 â SEC's three conditions never appear anywhere in the doc.** Row 6 says *"No contract, nothing to breach."* The grant at sec.gov/about/privacy-information is conditioned: *"Please consider appropriate citation to the SEC as the source"*; *"Please do not use the SEC seal or any of the other logos"*; and 'SEC', 'EDGAR', 'EDGARLink' are registered SEC trademarks that may not appear in a trade name, trademark or domain. **Correction:** a "Source: SEC EDGAR" line on every SEC-derived surface; no seal; no SEC/EDGAR in the app name or domain. (User-Agent + â¤10 req/s are access conditions, not licence terms â keep them separate.)

**B2 â Logo.dev's attribution is a hard production condition, and no data licence conveys trademark rights.** Row 8's *"Logo.dev free tier OK with attribution"* under-states it: the free tier requires a **visible attribution link to logo.dev on the production public marketing site** (footer or about page â staging/localhost does not count); any paid plan removes it. And the logo is the issuer's trademark â the basis for showing it is nominative/identifying use, unchanged by dropping FMP. Neither lands in any action list.

**B3 â GICS sector *names* are themselves MSCI/S&P DJI product.** The doc correctly kills GICS in row 8, then ships GICS-shaped labels in row 5 (sector performance), the row-10 sector-weightings sub-row, and the `sector_benchmarks` rebuild. Use plain-English labels you author; do not ship verbatim GICS taxonomy strings.

### C. Overstated certainty â belongs in Â§6

**C1** â *"EOD needs **no exchange licence** (>1 trading day old)"* is stated as settled fact and carries the entire recurringâfixed thesis, but rests on one vendor education page (marketdata.app), not the CTA/UTP plan documents. Â§6 already flags weaker claims than this.

**C2** â The **Nasdaq top-10 safe harbour is missing.** Nasdaq's Index Data Usage and Distribution Policy exempts an External Subscriber from a Global Data Agreement where distribution is limited to (a) index changes Nasdaq previously published via press release, (b) *"no more than the top ten (10) constituent members/weights"*, (c) index level performance from end-of-day levels. S&P publishes no equivalent. Row 11 treats all index membership uniformly and loses the single place a real index list is lawful.

**C3** â **Row 10 reproduces an index constituent list.** A full SPY N-PORT holdings table is functionally the S&P 500 membership list. The defence â it is the *fund's* own disclosed portfolio filed with a federal regulator, not the index provider's list â is an argument, not a settled answer, and appears nowhere in the doc while row 11 warns hard about the same object. Name the tension; consider capping displayed constituents (C2 is the industry's own read of where that line sits).

**C4** â **Row 3's ETF swap is mislabelled SELF-COMPUTE.** SPY/QQQ/DIA prices are consolidated exchange market data under the row-1 licence; you are substituting a vendor, not computing anything. The swap's cost is zero *incremental* licence, not zero licence. Also, "S&P 500 ETF (SPY)" still uses the S&P 500 mark â defensible as nominative reference to the fund's stated objective, but the research's third option carries the matching prohibition the doc omits: a Caydex-computed cap-weighted composite must **not** be called "the S&P 500."

**C5 â precision, ticker symbols.** Â§2 bullet 3 rejects nasdaqtrader.com partly because Nasdaq *"asserts its symbols are proprietary."* The operative reason is the **file's** terms (*"internal non-commercial usage only"* on the Fund Network content; *"personal and non-commercial use only"* on the general copyright page), not symbol IP â the same tickers arrive public-domain via the three SEC files in row 9. Leaving the symbol-proprietary assertion unreconciled invites the wrong inference in both directions.

### Checks that came back clean
No vendor free tier is proposed for production anywhere in the doc â Alpha Vantage, Tiingo, Finnhub, Alpaca, Yahoo, Stooq, Twelve Data free and the ETF issuer files are all correctly rejected with the right clauses. FRED `DGS10` / `DCOILWTICO` / `DHHNGSP` are correctly distinguished from `SP500` (S&P DJI reproduction prohibition) and `VIXCLS` (Cboe copyright, permission running to FRED only). The Marketaux and FMP-personal-plan pattern is correctly generalised in Â§2.",
    "effort_critique": null
  },
  "workflowProgress": [
    {
      "type": "workflow_phase",
      "index": 1,
      "title": "Research"
    },
    {
      "type": "workflow_phase",
      "index": 2,
      "title": "Synthesize"
    },
    {
      "type": "workflow_phase",
      "index": 3,
      "title": "Verify"
    },
    {
      "type": "workflow_agent",
      "index": 1,
      "label": "res:prices",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "aaf0e5ac853204912",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787352689413,
      "lastProgressAt": 1787352689413,
      "cached": true,
      "resultPreview": "{"group":"Price data","datasets":[{"dataset":"Real-Time Market Data (quote, batch-quote) â US equities","free_or_self_source_exists":false,"licence_for_commercial_display":"NO free source grants it. Verified refusals, all primary-source: Alpha Vantage ToS Â§2.a grants use 'for personal, non-commercial use' and Â§2.a.iii classifies as commercial any plan to 'use or provide information accessed througâ¦",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 2,
      "label": "res:fundamentals",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a7ba54b7880611517",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787352689413,
      "lastProgressAt": 1787352689413,
      "cached": true,
      "resultPreview": "{"group":"Fundamentals","datasets":[{"dataset":"Fundamental Financial Statements (income statement, balance sheet, cash flow, key-metrics, key-metrics-ttm, ratios, ratios-ttm)","free_or_self_source_exists":true,"licence_for_commercial_display":"PUBLIC DOMAIN â unrestricted commercial display. Verified verbatim first-hand at https://www.sec.gov/about/privacy-information: \"Information presented on â¦",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 3,
      "label": "res:profile-search",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a4cd908629bf30272",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787352689413,
      "lastProgressAt": 1787352689413,
      "cached": true,
      "resultPreview": "{"group":"Company identity","datasets":[{"dataset":"Company Profile & Executive (profile, company-outlook) â EXCLUDING sector/industry, which is carved out into its own row below","free_or_self_source_exists":true,"licence_for_commercial_display":"MIXED, all clean. (1) SEC EDGAR â public domain, verified verbatim at https://www.sec.gov/privacy under 'Website Dissemination': 'Information presented â¦",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 4,
      "label": "res:etf-index-constituents",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a35e48d80ec1ec581",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787352689414,
      "lastProgressAt": 1787352689414,
      "cached": true,
      "resultPreview": "{"group":"ETF and constituent lists","datasets":[{"dataset":"ETF issuer daily holdings files (iShares / SPDR / Vanguard / Invesco website CSV+XLSX)","free_or_self_source_exists":true,"licence_for_commercial_display":"PROHIBITED. All major issuers restrict site content to personal, non-commercial use. iShares/BlackRock: content is 'solely for personal, non-commercial use. Accordingly, you may not câ¦",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 5,
      "label": "res:calendar",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "aac8d68348931ff17",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787352689414,
      "lastProgressAt": 1787352689414,
      "cached": true,
      "resultPreview": "{"group":"Market Calendar","datasets":[{"dataset":"Earnings calendar â FORWARD (upcoming) earnings dates","free_or_self_source_exists":false,"source_url":"https://data.sec.gov/submissions/CIK0000320193.json","licence_for_commercial_display":"NO LAWFUL FREE OPTION. The one source that IS freely redistributable (SEC EDGAR) does not contain this data at all. Every source that does contain it is persoâ¦",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 6,
      "label": "res:analyst",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a7981c7e3f7c104d7",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787352689414,
      "lastProgressAt": 1787352689414,
      "cached": true,
      "resultPreview": "{"group":"Analyst data","datasets":[{"dataset":"Analyst Estimates & Price Target Consensus (analyst-estimates, price-target-consensus)","free_or_self_source_exists":true,"licence_for_commercial_display":"For real sell-side consensus: NONE EXISTS at any price under a free licence â confirmed structurally, not assumed (see reasoning). For the self-computed substitute: unrestricted, because the inputâ¦",
      "promptPreview": "CONTEXT: Solo indie dev, SwiftUI + FastAPI investing app (Caydex), quoted $600-700/mo
by Financial Modeling Prep for a commercial data-display licence. Wants to know, for each dataset,
whether it can be SELF-COMPUTED from data already held, or sourced FREE and lawfully.

â ï¸ THE RULE THAT MATTERS MOST: for every "free source" you propose, state its **licence for
commercial display in a public consuâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 7,
      "label": "synthesize",
      "phaseIndex": 2,
      "phaseTitle": "Synthesize",
      "agentId": "a27c40ab13f5f93c6",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787352689415,
      "lastProgressAt": 1787352689415,
      "cached": true,
      "resultPreview": "# Build-or-Buy Verdict â All 16 FMP Datasets (Solo Indie, Caydex)

## 0. The finding that reframes the whole exercise

**FMP is a bundle, and you cannot cancel a bundle partially.** Every dev-day spent self-sourcing saves exactly **$0/mo** until the *last* dataset is off it. Three datasets in the bundle â consensus EPS estimates, forward earnings dates, and market news â have **no free source at aâ¦",
      "promptPreview": "Write a build-or-buy verdict table covering ALL 16 FMP datasets for a solo indie developer.

RESEARCH (JSON):
[
 {
  "group": "Price data",
  "datasets": [
   {
    "dataset": "Real-Time Market Data (quote, batch-quote) â US equities",
    "free_or_self_source_exists": false,
    "licence_for_commercial_display": "NO free source grants it. Verified refusals, all primary-source: Alpha Vantage ToS Â§â¦"
    },
    {
      "type": "workflow_agent",
      "index": 8,
      "label": "verify:licensing",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "aec04e64b27147be2",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787352691808,
      "queuedAt": 1787352689417,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "echo ok",
      "promptPreview": "Adversarially review this document for LICENSING errors â the developer has already been
burned twice by assuming API access equals display rights.
DOC:
# Build-or-Buy Verdict â All 16 FMP Datasets (Solo Indie, Caydex)

## 0. The finding that reframes the whole exercise

**FMP is a bundle, and you cannot cancel a bundle partially.** Every dev-day spent self-sourcing saves exactly **$0/mo** until tâ¦",
      "lastProgressAt": 1787352928037,
      "tokens": 131641,
      "toolCalls": 1,
      "durationMs": 236228,
      "resultPreview": "## LICENSING REVIEW â 13 corrections

### A. Material errors (change a verdict or create live exposure)

**A1 â The "self-accrete EOD forever" thesis dies on FMP Â§6.3, which the doc applies to only half its own scope.**
Row 2 and its sub-verdict ("buy backfill **once**, self-accrete foreverâ¦ Converts recurring â fixed") accretes daily closes from your *currently licensed* quote feed â which is FMPâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 9,
      "label": "verify:effort",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "a0e3a1142edbe8294",
      "model": "claude-opus-5",
      "state": "error",
      "startedAt": 1787352692643,
      "queuedAt": 1787352689417,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "cd frontend/ios/ios && grep -rl "price\b" --include="*.swifâ¦",
      "promptPreview": "Adversarially review this document for EFFORT and FEASIBILITY optimism.
DOC:
# Build-or-Buy Verdict â All 16 FMP Datasets (Solo Indie, Caydex)

## 0. The finding that reframes the whole exercise

**FMP is a bundle, and you cannot cancel a bundle partially.** Every dev-day spent self-sourcing saves exactly **$0/mo** until the *last* dataset is off it. Three datasets in the bundle â consensus EPS esâ¦",
      "lastProgressAt": 1787353120699,
      "error": "API Error: 529 Overloaded. This is a server-side issue, usually temporary â try again in a moment. If it persists, check https://status.claude.com.",
      "tokens": 153240,
      "toolCalls": 28,
      "durationMs": 423826
    }
  ],
  "totalTokens": 284881,
  "totalToolCalls": 29
}