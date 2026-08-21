# Build vs Buy — replacing the FMP datasets

> ⚠️ **Read the two critiques at the bottom before acting on the plan.** Both adversarial passes ran
> this time and both found real errors in the synthesis — including one BLOCKING licensing flaw and a
> schedule line item wrong by 4×. The synthesis is the argument; the critiques are the corrections.

# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days against a 10-day window, a 12.5Ã overrun, roughly six calendar months of solo work.** Even the stripped-down, honestly-degraded version of every dataset that *has* a degraded version sums to 28 days, and three of the seven datasets have no viable degraded version at all. Any plan that says "we'll self-source the SEC stuff in the next two weeks" is a missed launch.

The two-week launch is achievable, but only by **buying two datasets, shipping five features dark, and fixing one correctness bug.** That plan is in Â§3.

And one finding outranks everything else in this document, so it goes here rather than buried in Â§4:

> ### â ï¸ The Twelve Data tier trap â check this before reading further
>
> If the Twelve Data plan already purchased is **Grow ($29â79), Pro ($99â229), or Ultra ($329â999)**, the entire migration delivers **zero legal improvement over FMP.** Twelve Data's individual pricing page states the data is for "personal, internal, and non-commercial purposes." Grow grants only *Internal display access* â tooltip: "The data may be displayed but **cannot be programmatically processed, stored, transformed, or redistributed**," which forbids `ticker_news_cache`, `sector_benchmarks`, and every two-tier cache in the codebase. Pro and Ultra are *internal non-display*.
>
> The right to show data to end users exists on exactly one Twelve Data line item: **Business / Venture**, which carries *"External display data access"* and the card subtitle *"ideal for companies showcasing data on client-facing apps or websites."*
>
> **Corollary:** the 13F research recommends using "Twelve Data's cheap Grow-tier `/splits` and `/profile`" to close the split-adjustment and sector gaps. Under the licence findings from the profile research, **that recommendation is wrong.** Any Twelve Data field that reaches a rendered screen requires Venture. There is no cheap tier that legally feeds the app.

---

## 1. The two-week reality check

### 1a. Effort arithmetic

Every figure below is the research authors' own solo-dev estimate, for a developer who has not built that pipeline before, at this repo's hardening bar (math tests, outlier tests, schema-parity tests).

| Dataset | Full build (days) | Minimum honest degraded build | What the degraded build actually ships |
|---|---:|---:|---|
| Form 13F institutional holdings | **13** | 5 | Latest quarter only â no 8-quarter flow history, no split adjustment |
| Congressional trades (House + Senate) | **15** (+5â8 for OCR) | â | No degraded version exists. 15 days *already excludes* 13% of House and 6% of Senate filings, and the excluded filers are Khanna, McCaul, Rogers, Fleischmann |
| Insider transactions (Form 4) | **16** | 6 | Quarterly bulk only â **52 days stale today**, labelled honestly |
| SC 13D/G beneficial ownership | **15** | 7â8 | XML-only, which silently drops Ellison and every stable founder â not a real reduction |
| Revenue segmentation (product/geo) | **16** | 7â9 | Top ~200 tickers, product only, 3 fiscal years, manual override table |
| Earnings call transcripts | **not feasible** (40+ days, ongoing breakage) | 5â6 | Not transcripts â an EDGAR 8-K *guidance* substitute. Outlook text + CEO quote, no raised/lowered diff |
| Financial news | **12** | â | No viable degraded version. 12 days buys imageless US-equity-only corporate events; crypto, commodity, index and market-feed screens stay empty |
| Company profile (sector/industry) | **20** | â | No viable version. Sector-only is 5â6 days but silently collapses every industry benchmark to a sector benchmark |
| Legal / compliance plumbing (shared) | **2** | 2 | Shared token bucket, declared UA, Senate CSRF handshake, attribution, CUSIP suppression |
| **TOTAL** | **125** | **28** | (28 covers only the five datasets that *have* a degraded version) |

**125 dev-days Ã· 10 working days = 12.5Ã.** The degraded subset is still **2.8Ã** over, and it leaves congressional trades, news, and company profile entirely unsolved.

### 1b. What those 125 days do *not* include

The estimates cover only the seven replacement pipelines. Not counted anywhere above:

- The **Twelve Data core market-data migration itself** (quotes, historical, fundamentals) â the reason this whole exercise exists. Budget 3â5 days pessimistically.
- **iOS work to disable features gracefully** â five surfaces need empty states, and every `*Response` field that goes away must become `Optional` with an updated schema-parity test, or the decoder crashes in production (CLAUDE.md invariant #3).
- **QA, regression sweep, and the hardening pass** the repo's own rules mandate.
- **App Store review.** Code freeze is not day 10, it is roughly day 7â8. A single rejection costs a cycle.

Applying a pessimistic +20% contingency to the build numbers (which the instruction to err pessimistic warrants â every one of these estimates has a "this is where the schedule dies" clause attached) pushes the full plan to ~150 days.

### 1c. Realistic engineering capacity for these seven datasets

10 working days â 4 (core Twelve Data migration) â 2 (QA/release/review buffer) = **~4 days of actual capacity** for everything in this document.

Four days. Against a 125-day plan.

---

## 2. Per-dataset decision table

| Dataset | Free source with display rights? | Build effort (days) | Cheapest vendor **with actual display rights** | Vendor price | **Recommendation** |
|---|---|---:|---|---|---|
| **Company profile** (sector/industry) | â No â SEC gives SIC only, and SICâ153 FMP industries is **not a function** at any effort | 20 (result is *worse* than what it replaces) | **Twelve Data Venture** â "External display data access" | **from $149/mo** (card $499; $414/mo annual) | **BUY â mandatory.** Load-bearing: 132 sector refs, 121 industry refs, all `sector_benchmarks` join keys |
| **Financial news** | â No â EDGAR yields filings, not news; every consumer feed is non-commercial by its own terms | 12 (and 4 screens still empty) | **Marketaux Standard** â *pending written approval*; fallback GNews Essential | **$49/mo** (GNews â¬49.99) | **BUY + reduce display surface.** Ship headline + source + timestamp + link only |
| **Form 13F institutional holdings** | â **Yes** â SEC Form 13F Data Sets, public domain, display-legal | 13 (5 staged) | **None.** All five vendors forbid display on affordable tiers | n/a | **DEFER.** Build post-launch â it is the strongest free path in the report, it just doesn't fit |
| **Insider transactions (Form 4)** | â **Yes** â SEC Insider Transactions Data Sets + daily index | 16 (6 degraded) | sec-api.io $49/mo (**ambiguous** â needs email) / Intrinio Startup $333/mo (**explicit**) | $49 / $333 | **DEFER.** Free source is *legally cleaner than every paid option* |
| **Earnings call transcripts** | â No â measured dead on EDGAR (<1% of 8-Ks) | not feasible | **API Ninjas Business** â the only self-serve grant | $99/mo annual ($149 monthly) | **DROP FOR LAUNCH.** Cost of dropping is contained (see Â§3d) |
| **Revenue segmentation** | â Yes (XBRL) but 16 days / 7â9 scoped | 16 | **Intrinio Startup** â "Commercial Use and Display Rights", self-serve | $333/mo â $666 â $999 | **DROP FOR LAUNCH**, defer build |
| **SC 13D/G beneficial ownership** | â Yes but stale *by design* â Ellison's freshest 13G figure is as of **2023-12-31** | 15 | sec-api.io $49/mo (**ambiguous**) | $49/mo | **DROP FOR LAUNCH.** No amount of money fixes the staleness |
| **Congressional trades** | â ï¸ Yes technically, but restricted by **statute** (5 U.S.C. Â§13107(c)), and 13%/6% of filings are unparseable scans | 15 (+5â8 OCR) | **Disclosed Capitol** â *likely yes, unconfirmed*; FMP commercial quote-only | $5â$100/30d | **DROP FOR LAUNCH.** Pursue the FMP quote + Disclosed Capitol email in parallel |

### 2a. The pattern that governs every row

**Cheap tiers sell API *access*. They almost never sell *display*.** Across all seven datasets and ~30 vendors examined, the vendors with published, self-serve, unambiguous display rights number **three**:

| Vendor | Price | Grants | Covers |
|---|---|---|---|
| Twelve Data **Venture** | from $149/mo | "External display data access" | profile/sector/industry, splits, core market data |
| **Intrinio Startup** | $333 â $666 â $999/mo | "Commercial Use and Display Rights" | insider, fundamentals, revenue segmentation dimensions, EOD |
| **API Ninjas Business** | $99/mo annual | ToS Â§3.2 carves display out of the redistribution ban | earnings transcripts |

Everything else is either (a) explicitly personal-use â Finnhub bans even *"derived results"* at $3,500/mo; Alpha Vantage's commercial definition catches any activity letting "individuals or entities other than User" access data "directly or indirectly"; Quiver prints "No Commercial Use Rights" on both self-serve tiers; Unusual Whales forbids redistribution at $375/mo with account termination as the stated penalty; WhaleWisdom bars providing data "to anyone" â or (b) a quote-only enterprise wall identical to the one FMP already presented.

**Being free and pre-revenue is not an exemption anywhere.** FMP's ToS closes it explicitly: *"irrespective of whether such usage is complimentary or paid."*

---

## 3. The two-week plan that actually ships

### 3a. Buy (day 0 â before writing any code)

1. **Twelve Data Venture.** Non-negotiable. Without it the sector/industry taxonomy has no legal source and `sector_benchmarks`, `industry_moat_benchmarks`, `industry_dossier_cache` and `benchmark_universe.json` (5,704 tickers / 153 industries) all lose their join key. Start at the $149 credit sub-tier; **verify during the trial** that it includes Fundamentals and enough credits (5,704 Ã 10 = 57,040 credits per nightly refresh).
2. **Marketaux Standard, $49/mo â but send the approval email first.** Their only legal document is unmaintained 2021 website boilerplate granting "personal, non-commercial use," whose Prohibited Activities clause carves out endeavours "specifically endorsed or approved by us." One line of written approval closes the gap. **Send this before writing the adapter**, because the answer determines whether you build against Marketaux or GNews.

### 3b. Send today, decide later (zero dev-days, high option value)

| Email | To | Question | If yes |
|---|---|---|---|
| **FMP Data Display & Licensing Agreement quote** | FMP sales | Price for a free consumer iOS app; ask specifically about a **time-boxed free-beta waiver** | Migration becomes optional. See Â§6 |
| sec-api.io tier question | support@sec-api.io | "Solo dev, free consumer iOS app, no paywall, displaying Form 4 and 13D/G data â which tier?" | $49/mo rescues insider **and** 13D/G with ~3â4 days integration instead of 31 days of building |
| Disclosed Capitol confirmation | Disclosed Capitol | Written confirmation that public display in a free iOS app is permitted | $5â100/30d restores congressional trades |
| EODHD commercial quote | EODHD sales | They advertise commercial onboarding in **3 business days** | Byte-identical FMP taxonomy *plus* IPO date |

Four emails. Roughly one hour. Collectively they are the highest-expected-value hour in the two weeks.

### 3c. Build (the only self-sourcing that fits)

**Nothing.** There is no SEC pipeline that fits inside four days of capacity. The 13F holdings-only build (5 days) is the closest and is genuinely tempting â but it is 125% of the available budget with zero slack, and 23.3% of filings hide the multi-manager row-splitting trap that understates Berkshire's Apple position by 2.8Ã. Shipping that wrong is worse than shipping it absent.

**Day-5 gate:** if the Twelve Data core migration and the news swap both land by end of day 5 with the regression sweep green, spend days 6â8 on the 13F holdings-only build. Otherwise, do not start it. Make this an explicit go/no-go, not a hope.

### 3d. Go dark â feature by feature

This is the concrete cut. Each of these needs an honest empty state, **not** a spinner, a zero, or a silent fallback.

| # | Feature / surface | Treatment at launch | Restore |
|---|---|---|---|
| 1 | **Whale tab** â 13F holdings, hedge-fund flow chart, sector allocation, portfolio value; whale follow; Home "exclusive signals" whale card | Hide the tab section. Copy: *"Institutional holdings are being rebuilt from SEC filings and return shortly."* Keep the whale registry and follow UI if it degrades cleanly; otherwise hide both | Sprint 1 (5 days holdings-only, +8 days flow history) |
| 2 | **Congress tab / congressional trading rows** on ticker detail + Home congress signals + Hidden Market Signals module (which reuses `holders_response`) | Hide entirely, or link out to disclosures-clerk.house.gov and efdsearch.senate.gov | Sprint 1â2 (buy) or Sprint 3+ (build, 15 days) |
| 3 | **Insider transactions chart** (Holders tab) | Hide. **Do not** render an empty chart or a zero net-flow verdict | Sprint 2 (6 days for the honestly-labelled quarterly-bulk version) |
| 4 | **Founder / major-holder stake** (SC 13D/G, e.g. Ellison on ORCL) | Hide, **or** hand-seed top ~100â200 tickers from the DEF 14A with a mandatory `"as of <proxy date>, per DEF 14A"` label (2â3 days â only if the day-5 gate opens) | Sprint 3+, or sec-api.io if the email lands |
| 5 | **Revenue by product / by geography** (Financials tab) | Hide the section | Sprint 2â3 (7â9 days scoped) or Intrinio |
| 6 | **CFO/CEO verbatim quote** in the report | Remove the field | Only if API Ninjas Business is bought |
| 7 | **Guidance status** (raised / maintained / lowered) | **See Â§3e â this one is a bug, not a feature cut** | Sprint 2 (5â6 days for the EDGAR 8-K extractor) |
| 8 | **News cards** (5 screens) | Stay live, reduced surface: headline + source name + timestamp + link + your own Gemini `summary_bullets`. **Drop the publisher snippet and the publisher image.** Replace `thumbnail_url` with a generated ticker/sector card | Restore images only under a licence that actually grants them (Benzinga) |
| 9 | **Moat pillars** (Switching Costs, Network Effects) | Degrade, don't hide. Switching Costs drops 2â1 driver, falls below `_MIN_METRICS_FOR_SCORE` and routes to the **already-built Phase 3D Gemini grounded fallback**. Network Effects goes 4â3 drivers and stays `_CONFIDENCE_HIGH`. TAM falls to its `industry_tam` tier | No action needed |

**iOS cost of going dark: budget 2 days**, and it is real work â every removed field must become `Optional` in the Pydantic `*Response` model *and* the Swift DTO, with the schema-parity test updated in the same change. A required field that disappears is a decode crash in production.

### 3e. The one non-negotiable correctness fix

`_overlay_ai_guidance()` in `ticker_report_data_collector.py` coerces guidance status to **`"maintained"`** whenever the AI cannot produce a verbatim source quote. Remove the transcript and **every ticker in the app confidently displays "maintained."**

That is not a missing feature â it is a wrong answer shown with certainty, on a financial surface, frozen permanently into point-in-time report snapshots that are never re-fetched. It is precisely the class of defect the whale-tab "$3T exodus" memory note records.

**Fix before launch: add an explicit `not_disclosed` state and surface it honestly.** ~0.5 day. This is the single highest-value half-day in the plan.

### 3f. Day-by-day

| Day | Work |
|---|---|
| **0** (today) | Send all four emails. Start the Twelve Data Venture trial. Begin the 25â30 ticker sector/industry parity sample |
| **1â3** | Twelve Data core migration + profile adapter behind the existing `fmp.py::get_company_profile` boundary. CIK from SEC `company_tickers.json` (free, public domain). Logo from TD `/logo` (1 credit). IPO date: backfill once from existing FMP data and freeze, or null it â it has 4 consumers |
| **3â5** | News adapter: 15 FMP call sites â one thin integration module, mapped into the existing `ticker_news_cache` row shape. Reduced display surface across 5 screens. Schema-parity test |
| **5** | **GATE.** Green regression sweep? â optional 13F holdings-only build days 6â8. Not green? â skip it |
| **6â7** | Feature-disable pass across the 5 dark surfaces. `Optional` field sweep + parity tests. Guidance `not_disclosed` fix |
| **8** | Full regression, adversarial hardening pass per CLAUDE.md, `pytest -x` |
| **9** | Submit to App Store review |
| **10** | Buffer for rejection |

---

## 4. What it costs

### Recommended launch mix

| Item | Monthly | Notes |
|---|---:|---|
| Twelve Data **Venture** (entry credit tier) | **$149** | Range $149â$499 depending on which sub-tier actually carries Fundamentals + 57k credits/night. **Unverified â confirm in trial** |
| Marketaux **Standard** | **$49** | Contingent on written approval. Fallback: GNews Essential â¬49.99 (loses ticker entity tagging) |
| **Launch total** | **$198/mo** | Upper bound if TD needs the $499 card: **$548/mo** |

### Optional additions

| Item | Monthly | Buys back |
|---|---:|---|
| API Ninjas Business | $99 | Earnings transcripts + CFO quote. **Not recommended at launch** |
| sec-api.io Personal | $49 | Insider + 13D/G without building (31 days saved) â **if the email confirms display** |
| Intrinio Startup | $333 â $666 â $999 | Insider + revenue segmentation dimensions + fundamentals + EOD, with unambiguous display rights |
| Disclosed Capitol | $5â100 | Congressional trades â **if the email confirms** |
| Securities counsel opinion (one-off) | $2,000â$7,500 | A reliance document on Â§13107(c). Needed **before monetising congressional data**, not before a free launch |

### Versus simply paying FMP

**This comparison cannot be made honestly, because FMP's display price is not published and was not obtainable during research.** What is known:

- Current FMP personal Ultimate is ~$99/mo and **does not convey display rights**. The app is in breach today.
- Display requires a bespoke Data Display and Licensing Agreement, quote-only, no self-serve path, no published price band.
- Integration cost of staying on FMP is **0 dev-days** â versus ~10+ dev-days and 5 dark features for the recommended mix.

**The asymmetry that matters:** if FMP quotes anything under roughly $200â250/mo, it beats the recommended mix on cost *and* on effort *and* on feature completeness â it keeps all seven datasets, keeps the taxonomy byte-identical, and returns the entire two-week window to QA. That is why the FMP email in Â§3b is worth more than any code written in week 1.

### One cost trap worth naming

Reports are **frozen point-in-time snapshots stored in Supabase forever**. Two vendor clauses interact badly with that:

- **API Ninjas Â§3.2**: display rights end when the subscription lapses â *"you may not continue to display, use, or provide Output in your commercial applications."* Every historical report containing a transcript-derived quote becomes non-compliant on the day you stop paying. A purge/redact path must exist **before** launch, not after.
- **Finnhub**: *"All data must be deleted should your subscription to that data end."* Incompatible with any cache retention.

Design the purge path in now, or do not buy transcripts at all. The recommendation is: do not buy them.

---

## 5. Legal clearance

### SEC EDGAR â unambiguous green light

The SEC grants exactly what the app needs, in its own words: sec.gov information *"may be copied or further distributed by users of the web site without the SEC's permission,"* and *"All Government-created content on sec.gov and EDGAR public filing content are free to access and reuse."* No terms of use, no click-through, no API key, no registration, no restriction on commercial display. This covers 13F, Forms 3/4/5, 13D/G, XBRL and 8-K exhibits alike.

**Important nuance the research corrects:** 17 U.S.C. Â§105 is *not* the operative authority. A 10-K or a 13F is authored by a private filer, not a government employee, so it is not a Â§105 work. The right to reuse comes from the SEC's affirmative dissemination permission plus the fact that the data points actually displayed (share counts, dates, dollar amounts) are uncopyrightable facts under *Feist*. Do not cite Â§105 as the basis.

**Operational obligations â technical, not legal, but launch-blocking if wrong:**

1. **Declared User-Agent is mandatory.** Format: `Caydex <contact-email>`. Verified empirically across four separate research passes: no UA â **HTTP 403**; `python-requests/2.31.0` â **HTTP 403**; declared UA â 200. `httpx`'s default UA will be blocked identically. Inject it at the `httpx.AsyncClient` construction level so no code path can omit it.
2. **10 requests/second, per organisation, not per process.** The SEC's wording: *"regardless of the number of machines used to submit requests."* On multi-worker Railway a per-worker `asyncio.Semaphore` is **wrong** â you need one authoritative token bucket (Redis or a single-process gateway). Exceeding it throttles the IP until the rate stays below threshold for 10 minutes, and the SEC reserves the right to block outright. That takes out the whole app, not one feature.
3. **`Accept-Encoding: gzip, deflate`** and an explicit `Host` header.
4. **Attribution**: "Source: SEC EDGAR." Requested, not mandatory â and free.
5. **No SEC seal, no EDGAR logo, no wording implying SEC affiliation or approval.** SEC, EDGAR and EDGARLink are registered trademarks; referring to EDGAR in text is fine.
6. **Never display a raw CUSIP.** This is the one genuine third-party IP claim inside EDGAR â CUSIP Global Services (ABA/FactSet) licenses redistribution, waives fees only below 500 identifiers, and this app would touch ~15,000. Resolve CUSIPâticker internally via OpenFIGI (public domain, MIT, explicit redistribution rights) and display the ticker. Cheap now, expensive retrofit later.

### House Clerk â green; Senate eFD â yellow

Neither site imposes a copyright restriction. The House has no robots.txt, no agreement, no auth, no observed rate limiting. The Senate requires a genuine **clickwrap**: scrape a CSRF token, POST `prohibition_agreement=1`, carry the session cookie. Your scraper therefore *affirmatively assents* to the statutory prohibitions on every run â which matters legally.

### The one real constraint: 5 U.S.C. Â§13107(c)

> *"It shall be unlawful for any person to obtain or use a reportâ¦ (B) for any commercial purpose, other than by news and communications media for dissemination to the general publicâ¦"* â civil penalty up to $10,000, enforceable by the Attorney General.

**Public domain does not mean unrestricted.** Copyright law is silent here; a separate use-restricting statute governs. This is the reasoning most write-ups get wrong.

**The honest read:** a free, publicly-downloadable, attributed app republishing these disclosures to the general public sits squarely inside the natural reading of the news-media exception â which explicitly carves out *commercial* media, so being for-profit is not disqualifying. Congress affirmatively mandated online public posting via the STOCK Act. Quiver, Capitol Trades and Unusual Whales have operated on this theory for years and no AG action against a data publisher appears in the record. **But "never enforced" is not "lawful," and there is essentially no case law construing the exception for app publishers.** This is genuinely grey.

**Two design constraints that follow, and they are architectural:**

1. **Never paywall congressional data.** The app has a two-pool credits/IAP system. Gating congressional content behind credits is the single change most likely to move you from "dissemination to the general public" to "sale of a dataset." Make it a documented invariant in `CLAUDE.md`.
2. **Buying from a vendor does not cure this.** The statute binds *"any person"* who *obtains or uses* a report. A vendor contract adds indemnity, not immunity. Treat resellers as parsing-effort savers.

Since congressional trades are dropped for launch (Â§3d), this question does not block the two-week window. **Get scoped counsel ($2,000â$7,500) before the feature returns, and certainly before it is ever metered.**

*Not legal advice â primary-source research. Solid enough to launch a free product on; confirm with counsel before the paywall question arises.*

---

## 6. The honest recommendation

### Does "just ask FMP for permission to run a free beta" beat all of this?

**For a two-week timeline: yes, it is the single highest-expected-value action available â and no, you must not bet the launch on it.**

**Why it wins if it lands:** zero integration work, zero feature loss, byte-identical taxonomy, all seven datasets intact, and the entire two-week window returned to QA and App Store review. Nothing else in this document comes close on effort-per-dollar.

**Why it cannot be the plan:**

- FMP's process is a negotiated bespoke agreement with no published price, no self-serve path, and no advertised turnaround. EODHD advertises 3-business-day commercial onboarding; FMP advertises nothing. A two-week window has no room for a sales cycle you cannot schedule.
- FMP's ToS explicitly closes the free-app loophole â *"irrespective of whether such usage is complimentary or paid."* A beta waiver must therefore be **in writing**, naming the app and the date range. An implied, verbal or "they didn't say no" permission is worth exactly nothing, and shipping on it is a live breach with vendor termination â mid-launch, without notice â as the failure mode. That is strictly worse than launching with five features dark.
- The quote may simply be unaffordable. That is the premise this whole evaluation started from.

**So: send the email today, and build the Twelve Data + Marketaux path in parallel. Do not block on the answer.** If a written waiver arrives by day 3, stop the migration, ship on FMP, and use the recovered week to build the 13F pipeline properly instead of the reduced-scope version. If it does not arrive, the fallback is already in flight and the date holds.

### The recommendation in one paragraph

Buy the two datasets that are load-bearing across many screens and genuinely cannot be self-sourced â **company profile (Twelve Data Venture, from $149/mo) and news (Marketaux, $49/mo pending written approval)** â for **~$198/mo**. Self-source **nothing** in week one; the SEC paths are excellent and free and legally the cleanest options in the entire report, but the cheapest of them is 5 days against 4 days of capacity. Ship **five features dark** with honest empty states: whale/13F, congress, insider, founder stake, revenue segmentation. **Drop transcripts entirely** â the moat-pillar cost is near-zero because Network Effects stays at high confidence and Switching Costs routes to the Gemini grounded fallback that is already built. **Fix the `"maintained"` guidance default before launch** â it is the only item here that is a shipped wrong answer rather than a missing feature. Then restore in sprint order: 13F holdings (5d) â insider quarterly bulk (6d) â 13F flow history (8d) â EDGAR 8-K guidance (5â6d) â revenue segmentation (7â9d) â congress (buy or 15d build). Send four emails on day 0; any one of them could collapse weeks of this backlog into an integration.

---

## 7. Where this document is uncertain â do not resolve these silently

| # | Uncertainty | Why it matters | How to close it |
|---|---|---|---|
| 1 | **Twelve Data sector/industry parity with FMP's 11Ã159 taxonomy is inferred from 3 tickers** (AAPL, TSLA, MSFT) plus a docs sample â not proven across 153 industries | `industry_benchmark_lookup` falls back to the sector row on a miss **with no error, no log, and no visible symptom**. A partial mismatch silently degrades every "vs industry average" to "vs sector average" across the app | Verify a 25â30 ticker sample spanning REITs, banks, biotech and software **during the trial, before committing.** Add a startup assertion that every industry in `benchmark_universe.json` resolves |
| 2 | **Which Twelve Data Venture credit sub-tier actually carries Fundamentals + 57,040 credits/night** | The difference between $149/mo and $499/mo â a 3.3Ã swing on the largest line item | Confirm in writing with Twelve Data sales during the trial |
| 3 | **Marketaux display rights** â their only legal document is 2021 website boilerplate granting "personal, non-commercial use" | Without written approval you have swapped one unlicensed vendor for another. This is the entire point of the migration | Written email approval before launch. Fallback: GNews Essential, images off, no ticker entity tagging (real quality hit) |
| 4 | **sec-api.io tier scope** â pricing page and ToS genuinely contradict each other on whether a free, non-paywalled app is in scope at $49 | Could collapse 31 dev-days (insider + 13D/G) into ~4 | One email. Keep the written answer |
| 5 | **Disclosed Capitol** â commercial-use permission appears on a docs page, not a signed licence; `/legal/terms` 404s. Small, young vendor | Single-vendor dependency for a launch feature, on an unverified grant | Written email. Treat as fallback, not primary |
| 6 | **Â§13107(c) news-media exception** â no case law, no AG interpretive guidance, universally practiced but untested | Determines whether congressional trading can ever be a metered feature | Scoped counsel opinion before the feature returns |
| 7 | **Twelve Data's display terms for the *core* market-data migration** were not independently assessed in this research pass | If core data was scoped to an Individual tier, see the box in Â§0 â the whole migration is legally inert | Confirm the purchased plan is Business/Venture **today** |
| 8 | **FMP's display price is entirely unknown** | Makes the Â§4 cost comparison one-sided. FMP could be cheaper than $198/mo, or 20Ã it | Request the quote. It costs one email |

**On the effort numbers:** every estimate in this document is the research author's own figure for a first-time implementer, and each carries a "this is where the schedule dies" clause â the Unicode small-caps cipher in House PDFs, the 2â3Ã over-count from XBRL presentation hierarchy, the 23.3% multi-manager row split, the 52-day Form 4 freshness gap, the CINS fallback rate-limited to 20/min. **None of them are padded, and none include the core migration, QA, iOS disable work, or App Store review.** Treat 125 days as a floor, not a target.",
    "schedule_critique": "## Verdict

The document is right that two weeks is impossible, and wrong about almost every number it uses to prove it. It under-counts the work in the direction that *flatters* the recommended plan: the 125 days is not reproducible from its own table, the "core migration, 3â5 days" line is off by 4Ã, and the plan it says *does* fit is ~18â25 working days, not 10. Below, everything is measured against the repo.

---

## (a) The effort estimates

### 1. The largest line item is a footnote, and it is wrong by 4Ã
Â§1b: *"the Twelve Data core market-data migration itself (quotes, historical, fundamentals) â budget 3â5 days pessimistically."*

Measured:
- `backend/app/integrations/fmp.py` is **2,098 lines**, **68 public `get_*` methods**, imported by **59 backend files**.
- `ls backend/app/integrations/` â `alternative_me, apewisdom, app_store, census, coingecko, finra_short_interest, fmp, fred, gemini, openfda, uspto`. **There is no Twelve Data code.** The adapter is greenfield: client, auth, credit accounting, rate limiter, typed exception hierarchy, `ErrorCode` mapping, `close_twelvedata_client()` in the `main.py` lifespan (`.claude/rules/integrations.md` requires all of it).
- At least **17 of the 68 methods have no Twelve Data equivalent at any tier**: `get_grades`, `get_price_target_consensus`, `get_stock_peers`, `get_index_constituents`, `get_sp500_constituents`, `get_etf_holders`, `get_etf_sector_weightings`, `get_etf_info`, `get_shares_float`, `get_historical_market_cap`, `get_sector_performance`, `get_industry_performance`, `get_biggest_gainers`, `get_biggest_losers`, `get_most_actives`, `get_earnings_calendar`, `get_earning_calendar_full`.

Those feed `analyst_service`, `home_dashboard_service` (Home movers), `index_service`, `etf_service`, `holders_service`, `signal_of_confidence_service`, `widget_movers_service`, `tracking_service`, `notification_senders/earnings_sender.py`.

**Correction:** the "core migration" is a *second, unscoped go-dark exercise* covering analyst ratings/targets, peers, index constituents, ETF holdings, Home market movers, and the earnings calendar. Realistic: **12â20 days**, and it produces its own dark-feature list that Â§3d does not contain.

### 2. The live-price WebSocket is not mentioned once in the document
```
app/services/live_price_manager.py:30: FMP_WS_URL_STOCK  = "wss://websockets.financialmodelingprep.com"
app/services/live_price_manager.py:31: FMP_WS_URL_CRYPTO = "wss://crypto.financialmodelingprep.com"
```
Plus `app/api/v1/endpoints/live_price.py` and an iOS WS client that passes `?token=` from `APIClient.currentAuthToken()` (auth.md Â§8). Twelve Data's WS is a different protocol with a per-plan symbol cap, and your own memory note records that **the local FMP key has no ws entitlement, so both sockets 401 locally** â i.e. you cannot prove the replacement works without a deployed key. **+3â5 days, unbudgeted, with a verification path you don't control.**

### 3. The credit math in Â§3a/Â§4 is wrong by roughly two orders of magnitude
Â§3a: *"verifyâ¦ enough credits (5,704 Ã 10 = 57,040 credits per nightly refresh)."*

That counts `/profile` only. The job actually keyed on those 5,704 tickers is `sector_benchmark_service.compute_all_benchmarks`, and `_fetch_company_data` (`app/services/sector_benchmark_service.py:821â838`) issues **ten fundamentals calls per ticker**: income (annual+quarter), cash flow (annual+quarter), ratios (annual+quarter), key metrics (annual+quarter), balance sheet (annual+quarter) â at `FMP_ANNUAL_LIMIT_BACKFILL = 16` / `FMP_QUARTERLY_LIMIT_BACKFILL = 80`.

So it is **57,040 fundamentals *calls*, not 57,040 credits.** Twelve Data prices fundamentals well above 10 credits/symbol. At even 20 credits/call and Venture's quoted 610 credits/min, one full recompute is ~31 hours. At 100, it's a week.

And there is a **second, larger sweep** the document never mentions (see Â§4 below).

**Correction:** Â§7 uncertainty #2 ("which sub-tier carries Fundamentals + 57,040 credits") is asking the wrong question with the wrong number. The trial must measure the *benchmark recompute*, not `/profile`. The "$149 vs $499" framing is likely a false choice.

### 4. There is a second taxonomy universe file, and a second sweep, neither of which appear in the document
`backend/data/industry_universe.json` â 443 KB, `"industry_count": 156, "ticker_count": 9188`, source `fmp /stable/available-industries + company-screener`. It is **larger than** `benchmark_universe.json` (153 / 5,704), which is the only file the document names.

It backs `industry_moat_benchmark_service` (`_UNIVERSE_PATH`, line 80) with `TOP_TICKERS_PER_INDUSTRY = 200` (line 63) and 5 FMP calls per peer â order **150,000 calls** per full recompute, on FMP's *and* Twelve Data's meter.

**Correction:** Â§7 uncertainty #1 ("verify a 25â30 ticker sampleâ¦ every industry in `benchmark_universe.json` resolves") is scoped to the smaller file. The startup assertion must cover **156** industries and **9,188** tickers across **both** files, plus `sector_benchmarks`, `industry_moat_benchmarks`, `industry_dossier_cache`.

### 5. The blast-radius counts are ~3Ã understated
| Doc claim | Measured |
|---|---|
| "132 sector refs" | `sector` appears **2,024** times across **78** backend files |
| "121 industry refs" | `industry` appears **1,082** times across **53** files |
| "/profile consumed by 26 backend files" | **36** files |

Those are the research author's *field-access* counts; Â§2 reuses them as a load-bearing-ness proxy. The real surface is triple.

### 6. Â§3e's "0.5 day" guidance fix is 1.5â2 days, and as specified it does not fix the bug
Verified end to end:
- Five hardcoded defaults, not one: `ticker_report_data_collector.py:2965`, `:4636`, `:6527` (`_VALID_GUIDANCE_STATUSES`), `narrative_prompts.py:1837`, and `narrative_prompts.py:915` (`rf.get("management_guidance") or "maintained"`).
- `app/schemas/ticker_report.py:132` is a bare `management_guidance: str` â a new value passes Pydantic silently.
- **The killer.** `frontend/ios/ios/Models/TickerReportResponse.swift:1142`:
  ```swift
  private static func mapGuidance(_ s: String) -> ManagementGuidance {
      switch s.lowercased() {
      case "raised": return .raised
      case "lowered": return .lowered
      default: return .maintained
      }
  }
  ```
  `ManagementGuidance` (`TickerReportModels.swift:586`) has exactly three cases. **A backend-only `not_disclosed` renders as "MAINTAINED" on screen.** The fix ships and the bug survives. You also need: the Swift enum case + `color`/`backgroundColor`, a colour token registered in `AppColors.auditManifest` or the DEBUG launch audit `assertionFailure`s (`.claude/rules/ios-swiftui.md`), the badge at `ReportFutureForecastSection.swift:91â93`, `pdf_report_service.py:592`, and `test_ticker_report_schema_parity.py`.
- **Frozen snapshots.** Reports are permanent Supabase rows, never re-fetched. Every existing report has `"maintained"` baked in. Adding a state does not un-lie them. Your own memory note `project_detail_screens_deep_check_2026_08.md` records exactly this: *an additive field's DEFAULT laundered 335 stale cache rows into a confident wrong value.* A backfill/invalidate decision belongs in the same change.

### 7. "Drop transcripts, moat cost is near-zero" is wrong at the benchmark layer
The document checks only the per-ticker pillar. But `industry_moat_benchmark_service._score_one_ticker` (lines ~176â184) fetches `self.fmp.get_earning_call_transcript(ticker)` for **every peer**, up to 200 per industry Ã 156 industries.

Drop transcripts and per-ticker scores are computed *without* the NRR driver while `industry_moat_benchmarks` still holds peer averages computed *with* it. Every "vs industry" moat verdict then silently compares two different metrics â the same class of defect as the whale "$3T exodus" note the document itself invokes. **Either wipe and recompute that table (a multi-hour-to-multi-day sweep, unpriced against TD credits) or gate the comparison. +1 day + a recompute window.**

### 8. Â§3d item 8 ("reduced display surface de-risks the dataset") is legally muddled
`news_insight_service.py:692` feeds `a.get("summary")` â the publisher's snippet â into the Gemini prompt, and the row is persisted in `ticker_news_cache`. Dropping the snippet from *display* removes nothing from **ingestion, storage, or LLM processing**. The document's own Â§0 quotes Twelve Data Grow forbidding data being *"programmatically processed, stored, transformed"* â three verbs that are not "displayed". Reducing the display surface is worth doing, but it does not "de-risk the entire dataset independent of which vendor you pick," and dropping the snippet *also* degrades the `summary_bullets` that Â§3d says replaces it.

### 9. The 13F day-5 gate is the single most dangerous sentence in the plan
Â§3c offers days 6â8 (3 days) for a build the research author sizes at 5, which itself includes the full OpenFIGI CUSIPâticker step with the CINS `/v3/search` fallback rate-limited to 20/min plus a hand-maintained override list. Realistic for a first-timer: 8â10. **Delete the gate.** A half-built 13F pipeline discovered on day 8 has no recovery path before a day-9 submit.

---

## (b) Does the plan fit in 10 working days? No â and Â§1c contradicts Â§3f

**The document's own arithmetic doesn't close:**
- Â§1a table sums to **109**, not 125 (13+15+16+15+16+12+20+2). You only reach 125 by silently valuing the "not feasible (40+)" transcripts cell at 16.
- The degraded column sums to **30â36** (5+6+7â8+7â9+5â6), not 28.
- Â§1c says **~4 days of capacity for everything in this document**. Â§3f then spends ~5 days on it (news adapter 3â5, go-dark 6â7, guidance fix). Both cannot be true.
- Days **1â3** and **3â5** overlap on day 3, so a 5-day span carries 6+ days of listed work at the doc's own optimistic rates.

**What day 8 actually contains:** "Full regression, adversarial hardening pass per CLAUDE.md, `pytest -x`." That is **298 test files / ~6,100 tests**, including **36 schema-parity tests** â every one of which is a live tripwire after an `Optional` sweep. One red parity test on day 8 consumes the entire day-10 buffer. Â§1b says code freeze is "roughly day 7â8"; Â§3f makes freeze *equal* to day 8, leaving zero days between the last code change and submission. **One day of buffer is not a buffer for an App Store rejection** â a rejection restarts the queue.

**Honest re-plan for the recommended mix** (buy profile + news, five features dark, guidance fix):

| Work | Days |
|---|---:|
| Twelve Data adapter + core migration (68 methods, 17 with no equivalent) | 12â20 |
| Live-price WebSocket replacement | 3â5 |
| News adapter + reduced display surface across 5 screens | 4â5 |
| Go-dark pass (see below) | 4â6 |
| Guidance `not_disclosed` (backend + iOS + PDF + backfill decision) | 1.5â2 |
| Moat-benchmark reconciliation + recompute | 1 + recompute window |
| Cache invalidation, migrations, taxonomy assertion | 2 |
| Regression + hardening + parity fixes | 3 |
| **Total** | **~30â44** |

Even the version the document calls "the plan that actually ships" is **3â4Ã the window**, not 1Ã.

---

## (c) Dependencies the document hides

1. **Live-price WebSocket** â two FMP sockets, an endpoint, an iOS client. Zero mentions.
2. **No Twelve Data code exists.** The plan reads as if there's an adapter to point at a new base URL. There isn't.
3. **Cache invalidation is named in Â§7 and scheduled nowhere.** Â§7 correctly notes `industry_benchmark_lookup` "falls back to the sector row on a miss with **no error, no log, and no visible symptom**" and prescribes a startup assertion â then Â§3f allocates zero time to it. Every `*_cache` row keyed on the old taxonomy is a silently-wrong row after the swap.
4. **Migration application is calendar time you do not control.** The repo is at migration **150**; `CLAUDE.local.md` says the user applies them manually via Supabase Studio and *"Claude must never run apply commands."* Any day-6/7 schema change blocks on a human. Not in the day plan.
5. **Notification senders.** `notification_senders/earnings_sender.py` consumes `get_earnings_calendar`; `config.py` documents the insider sender at "~200 FMP calls". Â§3d lists 8 UI surfaces and never touches the push layer â going dark on insider without updating the notification registry ships a push kind that either can never fire or fires on stale data.
6. **Rate-limit constants are calibrated in the wrong unit.** `config.py` comments tune prewarm against *"FMP Starter (300/min)"*, "~20-call FMP fan-out" per report, "~135 FMP calls per interval". Twelve Data meters **credits/min (610 on Venture)**, not calls/min. Every semaphore, `FMP_BATCH_SIZE`, and prewarm interval needs re-derivation â and your last two commits were literally *"Restore GEMINI_SEMAPHORE and FMP_BATCH_SIZE."*
7. **Go-dark is ~100 Swift files, not "2 days."** Swift files referencing Whale **32**, Congress **36**, Insider **46**; backend files referencing whale/congress/insider **50**. Against 36 parity tests. **4â6 days.**
8. **App Store listing.** Five dark features means screenshots and description likely need re-shooting before a day-9 submit.
9. **Two of the four "one hour" emails are on the critical path.** Â§3a says send Marketaux first *"because the answer determines whether you build against Marketaux or GNews"* â so days 3â5 are blocked on a vendor's reply SLA. There is no fallback trigger ("no reply by end of day 2 â build GNews").

---

## The three edits that would make the document honest

1. **Move the core migration into the Â§1a table** at 12â20 days with its own go-dark list (analyst ratings/targets, peers, index constituents, ETF holdings, Home movers, earnings calendar), and add a **live-price WebSocket** row at 3â5. The headline then reads *"~150â170 days against 10"* and Â§1c's "~4 days of capacity" becomes **negative**, which is the true finding.
2. **Replace the Â§3a credit check** with: *"measure one full `sector_benchmark_service.compute_all_benchmarks` (57,040 fundamentals calls) and one `industry_moat_benchmark_service.recompute_all` (156 industries Ã 200 peers Ã 5 calls) against Venture's credit meter during the trial. If either exceeds the monthly allowance, the $149 tier is not viable and the plan has no profile source."* This is a go/no-go on the whole recommendation, not a Â§7 footnote.
3. **Delete the day-5 13F gate**, re-price the guidance fix at 1.5â2 days *including the iOS enum and the frozen-row backfill*, and add a moat-benchmark reconciliation line. Then extend the schedule to ~30â44 days or cut scope further â do not keep a 10-day calendar over a 30-day plan.",
    "licensing_critique": "# Adversarial licensing review â Build vs Buy doc

Reviewed against the seven research passes. **11 corrections**, ranked. The document is materially better than most on this topic (Â§0's Twelve Data tier trap and Â§5's Â§105 correction are both right and both non-obvious), but it repeats the exact failure mode it was written to prevent in three places.

---

## ð´ 1. BLOCKING â the plan re-displays unlicensed FMP data after the migration (Â§3f, Â§2, Â§7 #1)

Â§3f Day 1â3 states verbatim:

> IPO date: **backfill once from existing FMP data and freeze**

This is the document's own thesis inverted. Â§4 says *"The app is in breach today"* under FMP personal. Copying that data once and freezing it does not cure the breach â it makes it permanent and undetectable. Governing clause (FMP ToS Â§2.2.2, quoted in the profile research):

> customers are prohibited from showcasing FMP Services or **Data** on platforms including â¦ applications designed for utilization by multiple individuals, irrespective of whether such usage is complimentary or paid

The prohibition attaches to the **Data**, not to the API call. And Â§2.2.1 separately bars integrating the data *"into any tools or applications accessible by any third parties."* Neither has a "but you fetched it earlier" exception.

**This is not limited to `ipoDate`.** I verified the FMP-derived corpus that survives a Twelve Data cutover untouched:

| Asset | Evidence |
|---|---|
| `backend/data/benchmark_universe.json` | `"source": "fmp /stable/available-industries + /stable/company-screener"`, 153 industries / 5,704 tickers, generated 2026-06-24 â **checked into the repo** |
| `backend/data/industry_universe.json` | `"source": "fmp /stable/available-industries + /stable/company-screener"`, 156 industries / 9,188 tickers |
| `sector_benchmarks`, `industry_moat_benchmarks`, `industry_dossier_cache` | keyed on the FMP industry strings from the above |
| `ticker_news_cache` | 10 backend files read it; populated from FMP news today |
| `ticker_report_data` JSONB | frozen report snapshots, never re-fetched (`pdf_report_service.py`, `chat_context_resolver.py`) |

**Correction:** Â§3f needs a cutover step the document does not contain anywhere â purge or re-derive FMP-sourced persisted data before launch. Concretely: re-generate `benchmark_universe.json` / `industry_universe.json` from Twelve Data during the trial (this is the same 25â30 ticker parity work Â§7 #1 already schedules, just extended to a full regeneration); truncate `ticker_news_cache` at the news-vendor swap; and decide explicitly what happens to pre-migration frozen reports (purge, or accept as historical and stop serving them). Budget this â it is not zero.

**Sharpest form of the finding:** Â§4's "One cost trap worth naming" identifies exactly this hazard â *"Reports are frozen point-in-time snapshots stored in Supabase forever"* â and applies it to API Ninjas Â§3.2 and Finnhub, but never to the incumbent the whole document is migrating away from. Same trap, same architecture, larger blast radius.

---

## ð´ 2. Marketaux is in the "actual display rights" column and in the launch total. It has no commercial grant. (Â§2, Â§3a, Â§4)

Â§2's column header is **"Cheapest vendor *with actual display rights*"**. The Marketaux entry sits in it. Marketaux's only legal document (website ToS, Jan 2021) grants:

> a licence **solely for your personal, non-commercial use**

and Prohibited Activities:

> The Site may not be used in connection with any **commercial endeavors** except those that are **specifically endorsed or approved by us**

"Site" is defined to include *"any other media formâ¦ mobile website or mobile application related, linked, or otherwise connected thereto"* â which reads onto the API. As of today there is no approval, so Marketaux has **zero** display rights on any tier, free or $199. It belongs in the same bucket as Stock News API and NewsAPI.org, not in a column defined by having rights.

**Corrections:**
- Move Marketaux out of that column; put **GNews Essential** there (see #6) and list Marketaux as a conditional upgrade.
- Â§4's "Launch total **$198/mo**" presents a number that assumes an approval that does not exist. Restate as `$149 (TD) + $49ââ¬49.99 (news, vendor TBD by approval)`.
- Â§3f has **no gate** for the Marketaux answer. Days 3â5 build the news adapter unconditionally. Add an explicit branch at Day 2: approval in hand â Marketaux; otherwise â GNews, images off, no entity tagging.
- Â§3a's *"One line of written approval closes the gap"* is optimistic. Require: from a named person with authority, naming the app + bundle ID, the paid tier, and the specific display surface (headline / source name / timestamp / link), retained. A support-inbox "sure, that's fine" is not a licence amendment.

---

## ð  3. The $149 Twelve Data Venture sub-tier's display right is unverified â this is the doc's own trap, one tier down (Â§0, Â§2, Â§3a, Â§4)

The document correctly proves that **Venture** carries *"External display data access"*. But the evidence in the research is the **Venture card**, headline **$499/mo**. The document then plans, prices and totals against *"from $149/mo (lowest credit sub-tier)"* and instructs: *"Start at the $149 credit sub-tier."*

Â§7 #2 flags the $149-vs-$499 question â but frames it as a **credits/Fundamentals** question ("a 3.3Ã swing on the largest line item"). It never asks the licensing question: **does the $149 credit sub-tier carry "External display data access," or is that feature attached to the $499 card?**

That distinction â cheap sub-tier sells access, headline tier sells display â is the precise pattern Â§2a says governs every row in the document.

**Correction:** add to Â§7 #2 and to the Â§3b email list: *"Confirm in writing that the $149 Venture credit sub-tier carries External display data access, not only the $499 plan."* Until answered, treat $499 (or $414/mo annual) as the planning number, which moves the launch total to **$548/mo**, not $198.

---

## ð  4. Twelve Data Venture's second limb is omitted â and the backend serves a public API (Â§0, Â§2a)

The document quotes the grant but not the restriction. Full tooltip:

> Display the data to end users in external applications, websites, or client-facing products. **Redistribution via raw data feeds or APIs is not permitted unless explicitly licensed.**

The research's own gloss: *"Serving JSON straight through your own API to your own iOS app is display, not redistribution â but **do not expose a public/partner data API on this tier**."* Twelve Data's Terms separately define Redistribution as *"any publication, distribution, or provision of Data to third parties."*

This is load-bearing here: Â§5 point 6 confirms the app has a public API surface (*"never render a raw CUSIP â¦ or expose it via the public API"*), and 46% of routes are `.public` per `auth.md` â i.e. unauthenticated callers can hit them.

**Correction:** add to Â§5 as an operational invariant alongside the CUSIP rule â TD-derived fields (sector, industry, profile, splits, logo) may be served to the app's own clients but must not be exposed on any documented/partner/public data endpoint. Worth a note in `CLAUDE.md` next to the congressional-paywall invariant Â§5 already proposes.

---

## ð  5. EODHD's published commercial tier explicitly forbids display â Â§3b makes it sound like a cheap fast win (Â§3b)

Â§3b lists the EODHD email as high-option-value: *"They advertise commercial onboarding in **3 business days** â Byte-identical FMP taxonomy *plus* IPO date."* No price, no rights caveat. Â§6 then says any one of the four emails *"could collapse weeks of this backlog."*

EODHD's commercial page:

> with the internal usage package, the data is restricted to being used solely within your company. **Displaying the data or sharing it with individuals outside your company is not permissible under this package.**

That is the **$399/mo** tier. Their personal tiers ($59.99 Fundamentals, $99.99 all-in-one) are worse â *"The packages on the pricing page are intended for personal use only."* Display therefore sits between Custom (from $399) and **Enterprise $2,499/mo**, quote-only.

**Correction:** annotate the Â§3b row â *"note: EODHD's published $399 'Internal Use' commercial tier explicitly forbids display; the quote to request is Custom/Enterprise, plausibly ~$2,499/mo."* Otherwise this email reads as a $399 escape hatch when it is most likely a 12Ã one, and IPO date is not worth $2,499/mo against 4 consumers (`etf_service.py:1015/1396`, `whale_service.py:3179`, `chat_service.py:1136`, `stock_overview_service.py:453` â verified).

---

## ð¡ 6. The news ranking is inverted on licensing grounds (Â§2, Â§2a, Â§4)

The document ranks **Marketaux primary / GNews fallback**, and Â§2a's "three vendors with published, self-serve, unambiguous display rights" excludes GNews entirely. But GNews is the only one of the two with a published commercial grant:

> **Data retrieved through the API may be used for commercial purposes**  *(paid tiers; free tier expressly excluded â "cannot be used for commercial projects")*

Marketaux has none. The ranking is correct on **features** (entity tagging, crypto/forex coverage) and backwards on **rights**. Given the developer's stated failure mode, the document should say so out loud rather than leaving Marketaux looking like the safer pick.

**Correction:** Â§2a's count is "three self-serve grants" only if GNews is excluded on a distinction the document never states. Either make it four with GNews's caveat attached, or say explicitly: *"GNews is the licensed option; Marketaux is the better product with no licence yet."*

**Also missing from Â§4's fallback line:** GNews's grant covers the *data*, not the *content*:

> Images and media content obtained through the API may be subject to copyright protection by third parties; **you are solely responsible for ensuring you have the necessary rights.**

Â§3d row 8 already drops images, which handles it â but Â§4's one-liner *"Fallback: GNews Essential â¬49.99 (loses ticker entity tagging)"* understates it. The loss is entity tagging **and** image rights **and** crypto/commodity/index coverage â i.e. four of the five screens Â§3d row 8 promises will "stay live" degrade to keyword matching.

---

## ð¡ 7. Trial-period data must not populate production caches (Â§3a, Â§3f Day 0, Â§7 #1)

Â§3f Day 0 starts the Twelve Data trial and begins the parity sample; Â§3a says *"verify during the trial."* The app's cache-aside pattern writes through to Supabase (`sector_benchmarks`, `industry_dossier_cache`, the `*_cache` tables). If the trial runs under Basic/eval terms â *"cannot be displayed to users, shared externally, or used in production systems"* â anything fetched during it and written through survives into launch as unlicensed data. Same shape as finding #1.

**Correction:** run the parity sample against a scratch table, or confirm the trial is a Venture trial under Venture terms before any write-through. One line in Â§3f Day 0.

---

## ð¡ 8. Two omitted vendor clauses (Â§4, Â§3d row 6)

- **API Ninjas Â§3.3** bars using Output to build a competing data API. Â§4 correctly captures the Â§3.2 lapse-purge trap but not this one. Relevant given the backend's public routes â a consumer app is fine; a transcript endpoint of your own is not.
- **Â§13107(c)(1)(D)** â Â§5 quotes only limb (B). The statute also bars use *"in the solicitation of money for any political, charitable, or **other purpose**."* The app has IAP. Keep congressional data out of any donation, referral, upsell or fundraising surface â a separate constraint from the paywall invariant Â§5 already names, and it should sit next to it.

---

## ð¡ 9. EDGAR's "free to reuse" does not launder third-party material inside a filing (Â§5, Â§3e)

Â§5 says *"no restriction on commercial display. This covers 13F, Forms 3/4/5, 13D/G, XBRL and 8-K exhibits alike."* The research carries a caveat the document drops:

> SEC's "free to reuse" covers the FILING. It does not launder the copyright in third-party material a filer attaches (e.g. a reprinted analyst chart inside an exhibit).

This lands directly on Â§3e / Â§3d row 7, which is the one EDGAR pipeline the document actually recommends building â scraping EX-99.x exhibits for Outlook text and a verbatim CEO quote. Filer-authored press-release prose is fine; a licensed chart, index data or third-party image inside the same exhibit is not.

**Correction:** extract text only, never re-serve exhibit images or embedded tables wholesale, and add the caveat to Â§5's list of operational obligations.

---

## ðµ 10. OpenFIGI: the standard's licence â  the service's terms (Â§5 point 6)

Â§5 asserts *"OpenFIGI (public domain, MIT, explicit redistribution rights)."* The research established that for the **FIGI identifier standard**. It did not quote the **OpenFIGI API terms of service** (listed as an evidence URL, never excerpted), and the plan depends on the free API key for the ~15,000 CUSIP mappings and the `/v3/search` CINS fallback.

**Correction:** downgrade to *"FIGI the identifier is openly licensed; confirm the OpenFIGI API ToS separately before relying on the free key in production."* Low risk, but it is an unverified rights claim sitting in the section that clears the CUSIP problem â the one genuine third-party IP claim in the whole EDGAR path.

---

## ðµ 11. Minor

- **Â§0 mis-example.** Grow's storage prohibition is said to forbid *"`ticker_news_cache`, `sector_benchmarks`, and every two-tier cache."* Twelve Data has no news endpoint (`api.twelvedata.com/news` 404s, per the news research), so `ticker_news_cache` is never TD data. `sector_benchmarks` and the profile caches are the right examples; drop the first.
- **Â§0 "exactly one Twelve Data line item."** Enterprise ($1,099/mo, per the 13F pass) presumably also carries external display. "Exactly one *affordable* line item" is the defensible claim.
- **Â§5 CUSIP attribution.** The doc says *"CUSIP Global Services (ABA/FactSet)"*; the SEC's own FAQ (quoted in the legal pass) says *"S&P Global Market Intelligence, which manages CUSIP Global Services on behalf of the American Bankers Association."* Immaterial to the mitigation, but the demand letter would come from whoever actually holds it.
- **`source_logo_url` is a live field** (`app/schemas/news.py:20,54` â verified). Â§3d row 8 says drop the publisher *image* and keep the source *name*; make the logo drop explicit, since a publisher logo is trademark use, not content reuse, and several aggregator hyperlink licences condition on links being text-only with no trademarks.

---

## Verified correct â do not re-litigate

Â§0's Twelve Data individual-tier trap and the Grow/Pro/Ultra breakdown Â· the Â§0 corollary killing the 13F pass's "cheap Grow-tier `/splits` and `/profile`" recommendation Â· Â§2a's characterisations of Finnhub ("derived results", $3,500 personal-use), Alpha Vantage ("directly or indirectly"), Quiver, Unusual Whales, WhaleWisdom, sec-api.io (genuinely ambiguous, needs the email) Â· Â§4's API Ninjas Â§3.2 lapse-purge trap and Finnhub's deletion-on-termination clause Â· Â§5's correction that 17 U.S.C. Â§105 is the wrong authority for filer-authored EDGAR content Â· Â§5's "public domain â  unrestricted" framing of Â§13107(c) and the "a vendor contract adds indemnity, not immunity" point Â· Â§6's insistence that any FMP beta waiver be in writing.

---

*Non-licensing, noted in passing since it affects the headline: Â§1a's columns don't sum. Full build across the 9 rows totals 109 excluding transcripts, 125 only if transcripts count as 16 â but that row reads "not feasible (40+)". The degraded column sums to 32â34 at the stated ranges, not 28.*"
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
      "label": "res:13f",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "ae86cdcb5ed8a3911",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335447066,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Form 13F institutional holdings (reverse lookup for ~45 namâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336417303,
      "tokens": 141788,
      "toolCalls": 56,
      "durationMs": 970235,
      "resultPreview": "{"dataset":"Form 13F institutional holdings (reverse lookup for ~45 named funds, 8 quarters of history, split-adjusted net-share flow, portfolio value + sector allocation, CIK identifiers)","what_the_app_needs":"Per-fund holdings (\"what does fund X hold\") for the 45 CIKs already in backend/data/whale_registry.json; 8 quarters per position to compute net-share-flow deltas; split adjustment acrossâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 2,
      "label": "res:congress",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a13a487f5213e06cb",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335447255,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Congressional (House + Senate) stock trading disclosures â â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336139719,
      "tokens": 140319,
      "toolCalls": 54,
      "durationMs": 691384,
      "resultPreview": "{"dataset":"Congressional (House + Senate) stock trading disclosures â STOCK Act Periodic Transaction Reports (PTRs)","what_the_app_needs":"Per-politician trade history with: ticker, transaction type (purchase/sale), the STOCK Act amount RANGE as a literal range (not a midpoint), transaction date, and disclosure/filing date. All five fields exist in the official free sources for ELECTRONICALLY filâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 3,
      "label": "res:form4",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a76315264812521ba",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335450297,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Insider transactions (SEC Form 4 / Forms 3-4-5, Section 16 â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336190941,
      "tokens": 201198,
      "toolCalls": 74,
      "durationMs": 740643,
      "resultPreview": "{"dataset":"Insider transactions (SEC Form 4 / Forms 3-4-5, Section 16 filings)","what_the_app_needs":"Per-transaction: TRANS_CODE (P/S/A/M/F/D/G/J/C/Xâ¦), signed share count, price per share, security title, insider name + role (Officer/Director/TenPercentOwner/Other + officer title), transaction date, issuer ticker. Aggregated into 12-month buy/sell dollar-flow bars and a net-flow verdict â whichâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 4,
      "label": "res:13dg-and-segmentation",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a44eb74b3ee3fdc09",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335450835,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "A: SC 13D/G beneficial ownership (founder/major-holder totaâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336436720,
      "tokens": 208892,
      "toolCalls": 72,
      "durationMs": 985883,
      "resultPreview": "{"dataset":"A: SC 13D/G beneficial ownership (founder/major-holder total stake). B: Revenue segmentation by product and by geography. Both researched together; findings labelled A/ and B/ throughout.","what_the_app_needs":"A/ For a ticker, the named 5%+ beneficial owners with total shares beneficially owned and percent of class, plus an as-of date (e.g. Larry Ellison / ORCL). Form 4 securitiesOwneâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 5,
      "label": "res:transcripts",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a774dc9556ff9a265",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335451770,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Earnings call transcripts (management guidance extraction +â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336203750,
      "tokens": 165296,
      "toolCalls": 66,
      "durationMs": 751980,
      "resultPreview": "{"dataset":"Earnings call transcripts (management guidance extraction + LLM-derived moat pillars: switching costs, network effects)","what_the_app_needs":"Three distinct things, and they have very different sourcing answers:\n\n1. GUIDANCE STATUS (raised / maintained / lowered) â currently derived by Gemini Stage A reading the FMP transcript. In `ticker_report_data_collector.py`, `_overlay_ai_guidâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 6,
      "label": "res:news",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a4538f08a56819f25",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335451399,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Financial news â per-ticker news and general market news (hâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336123155,
      "tokens": 147533,
      "toolCalls": 61,
      "durationMs": 671755,
      "resultPreview": "{"dataset":"Financial news â per-ticker news and general market news (headline, source, snippet, image, link), ~15 call sites across 5 screens (stocks, crypto, commodities, indices, Updates/market feed)","what_the_app_needs":"Per-ticker news for stocks AND crypto AND commodities AND indices, plus a general market feed. The wire model (backend/app/schemas/news.py) displays: headline, summary/snippeâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 7,
      "label": "res:profile-sector-industry",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a7a3b7bae7a57bdd9",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787336126729,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Company profile â sector & industry classification, plus coâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787337005757,
      "tokens": 161841,
      "toolCalls": 82,
      "durationMs": 879027,
      "resultPreview": "{"dataset":"Company profile â sector & industry classification, plus company name, description, CEO, employees, IPO date, logo","what_the_app_needs":"FMP `/stable/profile` is consumed by 26 backend files. Field-usage counts across `backend/app`: sector 132 refs, industry 121, description 118, marketCap 38, exchange 38, website 35, companyName 32, beta 15, cik 13, image 11, ceo 8, ipoDate 7, fullTiâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 8,
      "label": "res:gov-data-legal",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a9af06ae69274521f",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787336144070,
      "queuedAt": 1787335444778,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "LEGAL BASIS â may data pulled directly from US government sâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336667733,
      "tokens": 133339,
      "toolCalls": 32,
      "durationMs": 523662,
      "resultPreview": "{"dataset":"LEGAL BASIS â may data pulled directly from US government systems (SEC EDGAR, House Clerk financial disclosures, Senate eFD) be redistributed and DISPLAYED in a commercial consumer app?","what_the_app_needs":"A defensible right to (a) ingest SEC EDGAR filing content (13F/13D/13G institutional holdings, Forms 3/4/5 insider transactions, XBRL company facts) and (b) ingest and display conâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 9,
      "label": "synthesize",
      "phaseIndex": 2,
      "phaseTitle": "Synthesize",
      "agentId": "aa46434937d8235fc",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337012883,
      "queuedAt": 1787337007429,
      "attempt": 1,
      "promptPreview": "You are writing a BUILD-vs-BUY decision document for a solo indie developer who wants to launch a FREE
consumer investing app in TWO WEEKS, replacing the datasets Twelve Data does not cover.

RESEARCH FINDINGS (JSON):
[
 {
  "dataset": "Form 13F institutional holdings (reverse lookup for ~45 named funds, 8 quarters of history, split-adjusted net-share flow, portfolio value + sector allocation, CIKâ¦",
      "lastProgressAt": 1787337256549,
      "tokens": 153007,
      "toolCalls": 0,
      "durationMs": 243665,
      "resultPreview": "# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days against a 10-day window, a 12.5Ã overrun, roughly six calendar months of solo work.** Even the stripâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 10,
      "label": "verify:schedule",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "a61350f5a3fe772af",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337267487,
      "queuedAt": 1787337258234,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "grep -rn "summary_bullets" app/services/news_insight_servicâ¦",
      "promptPreview": "Adversarially review this build-vs-buy document. Find where it is WRONG or OPTIMISTIC.
DOCUMENT:
# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days agaâ¦",
      "lastProgressAt": 1787337606992,
      "tokens": 200671,
      "toolCalls": 28,
      "durationMs": 339504,
      "resultPreview": "## Verdict

The document is right that two weeks is impossible, and wrong about almost every number it uses to prove it. It under-counts the work in the direction that *flatters* the recommended plan: the 125 days is not reproducible from its own table, the "core migration, 3â5 days" line is off by 4Ã, and the plan it says *does* fit is ~18â25 working days, not 10. Below, everything is measured agâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 11,
      "label": "verify:licensing",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "af33a11c03ffa6939",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337266056,
      "queuedAt": 1787337258235,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "cd /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App/backeâ¦",
      "promptPreview": "Adversarially review this build-vs-buy document for LICENSING errors.
DOCUMENT:
# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days against a 10-day winâ¦",
      "lastProgressAt": 1787337586640,
      "tokens": 183189,
      "toolCalls": 5,
      "durationMs": 320583,
      "resultPreview": "# Adversarial licensing review â Build vs Buy doc

Reviewed against the seven research passes. **11 corrections**, ranked. The document is materially better than most on this topic (Â§0's Twelve Data tier trap and Â§5's Â§105 correction are both right and both non-obvious), but it repeats the exact failure mode it was written to prevent in three places.

---

## ð´ 1. BLOCKING â the plan re-displays â¦"
    }
  ],
  "totalTokens": 1837073,
  "totalToolCalls": 530
}

---

# ⏱ SCHEDULE CRITIQUE

## Verdict

The document is right that two weeks is impossible, and wrong about almost every number it uses to prove it. It under-counts the work in the direction that *flatters* the recommended plan: the 125 days is not reproducible from its own table, the "core migration, 3â5 days" line is off by 4Ã, and the plan it says *does* fit is ~18â25 working days, not 10. Below, everything is measured against the repo.

---

## (a) The effort estimates

### 1. The largest line item is a footnote, and it is wrong by 4Ã
Â§1b: *"the Twelve Data core market-data migration itself (quotes, historical, fundamentals) â budget 3â5 days pessimistically."*

Measured:
- `backend/app/integrations/fmp.py` is **2,098 lines**, **68 public `get_*` methods**, imported by **59 backend files**.
- `ls backend/app/integrations/` â `alternative_me, apewisdom, app_store, census, coingecko, finra_short_interest, fmp, fred, gemini, openfda, uspto`. **There is no Twelve Data code.** The adapter is greenfield: client, auth, credit accounting, rate limiter, typed exception hierarchy, `ErrorCode` mapping, `close_twelvedata_client()` in the `main.py` lifespan (`.claude/rules/integrations.md` requires all of it).
- At least **17 of the 68 methods have no Twelve Data equivalent at any tier**: `get_grades`, `get_price_target_consensus`, `get_stock_peers`, `get_index_constituents`, `get_sp500_constituents`, `get_etf_holders`, `get_etf_sector_weightings`, `get_etf_info`, `get_shares_float`, `get_historical_market_cap`, `get_sector_performance`, `get_industry_performance`, `get_biggest_gainers`, `get_biggest_losers`, `get_most_actives`, `get_earnings_calendar`, `get_earning_calendar_full`.

Those feed `analyst_service`, `home_dashboard_service` (Home movers), `index_service`, `etf_service`, `holders_service`, `signal_of_confidence_service`, `widget_movers_service`, `tracking_service`, `notification_senders/earnings_sender.py`.

**Correction:** the "core migration" is a *second, unscoped go-dark exercise* covering analyst ratings/targets, peers, index constituents, ETF holdings, Home market movers, and the earnings calendar. Realistic: **12â20 days**, and it produces its own dark-feature list that Â§3d does not contain.

### 2. The live-price WebSocket is not mentioned once in the document
```
app/services/live_price_manager.py:30: FMP_WS_URL_STOCK  = "wss://websockets.financialmodelingprep.com"
app/services/live_price_manager.py:31: FMP_WS_URL_CRYPTO = "wss://crypto.financialmodelingprep.com"
```
Plus `app/api/v1/endpoints/live_price.py` and an iOS WS client that passes `?token=` from `APIClient.currentAuthToken()` (auth.md Â§8). Twelve Data's WS is a different protocol with a per-plan symbol cap, and your own memory note records that **the local FMP key has no ws entitlement, so both sockets 401 locally** â i.e. you cannot prove the replacement works without a deployed key. **+3â5 days, unbudgeted, with a verification path you don't control.**

### 3. The credit math in Â§3a/Â§4 is wrong by roughly two orders of magnitude
Â§3a: *"verifyâ¦ enough credits (5,704 Ã 10 = 57,040 credits per nightly refresh)."*

That counts `/profile` only. The job actually keyed on those 5,704 tickers is `sector_benchmark_service.compute_all_benchmarks`, and `_fetch_company_data` (`app/services/sector_benchmark_service.py:821â838`) issues **ten fundamentals calls per ticker**: income (annual+quarter), cash flow (annual+quarter), ratios (annual+quarter), key metrics (annual+quarter), balance sheet (annual+quarter) â at `FMP_ANNUAL_LIMIT_BACKFILL = 16` / `FMP_QUARTERLY_LIMIT_BACKFILL = 80`.

So it is **57,040 fundamentals *calls*, not 57,040 credits.** Twelve Data prices fundamentals well above 10 credits/symbol. At even 20 credits/call and Venture's quoted 610 credits/min, one full recompute is ~31 hours. At 100, it's a week.

And there is a **second, larger sweep** the document never mentions (see Â§4 below).

**Correction:** Â§7 uncertainty #2 ("which sub-tier carries Fundamentals + 57,040 credits") is asking the wrong question with the wrong number. The trial must measure the *benchmark recompute*, not `/profile`. The "$149 vs $499" framing is likely a false choice.

### 4. There is a second taxonomy universe file, and a second sweep, neither of which appear in the document
`backend/data/industry_universe.json` â 443 KB, `"industry_count": 156, "ticker_count": 9188`, source `fmp /stable/available-industries + company-screener`. It is **larger than** `benchmark_universe.json` (153 / 5,704), which is the only file the document names.

It backs `industry_moat_benchmark_service` (`_UNIVERSE_PATH`, line 80) with `TOP_TICKERS_PER_INDUSTRY = 200` (line 63) and 5 FMP calls per peer â order **150,000 calls** per full recompute, on FMP's *and* Twelve Data's meter.

**Correction:** Â§7 uncertainty #1 ("verify a 25â30 ticker sampleâ¦ every industry in `benchmark_universe.json` resolves") is scoped to the smaller file. The startup assertion must cover **156** industries and **9,188** tickers across **both** files, plus `sector_benchmarks`, `industry_moat_benchmarks`, `industry_dossier_cache`.

### 5. The blast-radius counts are ~3Ã understated
| Doc claim | Measured |
|---|---|
| "132 sector refs" | `sector` appears **2,024** times across **78** backend files |
| "121 industry refs" | `industry` appears **1,082** times across **53** files |
| "/profile consumed by 26 backend files" | **36** files |

Those are the research author's *field-access* counts; Â§2 reuses them as a load-bearing-ness proxy. The real surface is triple.

### 6. Â§3e's "0.5 day" guidance fix is 1.5â2 days, and as specified it does not fix the bug
Verified end to end:
- Five hardcoded defaults, not one: `ticker_report_data_collector.py:2965`, `:4636`, `:6527` (`_VALID_GUIDANCE_STATUSES`), `narrative_prompts.py:1837`, and `narrative_prompts.py:915` (`rf.get("management_guidance") or "maintained"`).
- `app/schemas/ticker_report.py:132` is a bare `management_guidance: str` â a new value passes Pydantic silently.
- **The killer.** `frontend/ios/ios/Models/TickerReportResponse.swift:1142`:
  ```swift
  private static func mapGuidance(_ s: String) -> ManagementGuidance {
      switch s.lowercased() {
      case "raised": return .raised
      case "lowered": return .lowered
      default: return .maintained
      }
  }
  ```
  `ManagementGuidance` (`TickerReportModels.swift:586`) has exactly three cases. **A backend-only `not_disclosed` renders as "MAINTAINED" on screen.** The fix ships and the bug survives. You also need: the Swift enum case + `color`/`backgroundColor`, a colour token registered in `AppColors.auditManifest` or the DEBUG launch audit `assertionFailure`s (`.claude/rules/ios-swiftui.md`), the badge at `ReportFutureForecastSection.swift:91â93`, `pdf_report_service.py:592`, and `test_ticker_report_schema_parity.py`.
- **Frozen snapshots.** Reports are permanent Supabase rows, never re-fetched. Every existing report has `"maintained"` baked in. Adding a state does not un-lie them. Your own memory note `project_detail_screens_deep_check_2026_08.md` records exactly this: *an additive field's DEFAULT laundered 335 stale cache rows into a confident wrong value.* A backfill/invalidate decision belongs in the same change.

### 7. "Drop transcripts, moat cost is near-zero" is wrong at the benchmark layer
The document checks only the per-ticker pillar. But `industry_moat_benchmark_service._score_one_ticker` (lines ~176â184) fetches `self.fmp.get_earning_call_transcript(ticker)` for **every peer**, up to 200 per industry Ã 156 industries.

Drop transcripts and per-ticker scores are computed *without* the NRR driver while `industry_moat_benchmarks` still holds peer averages computed *with* it. Every "vs industry" moat verdict then silently compares two different metrics â the same class of defect as the whale "$3T exodus" note the document itself invokes. **Either wipe and recompute that table (a multi-hour-to-multi-day sweep, unpriced against TD credits) or gate the comparison. +1 day + a recompute window.**

### 8. Â§3d item 8 ("reduced display surface de-risks the dataset") is legally muddled
`news_insight_service.py:692` feeds `a.get("summary")` â the publisher's snippet â into the Gemini prompt, and the row is persisted in `ticker_news_cache`. Dropping the snippet from *display* removes nothing from **ingestion, storage, or LLM processing**. The document's own Â§0 quotes Twelve Data Grow forbidding data being *"programmatically processed, stored, transformed"* â three verbs that are not "displayed". Reducing the display surface is worth doing, but it does not "de-risk the entire dataset independent of which vendor you pick," and dropping the snippet *also* degrades the `summary_bullets` that Â§3d says replaces it.

### 9. The 13F day-5 gate is the single most dangerous sentence in the plan
Â§3c offers days 6â8 (3 days) for a build the research author sizes at 5, which itself includes the full OpenFIGI CUSIPâticker step with the CINS `/v3/search` fallback rate-limited to 20/min plus a hand-maintained override list. Realistic for a first-timer: 8â10. **Delete the gate.** A half-built 13F pipeline discovered on day 8 has no recovery path before a day-9 submit.

---

## (b) Does the plan fit in 10 working days? No â and Â§1c contradicts Â§3f

**The document's own arithmetic doesn't close:**
- Â§1a table sums to **109**, not 125 (13+15+16+15+16+12+20+2). You only reach 125 by silently valuing the "not feasible (40+)" transcripts cell at 16.
- The degraded column sums to **30â36** (5+6+7â8+7â9+5â6), not 28.
- Â§1c says **~4 days of capacity for everything in this document**. Â§3f then spends ~5 days on it (news adapter 3â5, go-dark 6â7, guidance fix). Both cannot be true.
- Days **1â3** and **3â5** overlap on day 3, so a 5-day span carries 6+ days of listed work at the doc's own optimistic rates.

**What day 8 actually contains:** "Full regression, adversarial hardening pass per CLAUDE.md, `pytest -x`." That is **298 test files / ~6,100 tests**, including **36 schema-parity tests** â every one of which is a live tripwire after an `Optional` sweep. One red parity test on day 8 consumes the entire day-10 buffer. Â§1b says code freeze is "roughly day 7â8"; Â§3f makes freeze *equal* to day 8, leaving zero days between the last code change and submission. **One day of buffer is not a buffer for an App Store rejection** â a rejection restarts the queue.

**Honest re-plan for the recommended mix** (buy profile + news, five features dark, guidance fix):

| Work | Days |
|---|---:|
| Twelve Data adapter + core migration (68 methods, 17 with no equivalent) | 12â20 |
| Live-price WebSocket replacement | 3â5 |
| News adapter + reduced display surface across 5 screens | 4â5 |
| Go-dark pass (see below) | 4â6 |
| Guidance `not_disclosed` (backend + iOS + PDF + backfill decision) | 1.5â2 |
| Moat-benchmark reconciliation + recompute | 1 + recompute window |
| Cache invalidation, migrations, taxonomy assertion | 2 |
| Regression + hardening + parity fixes | 3 |
| **Total** | **~30â44** |

Even the version the document calls "the plan that actually ships" is **3â4Ã the window**, not 1Ã.

---

## (c) Dependencies the document hides

1. **Live-price WebSocket** â two FMP sockets, an endpoint, an iOS client. Zero mentions.
2. **No Twelve Data code exists.** The plan reads as if there's an adapter to point at a new base URL. There isn't.
3. **Cache invalidation is named in Â§7 and scheduled nowhere.** Â§7 correctly notes `industry_benchmark_lookup` "falls back to the sector row on a miss with **no error, no log, and no visible symptom**" and prescribes a startup assertion â then Â§3f allocates zero time to it. Every `*_cache` row keyed on the old taxonomy is a silently-wrong row after the swap.
4. **Migration application is calendar time you do not control.** The repo is at migration **150**; `CLAUDE.local.md` says the user applies them manually via Supabase Studio and *"Claude must never run apply commands."* Any day-6/7 schema change blocks on a human. Not in the day plan.
5. **Notification senders.** `notification_senders/earnings_sender.py` consumes `get_earnings_calendar`; `config.py` documents the insider sender at "~200 FMP calls". Â§3d lists 8 UI surfaces and never touches the push layer â going dark on insider without updating the notification registry ships a push kind that either can never fire or fires on stale data.
6. **Rate-limit constants are calibrated in the wrong unit.** `config.py` comments tune prewarm against *"FMP Starter (300/min)"*, "~20-call FMP fan-out" per report, "~135 FMP calls per interval". Twelve Data meters **credits/min (610 on Venture)**, not calls/min. Every semaphore, `FMP_BATCH_SIZE`, and prewarm interval needs re-derivation â and your last two commits were literally *"Restore GEMINI_SEMAPHORE and FMP_BATCH_SIZE."*
7. **Go-dark is ~100 Swift files, not "2 days."** Swift files referencing Whale **32**, Congress **36**, Insider **46**; backend files referencing whale/congress/insider **50**. Against 36 parity tests. **4â6 days.**
8. **App Store listing.** Five dark features means screenshots and description likely need re-shooting before a day-9 submit.
9. **Two of the four "one hour" emails are on the critical path.** Â§3a says send Marketaux first *"because the answer determines whether you build against Marketaux or GNews"* â so days 3â5 are blocked on a vendor's reply SLA. There is no fallback trigger ("no reply by end of day 2 â build GNews").

---

## The three edits that would make the document honest

1. **Move the core migration into the Â§1a table** at 12â20 days with its own go-dark list (analyst ratings/targets, peers, index constituents, ETF holdings, Home movers, earnings calendar), and add a **live-price WebSocket** row at 3â5. The headline then reads *"~150â170 days against 10"* and Â§1c's "~4 days of capacity" becomes **negative**, which is the true finding.
2. **Replace the Â§3a credit check** with: *"measure one full `sector_benchmark_service.compute_all_benchmarks` (57,040 fundamentals calls) and one `industry_moat_benchmark_service.recompute_all` (156 industries Ã 200 peers Ã 5 calls) against Venture's credit meter during the trial. If either exceeds the monthly allowance, the $149 tier is not viable and the plan has no profile source."* This is a go/no-go on the whole recommendation, not a Â§7 footnote.
3. **Delete the day-5 13F gate**, re-price the guidance fix at 1.5â2 days *including the iOS enum and the frozen-row backfill*, and add a moat-benchmark reconciliation line. Then extend the schedule to ~30â44 days or cut scope further â do not keep a 10-day calendar over a 30-day plan.",
    "licensing_critique": "# Adversarial licensing review â Build vs Buy doc

Reviewed against the seven research passes. **11 corrections**, ranked. The document is materially better than most on this topic (Â§0's Twelve Data tier trap and Â§5's Â§105 correction are both right and both non-obvious), but it repeats the exact failure mode it was written to prevent in three places.

---

## ð´ 1. BLOCKING â the plan re-displays unlicensed FMP data after the migration (Â§3f, Â§2, Â§7 #1)

Â§3f Day 1â3 states verbatim:

> IPO date: **backfill once from existing FMP data and freeze**

This is the document's own thesis inverted. Â§4 says *"The app is in breach today"* under FMP personal. Copying that data once and freezing it does not cure the breach â it makes it permanent and undetectable. Governing clause (FMP ToS Â§2.2.2, quoted in the profile research):

> customers are prohibited from showcasing FMP Services or **Data** on platforms including â¦ applications designed for utilization by multiple individuals, irrespective of whether such usage is complimentary or paid

The prohibition attaches to the **Data**, not to the API call. And Â§2.2.1 separately bars integrating the data *"into any tools or applications accessible by any third parties."* Neither has a "but you fetched it earlier" exception.

**This is not limited to `ipoDate`.** I verified the FMP-derived corpus that survives a Twelve Data cutover untouched:

| Asset | Evidence |
|---|---|
| `backend/data/benchmark_universe.json` | `"source": "fmp /stable/available-industries + /stable/company-screener"`, 153 industries / 5,704 tickers, generated 2026-06-24 â **checked into the repo** |
| `backend/data/industry_universe.json` | `"source": "fmp /stable/available-industries + /stable/company-screener"`, 156 industries / 9,188 tickers |
| `sector_benchmarks`, `industry_moat_benchmarks`, `industry_dossier_cache` | keyed on the FMP industry strings from the above |
| `ticker_news_cache` | 10 backend files read it; populated from FMP news today |
| `ticker_report_data` JSONB | frozen report snapshots, never re-fetched (`pdf_report_service.py`, `chat_context_resolver.py`) |

**Correction:** Â§3f needs a cutover step the document does not contain anywhere â purge or re-derive FMP-sourced persisted data before launch. Concretely: re-generate `benchmark_universe.json` / `industry_universe.json` from Twelve Data during the trial (this is the same 25â30 ticker parity work Â§7 #1 already schedules, just extended to a full regeneration); truncate `ticker_news_cache` at the news-vendor swap; and decide explicitly what happens to pre-migration frozen reports (purge, or accept as historical and stop serving them). Budget this â it is not zero.

**Sharpest form of the finding:** Â§4's "One cost trap worth naming" identifies exactly this hazard â *"Reports are frozen point-in-time snapshots stored in Supabase forever"* â and applies it to API Ninjas Â§3.2 and Finnhub, but never to the incumbent the whole document is migrating away from. Same trap, same architecture, larger blast radius.

---

## ð´ 2. Marketaux is in the "actual display rights" column and in the launch total. It has no commercial grant. (Â§2, Â§3a, Â§4)

Â§2's column header is **"Cheapest vendor *with actual display rights*"**. The Marketaux entry sits in it. Marketaux's only legal document (website ToS, Jan 2021) grants:

> a licence **solely for your personal, non-commercial use**

and Prohibited Activities:

> The Site may not be used in connection with any **commercial endeavors** except those that are **specifically endorsed or approved by us**

"Site" is defined to include *"any other media formâ¦ mobile website or mobile application related, linked, or otherwise connected thereto"* â which reads onto the API. As of today there is no approval, so Marketaux has **zero** display rights on any tier, free or $199. It belongs in the same bucket as Stock News API and NewsAPI.org, not in a column defined by having rights.

**Corrections:**
- Move Marketaux out of that column; put **GNews Essential** there (see #6) and list Marketaux as a conditional upgrade.
- Â§4's "Launch total **$198/mo**" presents a number that assumes an approval that does not exist. Restate as `$149 (TD) + $49ââ¬49.99 (news, vendor TBD by approval)`.
- Â§3f has **no gate** for the Marketaux answer. Days 3â5 build the news adapter unconditionally. Add an explicit branch at Day 2: approval in hand â Marketaux; otherwise â GNews, images off, no entity tagging.
- Â§3a's *"One line of written approval closes the gap"* is optimistic. Require: from a named person with authority, naming the app + bundle ID, the paid tier, and the specific display surface (headline / source name / timestamp / link), retained. A support-inbox "sure, that's fine" is not a licence amendment.

---

## ð  3. The $149 Twelve Data Venture sub-tier's display right is unverified â this is the doc's own trap, one tier down (Â§0, Â§2, Â§3a, Â§4)

The document correctly proves that **Venture** carries *"External display data access"*. But the evidence in the research is the **Venture card**, headline **$499/mo**. The document then plans, prices and totals against *"from $149/mo (lowest credit sub-tier)"* and instructs: *"Start at the $149 credit sub-tier."*

Â§7 #2 flags the $149-vs-$499 question â but frames it as a **credits/Fundamentals** question ("a 3.3Ã swing on the largest line item"). It never asks the licensing question: **does the $149 credit sub-tier carry "External display data access," or is that feature attached to the $499 card?**

That distinction â cheap sub-tier sells access, headline tier sells display â is the precise pattern Â§2a says governs every row in the document.

**Correction:** add to Â§7 #2 and to the Â§3b email list: *"Confirm in writing that the $149 Venture credit sub-tier carries External display data access, not only the $499 plan."* Until answered, treat $499 (or $414/mo annual) as the planning number, which moves the launch total to **$548/mo**, not $198.

---

## ð  4. Twelve Data Venture's second limb is omitted â and the backend serves a public API (Â§0, Â§2a)

The document quotes the grant but not the restriction. Full tooltip:

> Display the data to end users in external applications, websites, or client-facing products. **Redistribution via raw data feeds or APIs is not permitted unless explicitly licensed.**

The research's own gloss: *"Serving JSON straight through your own API to your own iOS app is display, not redistribution â but **do not expose a public/partner data API on this tier**."* Twelve Data's Terms separately define Redistribution as *"any publication, distribution, or provision of Data to third parties."*

This is load-bearing here: Â§5 point 6 confirms the app has a public API surface (*"never render a raw CUSIP â¦ or expose it via the public API"*), and 46% of routes are `.public` per `auth.md` â i.e. unauthenticated callers can hit them.

**Correction:** add to Â§5 as an operational invariant alongside the CUSIP rule â TD-derived fields (sector, industry, profile, splits, logo) may be served to the app's own clients but must not be exposed on any documented/partner/public data endpoint. Worth a note in `CLAUDE.md` next to the congressional-paywall invariant Â§5 already proposes.

---

## ð  5. EODHD's published commercial tier explicitly forbids display â Â§3b makes it sound like a cheap fast win (Â§3b)

Â§3b lists the EODHD email as high-option-value: *"They advertise commercial onboarding in **3 business days** â Byte-identical FMP taxonomy *plus* IPO date."* No price, no rights caveat. Â§6 then says any one of the four emails *"could collapse weeks of this backlog."*

EODHD's commercial page:

> with the internal usage package, the data is restricted to being used solely within your company. **Displaying the data or sharing it with individuals outside your company is not permissible under this package.**

That is the **$399/mo** tier. Their personal tiers ($59.99 Fundamentals, $99.99 all-in-one) are worse â *"The packages on the pricing page are intended for personal use only."* Display therefore sits between Custom (from $399) and **Enterprise $2,499/mo**, quote-only.

**Correction:** annotate the Â§3b row â *"note: EODHD's published $399 'Internal Use' commercial tier explicitly forbids display; the quote to request is Custom/Enterprise, plausibly ~$2,499/mo."* Otherwise this email reads as a $399 escape hatch when it is most likely a 12Ã one, and IPO date is not worth $2,499/mo against 4 consumers (`etf_service.py:1015/1396`, `whale_service.py:3179`, `chat_service.py:1136`, `stock_overview_service.py:453` â verified).

---

## ð¡ 6. The news ranking is inverted on licensing grounds (Â§2, Â§2a, Â§4)

The document ranks **Marketaux primary / GNews fallback**, and Â§2a's "three vendors with published, self-serve, unambiguous display rights" excludes GNews entirely. But GNews is the only one of the two with a published commercial grant:

> **Data retrieved through the API may be used for commercial purposes**  *(paid tiers; free tier expressly excluded â "cannot be used for commercial projects")*

Marketaux has none. The ranking is correct on **features** (entity tagging, crypto/forex coverage) and backwards on **rights**. Given the developer's stated failure mode, the document should say so out loud rather than leaving Marketaux looking like the safer pick.

**Correction:** Â§2a's count is "three self-serve grants" only if GNews is excluded on a distinction the document never states. Either make it four with GNews's caveat attached, or say explicitly: *"GNews is the licensed option; Marketaux is the better product with no licence yet."*

**Also missing from Â§4's fallback line:** GNews's grant covers the *data*, not the *content*:

> Images and media content obtained through the API may be subject to copyright protection by third parties; **you are solely responsible for ensuring you have the necessary rights.**

Â§3d row 8 already drops images, which handles it â but Â§4's one-liner *"Fallback: GNews Essential â¬49.99 (loses ticker entity tagging)"* understates it. The loss is entity tagging **and** image rights **and** crypto/commodity/index coverage â i.e. four of the five screens Â§3d row 8 promises will "stay live" degrade to keyword matching.

---

## ð¡ 7. Trial-period data must not populate production caches (Â§3a, Â§3f Day 0, Â§7 #1)

Â§3f Day 0 starts the Twelve Data trial and begins the parity sample; Â§3a says *"verify during the trial."* The app's cache-aside pattern writes through to Supabase (`sector_benchmarks`, `industry_dossier_cache`, the `*_cache` tables). If the trial runs under Basic/eval terms â *"cannot be displayed to users, shared externally, or used in production systems"* â anything fetched during it and written through survives into launch as unlicensed data. Same shape as finding #1.

**Correction:** run the parity sample against a scratch table, or confirm the trial is a Venture trial under Venture terms before any write-through. One line in Â§3f Day 0.

---

## ð¡ 8. Two omitted vendor clauses (Â§4, Â§3d row 6)

- **API Ninjas Â§3.3** bars using Output to build a competing data API. Â§4 correctly captures the Â§3.2 lapse-purge trap but not this one. Relevant given the backend's public routes â a consumer app is fine; a transcript endpoint of your own is not.
- **Â§13107(c)(1)(D)** â Â§5 quotes only limb (B). The statute also bars use *"in the solicitation of money for any political, charitable, or **other purpose**."* The app has IAP. Keep congressional data out of any donation, referral, upsell or fundraising surface â a separate constraint from the paywall invariant Â§5 already names, and it should sit next to it.

---

## ð¡ 9. EDGAR's "free to reuse" does not launder third-party material inside a filing (Â§5, Â§3e)

Â§5 says *"no restriction on commercial display. This covers 13F, Forms 3/4/5, 13D/G, XBRL and 8-K exhibits alike."* The research carries a caveat the document drops:

> SEC's "free to reuse" covers the FILING. It does not launder the copyright in third-party material a filer attaches (e.g. a reprinted analyst chart inside an exhibit).

This lands directly on Â§3e / Â§3d row 7, which is the one EDGAR pipeline the document actually recommends building â scraping EX-99.x exhibits for Outlook text and a verbatim CEO quote. Filer-authored press-release prose is fine; a licensed chart, index data or third-party image inside the same exhibit is not.

**Correction:** extract text only, never re-serve exhibit images or embedded tables wholesale, and add the caveat to Â§5's list of operational obligations.

---

## ðµ 10. OpenFIGI: the standard's licence â  the service's terms (Â§5 point 6)

Â§5 asserts *"OpenFIGI (public domain, MIT, explicit redistribution rights)."* The research established that for the **FIGI identifier standard**. It did not quote the **OpenFIGI API terms of service** (listed as an evidence URL, never excerpted), and the plan depends on the free API key for the ~15,000 CUSIP mappings and the `/v3/search` CINS fallback.

**Correction:** downgrade to *"FIGI the identifier is openly licensed; confirm the OpenFIGI API ToS separately before relying on the free key in production."* Low risk, but it is an unverified rights claim sitting in the section that clears the CUSIP problem â the one genuine third-party IP claim in the whole EDGAR path.

---

## ðµ 11. Minor

- **Â§0 mis-example.** Grow's storage prohibition is said to forbid *"`ticker_news_cache`, `sector_benchmarks`, and every two-tier cache."* Twelve Data has no news endpoint (`api.twelvedata.com/news` 404s, per the news research), so `ticker_news_cache` is never TD data. `sector_benchmarks` and the profile caches are the right examples; drop the first.
- **Â§0 "exactly one Twelve Data line item."** Enterprise ($1,099/mo, per the 13F pass) presumably also carries external display. "Exactly one *affordable* line item" is the defensible claim.
- **Â§5 CUSIP attribution.** The doc says *"CUSIP Global Services (ABA/FactSet)"*; the SEC's own FAQ (quoted in the legal pass) says *"S&P Global Market Intelligence, which manages CUSIP Global Services on behalf of the American Bankers Association."* Immaterial to the mitigation, but the demand letter would come from whoever actually holds it.
- **`source_logo_url` is a live field** (`app/schemas/news.py:20,54` â verified). Â§3d row 8 says drop the publisher *image* and keep the source *name*; make the logo drop explicit, since a publisher logo is trademark use, not content reuse, and several aggregator hyperlink licences condition on links being text-only with no trademarks.

---

## Verified correct â do not re-litigate

Â§0's Twelve Data individual-tier trap and the Grow/Pro/Ultra breakdown Â· the Â§0 corollary killing the 13F pass's "cheap Grow-tier `/splits` and `/profile`" recommendation Â· Â§2a's characterisations of Finnhub ("derived results", $3,500 personal-use), Alpha Vantage ("directly or indirectly"), Quiver, Unusual Whales, WhaleWisdom, sec-api.io (genuinely ambiguous, needs the email) Â· Â§4's API Ninjas Â§3.2 lapse-purge trap and Finnhub's deletion-on-termination clause Â· Â§5's correction that 17 U.S.C. Â§105 is the wrong authority for filer-authored EDGAR content Â· Â§5's "public domain â  unrestricted" framing of Â§13107(c) and the "a vendor contract adds indemnity, not immunity" point Â· Â§6's insistence that any FMP beta waiver be in writing.

---

*Non-licensing, noted in passing since it affects the headline: Â§1a's columns don't sum. Full build across the 9 rows totals 109 excluding transcripts, 125 only if transcripts count as 16 â but that row reads "not feasible (40+)". The degraded column sums to 32â34 at the stated ranges, not 28.*"
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
      "label": "res:13f",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "ae86cdcb5ed8a3911",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335447066,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Form 13F institutional holdings (reverse lookup for ~45 namâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336417303,
      "tokens": 141788,
      "toolCalls": 56,
      "durationMs": 970235,
      "resultPreview": "{"dataset":"Form 13F institutional holdings (reverse lookup for ~45 named funds, 8 quarters of history, split-adjusted net-share flow, portfolio value + sector allocation, CIK identifiers)","what_the_app_needs":"Per-fund holdings (\"what does fund X hold\") for the 45 CIKs already in backend/data/whale_registry.json; 8 quarters per position to compute net-share-flow deltas; split adjustment acrossâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 2,
      "label": "res:congress",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a13a487f5213e06cb",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335447255,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Congressional (House + Senate) stock trading disclosures â â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336139719,
      "tokens": 140319,
      "toolCalls": 54,
      "durationMs": 691384,
      "resultPreview": "{"dataset":"Congressional (House + Senate) stock trading disclosures â STOCK Act Periodic Transaction Reports (PTRs)","what_the_app_needs":"Per-politician trade history with: ticker, transaction type (purchase/sale), the STOCK Act amount RANGE as a literal range (not a midpoint), transaction date, and disclosure/filing date. All five fields exist in the official free sources for ELECTRONICALLY filâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 3,
      "label": "res:form4",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a76315264812521ba",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335450297,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Insider transactions (SEC Form 4 / Forms 3-4-5, Section 16 â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336190941,
      "tokens": 201198,
      "toolCalls": 74,
      "durationMs": 740643,
      "resultPreview": "{"dataset":"Insider transactions (SEC Form 4 / Forms 3-4-5, Section 16 filings)","what_the_app_needs":"Per-transaction: TRANS_CODE (P/S/A/M/F/D/G/J/C/Xâ¦), signed share count, price per share, security title, insider name + role (Officer/Director/TenPercentOwner/Other + officer title), transaction date, issuer ticker. Aggregated into 12-month buy/sell dollar-flow bars and a net-flow verdict â whichâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 4,
      "label": "res:13dg-and-segmentation",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a44eb74b3ee3fdc09",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335450835,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "A: SC 13D/G beneficial ownership (founder/major-holder totaâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336436720,
      "tokens": 208892,
      "toolCalls": 72,
      "durationMs": 985883,
      "resultPreview": "{"dataset":"A: SC 13D/G beneficial ownership (founder/major-holder total stake). B: Revenue segmentation by product and by geography. Both researched together; findings labelled A/ and B/ throughout.","what_the_app_needs":"A/ For a ticker, the named 5%+ beneficial owners with total shares beneficially owned and percent of class, plus an as-of date (e.g. Larry Ellison / ORCL). Form 4 securitiesOwneâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 5,
      "label": "res:transcripts",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a774dc9556ff9a265",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335451770,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Earnings call transcripts (management guidance extraction +â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336203750,
      "tokens": 165296,
      "toolCalls": 66,
      "durationMs": 751980,
      "resultPreview": "{"dataset":"Earnings call transcripts (management guidance extraction + LLM-derived moat pillars: switching costs, network effects)","what_the_app_needs":"Three distinct things, and they have very different sourcing answers:\n\n1. GUIDANCE STATUS (raised / maintained / lowered) â currently derived by Gemini Stage A reading the FMP transcript. In `ticker_report_data_collector.py`, `_overlay_ai_guidâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 6,
      "label": "res:news",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a4538f08a56819f25",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335451399,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Financial news â per-ticker news and general market news (hâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336123155,
      "tokens": 147533,
      "toolCalls": 61,
      "durationMs": 671755,
      "resultPreview": "{"dataset":"Financial news â per-ticker news and general market news (headline, source, snippet, image, link), ~15 call sites across 5 screens (stocks, crypto, commodities, indices, Updates/market feed)","what_the_app_needs":"Per-ticker news for stocks AND crypto AND commodities AND indices, plus a general market feed. The wire model (backend/app/schemas/news.py) displays: headline, summary/snippeâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 7,
      "label": "res:profile-sector-industry",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a7a3b7bae7a57bdd9",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787336126729,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Company profile â sector & industry classification, plus coâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787337005757,
      "tokens": 161841,
      "toolCalls": 82,
      "durationMs": 879027,
      "resultPreview": "{"dataset":"Company profile â sector & industry classification, plus company name, description, CEO, employees, IPO date, logo","what_the_app_needs":"FMP `/stable/profile` is consumed by 26 backend files. Field-usage counts across `backend/app`: sector 132 refs, industry 121, description 118, marketCap 38, exchange 38, website 35, companyName 32, beta 15, cik 13, image 11, ceo 8, ipoDate 7, fullTiâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 8,
      "label": "res:gov-data-legal",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a9af06ae69274521f",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787336144070,
      "queuedAt": 1787335444778,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "LEGAL BASIS â may data pulled directly from US government sâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336667733,
      "tokens": 133339,
      "toolCalls": 32,
      "durationMs": 523662,
      "resultPreview": "{"dataset":"LEGAL BASIS â may data pulled directly from US government systems (SEC EDGAR, House Clerk financial disclosures, Senate eFD) be redistributed and DISPLAYED in a commercial consumer app?","what_the_app_needs":"A defensible right to (a) ingest SEC EDGAR filing content (13F/13D/13G institutional holdings, Forms 3/4/5 insider transactions, XBRL company facts) and (b) ingest and display conâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 9,
      "label": "synthesize",
      "phaseIndex": 2,
      "phaseTitle": "Synthesize",
      "agentId": "aa46434937d8235fc",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337012883,
      "queuedAt": 1787337007429,
      "attempt": 1,
      "promptPreview": "You are writing a BUILD-vs-BUY decision document for a solo indie developer who wants to launch a FREE
consumer investing app in TWO WEEKS, replacing the datasets Twelve Data does not cover.

RESEARCH FINDINGS (JSON):
[
 {
  "dataset": "Form 13F institutional holdings (reverse lookup for ~45 named funds, 8 quarters of history, split-adjusted net-share flow, portfolio value + sector allocation, CIKâ¦",
      "lastProgressAt": 1787337256549,
      "tokens": 153007,
      "toolCalls": 0,
      "durationMs": 243665,
      "resultPreview": "# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days against a 10-day window, a 12.5Ã overrun, roughly six calendar months of solo work.** Even the stripâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 10,
      "label": "verify:schedule",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "a61350f5a3fe772af",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337267487,
      "queuedAt": 1787337258234,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "grep -rn "summary_bullets" app/services/news_insight_servicâ¦",
      "promptPreview": "Adversarially review this build-vs-buy document. Find where it is WRONG or OPTIMISTIC.
DOCUMENT:
# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days agaâ¦",
      "lastProgressAt": 1787337606992,
      "tokens": 200671,
      "toolCalls": 28,
      "durationMs": 339504,
      "resultPreview": "## Verdict

The document is right that two weeks is impossible, and wrong about almost every number it uses to prove it. It under-counts the work in the direction that *flatters* the recommended plan: the 125 days is not reproducible from its own table, the "core migration, 3â5 days" line is off by 4Ã, and the plan it says *does* fit is ~18â25 working days, not 10. Below, everything is measured agâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 11,
      "label": "verify:licensing",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "af33a11c03ffa6939",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337266056,
      "queuedAt": 1787337258235,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "cd /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App/backeâ¦",
      "promptPreview": "Adversarially review this build-vs-buy document for LICENSING errors.
DOCUMENT:
# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days against a 10-day winâ¦",
      "lastProgressAt": 1787337586640,
      "tokens": 183189,
      "toolCalls": 5,
      "durationMs": 320583,
      "resultPreview": "# Adversarial licensing review â Build vs Buy doc

Reviewed against the seven research passes. **11 corrections**, ranked. The document is materially better than most on this topic (Â§0's Twelve Data tier trap and Â§5's Â§105 correction are both right and both non-obvious), but it repeats the exact failure mode it was written to prevent in three places.

---

## ð´ 1. BLOCKING â the plan re-displays â¦"
    }
  ],
  "totalTokens": 1837073,
  "totalToolCalls": 530
}

---

# ⚖️ LICENSING CRITIQUE

# Adversarial licensing review â Build vs Buy doc

Reviewed against the seven research passes. **11 corrections**, ranked. The document is materially better than most on this topic (Â§0's Twelve Data tier trap and Â§5's Â§105 correction are both right and both non-obvious), but it repeats the exact failure mode it was written to prevent in three places.

---

## ð´ 1. BLOCKING â the plan re-displays unlicensed FMP data after the migration (Â§3f, Â§2, Â§7 #1)

Â§3f Day 1â3 states verbatim:

> IPO date: **backfill once from existing FMP data and freeze**

This is the document's own thesis inverted. Â§4 says *"The app is in breach today"* under FMP personal. Copying that data once and freezing it does not cure the breach â it makes it permanent and undetectable. Governing clause (FMP ToS Â§2.2.2, quoted in the profile research):

> customers are prohibited from showcasing FMP Services or **Data** on platforms including â¦ applications designed for utilization by multiple individuals, irrespective of whether such usage is complimentary or paid

The prohibition attaches to the **Data**, not to the API call. And Â§2.2.1 separately bars integrating the data *"into any tools or applications accessible by any third parties."* Neither has a "but you fetched it earlier" exception.

**This is not limited to `ipoDate`.** I verified the FMP-derived corpus that survives a Twelve Data cutover untouched:

| Asset | Evidence |
|---|---|
| `backend/data/benchmark_universe.json` | `"source": "fmp /stable/available-industries + /stable/company-screener"`, 153 industries / 5,704 tickers, generated 2026-06-24 â **checked into the repo** |
| `backend/data/industry_universe.json` | `"source": "fmp /stable/available-industries + /stable/company-screener"`, 156 industries / 9,188 tickers |
| `sector_benchmarks`, `industry_moat_benchmarks`, `industry_dossier_cache` | keyed on the FMP industry strings from the above |
| `ticker_news_cache` | 10 backend files read it; populated from FMP news today |
| `ticker_report_data` JSONB | frozen report snapshots, never re-fetched (`pdf_report_service.py`, `chat_context_resolver.py`) |

**Correction:** Â§3f needs a cutover step the document does not contain anywhere â purge or re-derive FMP-sourced persisted data before launch. Concretely: re-generate `benchmark_universe.json` / `industry_universe.json` from Twelve Data during the trial (this is the same 25â30 ticker parity work Â§7 #1 already schedules, just extended to a full regeneration); truncate `ticker_news_cache` at the news-vendor swap; and decide explicitly what happens to pre-migration frozen reports (purge, or accept as historical and stop serving them). Budget this â it is not zero.

**Sharpest form of the finding:** Â§4's "One cost trap worth naming" identifies exactly this hazard â *"Reports are frozen point-in-time snapshots stored in Supabase forever"* â and applies it to API Ninjas Â§3.2 and Finnhub, but never to the incumbent the whole document is migrating away from. Same trap, same architecture, larger blast radius.

---

## ð´ 2. Marketaux is in the "actual display rights" column and in the launch total. It has no commercial grant. (Â§2, Â§3a, Â§4)

Â§2's column header is **"Cheapest vendor *with actual display rights*"**. The Marketaux entry sits in it. Marketaux's only legal document (website ToS, Jan 2021) grants:

> a licence **solely for your personal, non-commercial use**

and Prohibited Activities:

> The Site may not be used in connection with any **commercial endeavors** except those that are **specifically endorsed or approved by us**

"Site" is defined to include *"any other media formâ¦ mobile website or mobile application related, linked, or otherwise connected thereto"* â which reads onto the API. As of today there is no approval, so Marketaux has **zero** display rights on any tier, free or $199. It belongs in the same bucket as Stock News API and NewsAPI.org, not in a column defined by having rights.

**Corrections:**
- Move Marketaux out of that column; put **GNews Essential** there (see #6) and list Marketaux as a conditional upgrade.
- Â§4's "Launch total **$198/mo**" presents a number that assumes an approval that does not exist. Restate as `$149 (TD) + $49ââ¬49.99 (news, vendor TBD by approval)`.
- Â§3f has **no gate** for the Marketaux answer. Days 3â5 build the news adapter unconditionally. Add an explicit branch at Day 2: approval in hand â Marketaux; otherwise â GNews, images off, no entity tagging.
- Â§3a's *"One line of written approval closes the gap"* is optimistic. Require: from a named person with authority, naming the app + bundle ID, the paid tier, and the specific display surface (headline / source name / timestamp / link), retained. A support-inbox "sure, that's fine" is not a licence amendment.

---

## ð  3. The $149 Twelve Data Venture sub-tier's display right is unverified â this is the doc's own trap, one tier down (Â§0, Â§2, Â§3a, Â§4)

The document correctly proves that **Venture** carries *"External display data access"*. But the evidence in the research is the **Venture card**, headline **$499/mo**. The document then plans, prices and totals against *"from $149/mo (lowest credit sub-tier)"* and instructs: *"Start at the $149 credit sub-tier."*

Â§7 #2 flags the $149-vs-$499 question â but frames it as a **credits/Fundamentals** question ("a 3.3Ã swing on the largest line item"). It never asks the licensing question: **does the $149 credit sub-tier carry "External display data access," or is that feature attached to the $499 card?**

That distinction â cheap sub-tier sells access, headline tier sells display â is the precise pattern Â§2a says governs every row in the document.

**Correction:** add to Â§7 #2 and to the Â§3b email list: *"Confirm in writing that the $149 Venture credit sub-tier carries External display data access, not only the $499 plan."* Until answered, treat $499 (or $414/mo annual) as the planning number, which moves the launch total to **$548/mo**, not $198.

---

## ð  4. Twelve Data Venture's second limb is omitted â and the backend serves a public API (Â§0, Â§2a)

The document quotes the grant but not the restriction. Full tooltip:

> Display the data to end users in external applications, websites, or client-facing products. **Redistribution via raw data feeds or APIs is not permitted unless explicitly licensed.**

The research's own gloss: *"Serving JSON straight through your own API to your own iOS app is display, not redistribution â but **do not expose a public/partner data API on this tier**."* Twelve Data's Terms separately define Redistribution as *"any publication, distribution, or provision of Data to third parties."*

This is load-bearing here: Â§5 point 6 confirms the app has a public API surface (*"never render a raw CUSIP â¦ or expose it via the public API"*), and 46% of routes are `.public` per `auth.md` â i.e. unauthenticated callers can hit them.

**Correction:** add to Â§5 as an operational invariant alongside the CUSIP rule â TD-derived fields (sector, industry, profile, splits, logo) may be served to the app's own clients but must not be exposed on any documented/partner/public data endpoint. Worth a note in `CLAUDE.md` next to the congressional-paywall invariant Â§5 already proposes.

---

## ð  5. EODHD's published commercial tier explicitly forbids display â Â§3b makes it sound like a cheap fast win (Â§3b)

Â§3b lists the EODHD email as high-option-value: *"They advertise commercial onboarding in **3 business days** â Byte-identical FMP taxonomy *plus* IPO date."* No price, no rights caveat. Â§6 then says any one of the four emails *"could collapse weeks of this backlog."*

EODHD's commercial page:

> with the internal usage package, the data is restricted to being used solely within your company. **Displaying the data or sharing it with individuals outside your company is not permissible under this package.**

That is the **$399/mo** tier. Their personal tiers ($59.99 Fundamentals, $99.99 all-in-one) are worse â *"The packages on the pricing page are intended for personal use only."* Display therefore sits between Custom (from $399) and **Enterprise $2,499/mo**, quote-only.

**Correction:** annotate the Â§3b row â *"note: EODHD's published $399 'Internal Use' commercial tier explicitly forbids display; the quote to request is Custom/Enterprise, plausibly ~$2,499/mo."* Otherwise this email reads as a $399 escape hatch when it is most likely a 12Ã one, and IPO date is not worth $2,499/mo against 4 consumers (`etf_service.py:1015/1396`, `whale_service.py:3179`, `chat_service.py:1136`, `stock_overview_service.py:453` â verified).

---

## ð¡ 6. The news ranking is inverted on licensing grounds (Â§2, Â§2a, Â§4)

The document ranks **Marketaux primary / GNews fallback**, and Â§2a's "three vendors with published, self-serve, unambiguous display rights" excludes GNews entirely. But GNews is the only one of the two with a published commercial grant:

> **Data retrieved through the API may be used for commercial purposes**  *(paid tiers; free tier expressly excluded â "cannot be used for commercial projects")*

Marketaux has none. The ranking is correct on **features** (entity tagging, crypto/forex coverage) and backwards on **rights**. Given the developer's stated failure mode, the document should say so out loud rather than leaving Marketaux looking like the safer pick.

**Correction:** Â§2a's count is "three self-serve grants" only if GNews is excluded on a distinction the document never states. Either make it four with GNews's caveat attached, or say explicitly: *"GNews is the licensed option; Marketaux is the better product with no licence yet."*

**Also missing from Â§4's fallback line:** GNews's grant covers the *data*, not the *content*:

> Images and media content obtained through the API may be subject to copyright protection by third parties; **you are solely responsible for ensuring you have the necessary rights.**

Â§3d row 8 already drops images, which handles it â but Â§4's one-liner *"Fallback: GNews Essential â¬49.99 (loses ticker entity tagging)"* understates it. The loss is entity tagging **and** image rights **and** crypto/commodity/index coverage â i.e. four of the five screens Â§3d row 8 promises will "stay live" degrade to keyword matching.

---

## ð¡ 7. Trial-period data must not populate production caches (Â§3a, Â§3f Day 0, Â§7 #1)

Â§3f Day 0 starts the Twelve Data trial and begins the parity sample; Â§3a says *"verify during the trial."* The app's cache-aside pattern writes through to Supabase (`sector_benchmarks`, `industry_dossier_cache`, the `*_cache` tables). If the trial runs under Basic/eval terms â *"cannot be displayed to users, shared externally, or used in production systems"* â anything fetched during it and written through survives into launch as unlicensed data. Same shape as finding #1.

**Correction:** run the parity sample against a scratch table, or confirm the trial is a Venture trial under Venture terms before any write-through. One line in Â§3f Day 0.

---

## ð¡ 8. Two omitted vendor clauses (Â§4, Â§3d row 6)

- **API Ninjas Â§3.3** bars using Output to build a competing data API. Â§4 correctly captures the Â§3.2 lapse-purge trap but not this one. Relevant given the backend's public routes â a consumer app is fine; a transcript endpoint of your own is not.
- **Â§13107(c)(1)(D)** â Â§5 quotes only limb (B). The statute also bars use *"in the solicitation of money for any political, charitable, or **other purpose**."* The app has IAP. Keep congressional data out of any donation, referral, upsell or fundraising surface â a separate constraint from the paywall invariant Â§5 already names, and it should sit next to it.

---

## ð¡ 9. EDGAR's "free to reuse" does not launder third-party material inside a filing (Â§5, Â§3e)

Â§5 says *"no restriction on commercial display. This covers 13F, Forms 3/4/5, 13D/G, XBRL and 8-K exhibits alike."* The research carries a caveat the document drops:

> SEC's "free to reuse" covers the FILING. It does not launder the copyright in third-party material a filer attaches (e.g. a reprinted analyst chart inside an exhibit).

This lands directly on Â§3e / Â§3d row 7, which is the one EDGAR pipeline the document actually recommends building â scraping EX-99.x exhibits for Outlook text and a verbatim CEO quote. Filer-authored press-release prose is fine; a licensed chart, index data or third-party image inside the same exhibit is not.

**Correction:** extract text only, never re-serve exhibit images or embedded tables wholesale, and add the caveat to Â§5's list of operational obligations.

---

## ðµ 10. OpenFIGI: the standard's licence â  the service's terms (Â§5 point 6)

Â§5 asserts *"OpenFIGI (public domain, MIT, explicit redistribution rights)."* The research established that for the **FIGI identifier standard**. It did not quote the **OpenFIGI API terms of service** (listed as an evidence URL, never excerpted), and the plan depends on the free API key for the ~15,000 CUSIP mappings and the `/v3/search` CINS fallback.

**Correction:** downgrade to *"FIGI the identifier is openly licensed; confirm the OpenFIGI API ToS separately before relying on the free key in production."* Low risk, but it is an unverified rights claim sitting in the section that clears the CUSIP problem â the one genuine third-party IP claim in the whole EDGAR path.

---

## ðµ 11. Minor

- **Â§0 mis-example.** Grow's storage prohibition is said to forbid *"`ticker_news_cache`, `sector_benchmarks`, and every two-tier cache."* Twelve Data has no news endpoint (`api.twelvedata.com/news` 404s, per the news research), so `ticker_news_cache` is never TD data. `sector_benchmarks` and the profile caches are the right examples; drop the first.
- **Â§0 "exactly one Twelve Data line item."** Enterprise ($1,099/mo, per the 13F pass) presumably also carries external display. "Exactly one *affordable* line item" is the defensible claim.
- **Â§5 CUSIP attribution.** The doc says *"CUSIP Global Services (ABA/FactSet)"*; the SEC's own FAQ (quoted in the legal pass) says *"S&P Global Market Intelligence, which manages CUSIP Global Services on behalf of the American Bankers Association."* Immaterial to the mitigation, but the demand letter would come from whoever actually holds it.
- **`source_logo_url` is a live field** (`app/schemas/news.py:20,54` â verified). Â§3d row 8 says drop the publisher *image* and keep the source *name*; make the logo drop explicit, since a publisher logo is trademark use, not content reuse, and several aggregator hyperlink licences condition on links being text-only with no trademarks.

---

## Verified correct â do not re-litigate

Â§0's Twelve Data individual-tier trap and the Grow/Pro/Ultra breakdown Â· the Â§0 corollary killing the 13F pass's "cheap Grow-tier `/splits` and `/profile`" recommendation Â· Â§2a's characterisations of Finnhub ("derived results", $3,500 personal-use), Alpha Vantage ("directly or indirectly"), Quiver, Unusual Whales, WhaleWisdom, sec-api.io (genuinely ambiguous, needs the email) Â· Â§4's API Ninjas Â§3.2 lapse-purge trap and Finnhub's deletion-on-termination clause Â· Â§5's correction that 17 U.S.C. Â§105 is the wrong authority for filer-authored EDGAR content Â· Â§5's "public domain â  unrestricted" framing of Â§13107(c) and the "a vendor contract adds indemnity, not immunity" point Â· Â§6's insistence that any FMP beta waiver be in writing.

---

*Non-licensing, noted in passing since it affects the headline: Â§1a's columns don't sum. Full build across the 9 rows totals 109 excluding transcripts, 125 only if transcripts count as 16 â but that row reads "not feasible (40+)". The degraded column sums to 32â34 at the stated ranges, not 28.*"
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
      "label": "res:13f",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "ae86cdcb5ed8a3911",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335447066,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Form 13F institutional holdings (reverse lookup for ~45 namâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336417303,
      "tokens": 141788,
      "toolCalls": 56,
      "durationMs": 970235,
      "resultPreview": "{"dataset":"Form 13F institutional holdings (reverse lookup for ~45 named funds, 8 quarters of history, split-adjusted net-share flow, portfolio value + sector allocation, CIK identifiers)","what_the_app_needs":"Per-fund holdings (\"what does fund X hold\") for the 45 CIKs already in backend/data/whale_registry.json; 8 quarters per position to compute net-share-flow deltas; split adjustment acrossâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 2,
      "label": "res:congress",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a13a487f5213e06cb",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335447255,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Congressional (House + Senate) stock trading disclosures â â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336139719,
      "tokens": 140319,
      "toolCalls": 54,
      "durationMs": 691384,
      "resultPreview": "{"dataset":"Congressional (House + Senate) stock trading disclosures â STOCK Act Periodic Transaction Reports (PTRs)","what_the_app_needs":"Per-politician trade history with: ticker, transaction type (purchase/sale), the STOCK Act amount RANGE as a literal range (not a midpoint), transaction date, and disclosure/filing date. All five fields exist in the official free sources for ELECTRONICALLY filâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 3,
      "label": "res:form4",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a76315264812521ba",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335450297,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Insider transactions (SEC Form 4 / Forms 3-4-5, Section 16 â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336190941,
      "tokens": 201198,
      "toolCalls": 74,
      "durationMs": 740643,
      "resultPreview": "{"dataset":"Insider transactions (SEC Form 4 / Forms 3-4-5, Section 16 filings)","what_the_app_needs":"Per-transaction: TRANS_CODE (P/S/A/M/F/D/G/J/C/Xâ¦), signed share count, price per share, security title, insider name + role (Officer/Director/TenPercentOwner/Other + officer title), transaction date, issuer ticker. Aggregated into 12-month buy/sell dollar-flow bars and a net-flow verdict â whichâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 4,
      "label": "res:13dg-and-segmentation",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a44eb74b3ee3fdc09",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335450835,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "A: SC 13D/G beneficial ownership (founder/major-holder totaâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336436720,
      "tokens": 208892,
      "toolCalls": 72,
      "durationMs": 985883,
      "resultPreview": "{"dataset":"A: SC 13D/G beneficial ownership (founder/major-holder total stake). B: Revenue segmentation by product and by geography. Both researched together; findings labelled A/ and B/ throughout.","what_the_app_needs":"A/ For a ticker, the named 5%+ beneficial owners with total shares beneficially owned and percent of class, plus an as-of date (e.g. Larry Ellison / ORCL). Form 4 securitiesOwneâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 5,
      "label": "res:transcripts",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a774dc9556ff9a265",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335451770,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Earnings call transcripts (management guidance extraction +â¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336203750,
      "tokens": 165296,
      "toolCalls": 66,
      "durationMs": 751980,
      "resultPreview": "{"dataset":"Earnings call transcripts (management guidance extraction + LLM-derived moat pillars: switching costs, network effects)","what_the_app_needs":"Three distinct things, and they have very different sourcing answers:\n\n1. GUIDANCE STATUS (raised / maintained / lowered) â currently derived by Gemini Stage A reading the FMP transcript. In `ticker_report_data_collector.py`, `_overlay_ai_guidâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 6,
      "label": "res:news",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a4538f08a56819f25",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787335451399,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Financial news â per-ticker news and general market news (hâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336123155,
      "tokens": 147533,
      "toolCalls": 61,
      "durationMs": 671755,
      "resultPreview": "{"dataset":"Financial news â per-ticker news and general market news (headline, source, snippet, image, link), ~15 call sites across 5 screens (stocks, crypto, commodities, indices, Updates/market feed)","what_the_app_needs":"Per-ticker news for stocks AND crypto AND commodities AND indices, plus a general market feed. The wire model (backend/app/schemas/news.py) displays: headline, summary/snippeâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 7,
      "label": "res:profile-sector-industry",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a7a3b7bae7a57bdd9",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787336126729,
      "queuedAt": 1787335444777,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "Company profile â sector & industry classification, plus coâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787337005757,
      "tokens": 161841,
      "toolCalls": 82,
      "durationMs": 879027,
      "resultPreview": "{"dataset":"Company profile â sector & industry classification, plus company name, description, CEO, employees, IPO date, logo","what_the_app_needs":"FMP `/stable/profile` is consumed by 26 backend files. Field-usage counts across `backend/app`: sector 132 refs, industry 121, description 118, marketCap 38, exchange 38, website 35, companyName 32, beta 15, cik 13, image 11, ceo 8, ipoDate 7, fullTiâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 8,
      "label": "res:gov-data-legal",
      "phaseIndex": 1,
      "phaseTitle": "Research",
      "agentId": "a9af06ae69274521f",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787336144070,
      "queuedAt": 1787335444778,
      "attempt": 1,
      "lastToolName": "StructuredOutput",
      "lastToolSummary": "LEGAL BASIS â may data pulled directly from US government sâ¦",
      "promptPreview": "CONTEXT: An indie solo developer has a SwiftUI/FastAPI consumer investing app (Caydex),
pre-revenue, launching FREE. They currently use Financial Modeling Prep but their plan is a PERSONAL
licence that forbids showing data in a public app; a commercial licence is quote-only and may be
unaffordable. They are evaluating Twelve Data for core market data, which does NOT cover the datasets
below. They â¦",
      "lastProgressAt": 1787336667733,
      "tokens": 133339,
      "toolCalls": 32,
      "durationMs": 523662,
      "resultPreview": "{"dataset":"LEGAL BASIS â may data pulled directly from US government systems (SEC EDGAR, House Clerk financial disclosures, Senate eFD) be redistributed and DISPLAYED in a commercial consumer app?","what_the_app_needs":"A defensible right to (a) ingest SEC EDGAR filing content (13F/13D/13G institutional holdings, Forms 3/4/5 insider transactions, XBRL company facts) and (b) ingest and display conâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 9,
      "label": "synthesize",
      "phaseIndex": 2,
      "phaseTitle": "Synthesize",
      "agentId": "aa46434937d8235fc",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337012883,
      "queuedAt": 1787337007429,
      "attempt": 1,
      "promptPreview": "You are writing a BUILD-vs-BUY decision document for a solo indie developer who wants to launch a FREE
consumer investing app in TWO WEEKS, replacing the datasets Twelve Data does not cover.

RESEARCH FINDINGS (JSON):
[
 {
  "dataset": "Form 13F institutional holdings (reverse lookup for ~45 named funds, 8 quarters of history, split-adjusted net-share flow, portfolio value + sector allocation, CIKâ¦",
      "lastProgressAt": 1787337256549,
      "tokens": 153007,
      "toolCalls": 0,
      "durationMs": 243665,
      "resultPreview": "# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days against a 10-day window, a 12.5Ã overrun, roughly six calendar months of solo work.** Even the stripâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 10,
      "label": "verify:schedule",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "a61350f5a3fe772af",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337267487,
      "queuedAt": 1787337258234,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "grep -rn "summary_bullets" app/services/news_insight_servicâ¦",
      "promptPreview": "Adversarially review this build-vs-buy document. Find where it is WRONG or OPTIMISTIC.
DOCUMENT:
# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days agaâ¦",
      "lastProgressAt": 1787337606992,
      "tokens": 200671,
      "toolCalls": 28,
      "durationMs": 339504,
      "resultPreview": "## Verdict

The document is right that two weeks is impossible, and wrong about almost every number it uses to prove it. It under-counts the work in the direction that *flatters* the recommended plan: the 125 days is not reproducible from its own table, the "core migration, 3â5 days" line is off by 4Ã, and the plan it says *does* fit is ~18â25 working days, not 10. Below, everything is measured agâ¦"
    },
    {
      "type": "workflow_agent",
      "index": 11,
      "label": "verify:licensing",
      "phaseIndex": 3,
      "phaseTitle": "Verify",
      "agentId": "af33a11c03ffa6939",
      "model": "claude-opus-5",
      "state": "done",
      "startedAt": 1787337266056,
      "queuedAt": 1787337258235,
      "attempt": 1,
      "lastToolName": "Bash",
      "lastToolSummary": "cd /Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App/backeâ¦",
      "promptPreview": "Adversarially review this build-vs-buy document for LICENSING errors.
DOCUMENT:
# Build vs Buy: Replacing the FMP Datasets in Two Weeks

**Prepared from the seven-dataset research pass. Solo developer, free consumer iOS app, 10 working days.**

---

## 0. The answer, up front

**No. The full self-source plan does not fit in two weeks. It is not close â it is 125 developer-days against a 10-day winâ¦",
      "lastProgressAt": 1787337586640,
      "tokens": 183189,
      "toolCalls": 5,
      "durationMs": 320583,
      "resultPreview": "# Adversarial licensing review â Build vs Buy doc

Reviewed against the seven research passes. **11 corrections**, ranked. The document is materially better than most on this topic (Â§0's Twelve Data tier trap and Â§5's Â§105 correction are both right and both non-obvious), but it repeats the exact failure mode it was written to prevent in three places.

---

## ð´ 1. BLOCKING â the plan re-displays â¦"
    }
  ],
  "totalTokens": 1837073,
  "totalToolCalls": 530
}