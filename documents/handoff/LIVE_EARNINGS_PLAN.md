# Live earnings results — the ORIGINAL plan (2026-10-30)

> **The original plan, kept for history. It is not the design as built.** The built design is
> SYSTEM_DESIGN_GUIDELINES §11.8 and the module docstrings of `earnings_live_service`,
> `earnings_filing_service` and `earnings_live_sender`; the status is in `LIVE_EARNINGS_HANDOFF.md`.
> A **Built:** note follows the plan's text where the design differs, and where one check against
> the code found the UI or push wording different. It is not a line-by-line audit: the code decides
> anything a note leaves out. The largest differences:
> * **Window and statuses:** LIVE only on the report day, 06:00–22:00 ET (before 06:00 only once a
>   figure is in), never 04:00 to the next day's noon.
> * **Memo:** the Tracking memo keeps its window and its two speeds (no third speed, no previous
>   trading day). The live service holds the days before today itself and fetches on its own cadence.
> * **Cadence:** FMP asks about a day only while a watched report on it still wants FMP. On the
>   report day, a closed day included: every 3 min inside a release window a waiting watched company
>   uses, else every 15 min, 06:00–22:00 ET. On the next trading day: the same hours, every 3 min in
>   the morning window only. Any other day before today: every 30 min, or every 2 h while today is a
>   closed day, at any hour. SEC: every minute inside each company's own release windows, else every
>   15 min, 06:00–22:00 ET, never at weekends, at most 25 tickers at a time, most-watched first (in
>   a burst of releases the others wait their turn).
> * **Owner decisions of 2026-11-05:** filings outlive a restart in `earnings_filing_cache`
>   (migration 209); the outlook is only the company's own verified sentence; neither push buzzes in
>   the night of a user who never set Quiet Hours; both push flags ship off.
> * **Release:** the UI ships in the first App Store build that carries it, not 1.03.
>
> Kept from the first build: the feed field is `recent_earnings_alerts`, push routes carry the report
> date (`ticker_route(..., report_date=)`), and the branch bumps no version.

---


## Context
Tracking → Alerts → "Upcoming" shows Earnings Alert cards with consensus only ("INTC reports
earnings on OCT 29. EPS Est: $0.41 · Rev Est: $16.6B"). On report day the owner wants:
- a red **LIVE** label on the card's left (where the bell is) while results are coming in;
- the detail sheet to show **estimate vs actual** for EPS and Revenue, each metric the moment it
  lands, **green = beat / red = miss** with the surprise %;
- extras: **growth vs the same quarter last year**, **last 4 quarters beat/miss**, **guidance**;
- a **push the moment results land**, with its own **"Live Results"** toggle (on by default) under
  Notification settings → Earnings. "Results & Surprises" stays (also sent live now); nobody gets
  two pushes for one report.
Owner decisions (2026-10-29): FMP + company-filing fast path; push live + new toggle row; all
three extras.

> **Built:** "Results & Surprises" is not sent live as shipped. Its live-timed push has a flag of
> its own, `EARNINGS_LIVE_RESULT_PUSH_ENABLED`, which ships off (owner, 2026-11-05: "Keep it off at
> first (Recommended)"), so the daily 16:00 ET pass sends that kind, as before. The Live Results
> push ships off too (Docs, flags, rollout).

## Answer: can FMP give us the results?
- **Yes.** `/stable/earnings-calendar` rows already carry `epsActual / revenueActual` beside
  `epsEstimated / revenueEstimated`; EPS is adjusted (non-GAAP), the same basis as the consensus
  (`earnings_service.py:6`). `tracking_service._get_earnings_alerts` simply ignores the actuals.
- **But not live.** FMP fills actuals ~1–3 h after a release and an after-close report can wait
  until the next morning (`earnings_window_service.py:93-95`); our memo refreshes "today" only
  every 30 min (`EARNINGS_CALENDAR_TTL`). No time-of-day field, no FMP streaming (ToS).
- **Fast path:** the company's own 8-K Item 2.02 press release (EX-99.1) on SEC EDGAR — free,
  usually minutes after the release; the client already exists (`app/integrations/sec_edgar.py`).
  (FMP's press-release feed had ORCL's release 16 min after it went out — OWNER_TASKS.md.)

## How it fits together
```
FMP earnings-calendar (shared memo, hot TTL 2 min on live days) ─┐
SEC 8-K Item 2.02 → EX-99.1 → Gemini extract → verbatim check ───┼─► earnings_live_service
earnings_service quarters (last 4, same quarter last year) ──────┘     (per symbol+report date)
        │                                   │                                   │
        ▼                                   ▼                                   ▼
/tracking/assets (card fields)   /tracking/earnings/{t}/live (sheet)   live loop → push
                                                                         (earnings_live, then
                                                                          earnings_result ≥5%)
```

> **Built:** the shared memo has no hot TTL: the live service fetches the days it needs itself (the
> Phase 1 notes), and the SEC watch is a loop of its own beside the FMP one, both under
> `run_earnings_live_loop`.

## Phase 1 — Backend: live state from FMP
**States** (no time-of-day exists, so the window is the report day; ET throughout):
- `upcoming` — report date in the future, or report day before 04:00.
- `live` — inside the watch window (report day 04:00 → next calendar day 12:00) and results not
  complete. This is what draws LIVE: SEC every minute, FMP every 5 min.
- `reported` — complete (EPS actual, plus revenue actual when a revenue estimate exists), or the
  window ended with at least one metric in.
- `awaiting` — window ended with nothing in. "Results not in yet", no LIVE. Matches the repo's
  rule of no invented "late" status (`earnings_window_service.py:39-40`).

> **Built:** `upcoming` until 06:00 ET on the report day while nothing is in (a figure already in
> before 06:00 shows LIVE, `live_status`); `live` on the report day only, never past 22:00 ET. From
> 06:00 to 22:00 ET while LIVE, SEC is read every minute inside the company's own release windows
> and every 15 min outside until its release is read, never at weekends (a weekend report day has
> FMP alone), and FMP every 3 min inside a release window one of the day's waiting companies uses
> and every 15 min otherwise. A card that is LIVE before 06:00 is polled by neither until 06:00.
> While FMP has not confirmed it, a report dated on a trading day is asked again on the next trading
> day, 06:00–22:00 ET, every 3 min in the morning window only, and every 2 h at any hour on the
> closed days between, so a Friday report is read from Saturday 00:00 ET. A report dated on a
> closed day keeps the report-day pace on its own day. An `awaiting` card stays until 12:00 ET on
> the first trading day after its report.

**`app/services/tracking_service.py`**
- Window gains the previous trading day (`market_hours.previous_trading_day`), so after-close
  results that land overnight keep their card through the next trading day.
- Third memo speed: `EARNINGS_CALENDAR_LIVE_TTL = 300` (5 min) for a hot day, 06:00–22:00 ET.
  A hot day is the previous trading day or today with a watched row still incomplete inside its
  watch window. The owner's 2026-10-22 speeds (30 min / 6 h) apply otherwise, overnight
  included. 5 min rather than faster, because the filing path supplies the speed and FMP is the
  confirmation.
  - `_cached_earnings_calendar(fmp, today, hot_symbols=…)` derives hot days from rows already
    held for those symbols. The feed passes the user's tickers; the live loop passes everyone's
    watched set.
  - Cost: at most ~190 extra single-day calls (~1,500 rows each in season) on a report day,
    plus ~70 for the previous day's morning. Baseline today is ~150/day.
- `_get_earnings_alerts` emits actuals and status by calling `earnings_live_service.card_fields(row, …)`.
  Previous-trading-day rows go into a NEW feed field so old app builds never see a past-dated
  "reports earnings on OCT 29" card in Upcoming.

> **Built:** the Tracking memo keeps its window (today to +14) and its two speeds (30 min / 6 h):
> no third speed, no `hot_symbols`, no previous trading day. The days before today are held only in
> `earnings_live_service` (`live_days`, every calendar day from the previous trading day through
> today, and one trading day more for the sheet, `sheet_days`), which fetches them itself
> (`_fmp_cadence` and `due_days`, the owner's design of 2026-11-11), each day only while a watched
> report on it still wants FMP (`needs_fmp`):
> * on the report day, a closed day included: every 3 min inside a release window one of that
>   day's waiting companies uses (`release_windows`), every 15 min otherwise, 06:00–22:00 ET;
> * on the next trading day, the previous trading day's reports in the same hours, every 3 min in
>   the morning window alone, whatever their companies' windows;
> * any other day before today (a closed day's report after its own day, and every day before a
>   closed today): every 30 min, or every 2 h while today is a closed day, at any hour. A Friday
>   report FMP has not filled is read every 2 h from Saturday 00:00 ET.
>
> The tick pays the memo a today-and-tomorrow round at 06:00 and 15:30 ET on a trading day, and at
> any hour when it holds no row of today (a restart, or a closed day no round reached), when a held
> row is too old to open its record, or when its own fetches ended a watched report the held rows
> still list (`tick_refreshes_memo`).
> While no other day the memo holds lists anyone, as after a restart and about once a fortnight,
> that round reads the whole window instead, 13 calls more. The cost figures here are superseded
> (the `earnings_live_service` module docstring): 151 FMP calls on a day of after-close reports
> whose windows are known, 288 on the worst day, 5 on a trading day with no watched report, 1 a day
> on a weekend, and up to 163 more on a closed day with a watched report dated on it.

**`app/schemas/tracking.py`**: all fields Optional, so shipped builds keep decoding.
- `AlertResponse` gains `earnings_status`, `report_date` (ISO), `eps_actual`, `revenue_actual`,
  `eps_surprise_pct`, `revenue_surprise_pct`, `results_source` (`fmp`|`filing`),
  `eps_basis` (`adjusted`|`gaap`), `results_checked_at` (ISO UTC).
- `TrackingFeedResponse` gains `recent_earnings: List[AlertResponse] = []`.

> **Built:** eleven keys: `results_preliminary` (a bool) in place of `results_source`, plus
> `eps_checking` and `revenue_checking`. The feed field is `recent_earnings_alerts`.

**New `app/services/earnings_live_service.py`**: the single merge point.
- Per (symbol, report_date): FMP row + filing extraction (Phase 2).
- FMP wins per metric when present; values are sticky and never fall back to null.
- Surprise uses `(a-e)/|e|`, skipped when `|e| < 0.01` (as `earnings_sender.surprise_pct`). The
  EPS actual is held back as "checking" when `_earnings_common.eps_digit_shift_suspect` fires.
  Revenue is held back when actual/estimate is outside [0.5, 2].
- Pure functions for status and card fields. In-process store, the same leader/joiner dedup
  shape as the other `_inflight` maps (single worker).

> **Built:** FMP's figure wins unless the filing outranks it (`_filing_outranks_fmp`), and an FMP
> zero can give way (the zero rules). The revenue guard is a 2x band around a material consensus,
> unless the other source agrees within 2%, and more guards came with it (a figure's own rounding,
> consensus twins, an overtaken estimate): GUARDS in the module docstring. Besides the in-process
> store, verified filings outlive a restart in `earnings_filing_cache` (migration 209).

**New endpoint `GET /api/v1/tracking/earnings/{ticker}/live`** in `endpoints/tracking.py`
(`get_watchlist_identity` + `StandardRateLimit`; signed-in, as the FMP display licence needs).
- Returns `EarningsLiveResponse`: the card fields, plus per-metric `{estimate, actual, surprise_pct,
  source, basis}`, `yoy` and `last_quarters`, `guidance` with `filing` (`accession`,
  `accepted_at`, `url` from `sec_edgar.filing_index_url`), and `checked_at`.
- `yoy` and `last_quarters` come from `get_earnings_service().get_earnings(ticker)`
  (`eps_quarters`/`revenue_quarters`), so the sheet agrees with the Financials tab: last 4 reported
  quarters, and the 4th-back quarter as "same quarter last year". Cached per (ticker, report_date)
  for 1 h.
- The YoY EPS percentage is shown only when both values are positive; otherwise "$0.23 vs $0.12".

> **Built:** the payload has `year_ago` (not `yoy`), `last_quarters`, `guidance` (the company's
> verified sentence), `filing` (`url`, `accepted_at`), `preliminary`, `status` (`none` included) and
> `poll_seconds`. The extras are kept 6 h per (symbol, report date), 5 min after a failure. The
> endpoint answers 400 for a date before the first `sheet_days` day or after today + 14.

## Phase 2 — Backend: company-filing fast path
**New `app/services/earnings_filing_service.py`**
1. **Find the CIK.** Ticker → CIK via `learn_weekly.signals.ticker_cik_map()` / `cik_for()`,
   SEC's own map, cached 24 h.
2. **Watch for the filing.** For each live symbol, call `sec_edgar.get_submissions(cik)`,
   gzip, about 40 KB.
   - Once a minute in the two release windows, 06:30–09:35 ET (before the open) and 15:55–18:30
     ET (after the close).
   - Every 5 min otherwise, 06:00–22:00 ET (EDGAR's dissemination hours).
   - Look for form `8-K` with item `2.02` filed on the report date or the next day. Read
     `acceptanceDateTime` (nothing reads it yet).
   - Budget: `EARNINGS_LIVE_MAX_FILING_SYMBOLS = 40`, most-watched first. That is ≤ 0.7 req/s
     against the 5 rps limiter, so the shared 600 s circuit (Learn and the Trillion stake finder
     use it too) never opens. Stop watching a symbol once its filing is found or FMP is complete.
     On a circuit or throttle error, skip the round; never retry harder.

   > **Built:** the windows are 06:00–09:35 and 15:55–18:30 ET, and each company is read every
   > minute only inside its OWN windows (`release_windows`, learned from the acceptance times of its
   > last six results releases; both while unknown), every 15 min outside (not 5), every ticker at
   > 21:59 ET, never on weekends. An 8-K counts when accepted on or after the report date and dated
   > no later than the second trading day after it (`find_results_filings`). `acceptanceDateTime`
   > is UTC; it is read, sets the company's windows, and the log prints it in ET. The budget is
   > `EARNINGS_LIVE_SEC_MAX_SYMBOLS` = 25 tickers at a time (not 40), most-watched first, each
   > keeping for the day the place it was first given, and a ticker that joins the day's list later
   > going ahead of those the list ranks below it (`_placed`); inside a release window its own
   > company does not use, a ticker holds a slot only in the round its read is due
   > (`_holds_a_slot`). No minute reads more than 25, calls are 1 s apart, and a throttle parks
   > every ticker for SEC's Retry-After. A round works through its tickers one at a time, so in a
   > burst of releases the less-watched wait their turn.
3. **Fetch the release.** Locate EX-99.1 from the filing index: `get_filing_document(cik, acc,
   "{acc}-index.htm")`, then parse the document-type table. Fetch the exhibit and run
   `utils.filing_text.html_to_text(max_chars=40_000, log_tag="earnings-live")`.
4. **Extract.** `get_gemini_client().generate_json(..., response_schema=…, temperature=0,
   usage_tag="earnings_live_extract")`.
   - Schema: `is_quarterly_results`, `fiscal_period`, and for `revenue`, `eps_adjusted_diluted`
     and `eps_gaap_diluted` each a `{value, unit, quote}`. Plus `guidance {text, quote}`.

   > **Built:** the release is read whole (`_release_text`, `html_to_text(..., max_chars=None)`).
   > The model gets its first 40,000 characters (`_EXTRACT_TEXT_CHARS`) and every quote must come
   > from those, while the scale, dollar and outlook checks read the whole release. The schema
   > (`EXTRACTION_SCHEMA`) adds a required `currency`, gives only `revenue` a unit, and asks
   > `guidance` for a quote alone, no `text`.
5. **Verify deterministically** (the anti-hallucination gate). A figure survives only when:
   - its `quote` is a substring of the release text after whitespace and quote normalisation;
   - the number re-parsed from the quote, with its unit word (thousand / million / billion),
     equals `value`;
   - the currency is USD.
   Guidance text survives only if its quote is verbatim and every number in the text appears in
   the quote. Then the Phase-1 sanity guards apply.

   > **Built:** stricter. The stored value is the number printed in the quote, never the model's;
   > the quote must name the metric at that number, as a level of the quarter, with its own sign (a
   > sign the label's words leave open refuses the figure), and the release must prove the scale.
   > The outlook keeps only its verified quote, never a word of the model's (owner, 2026-11-05:
   > "Company's own sentence (Recommended)"), and refuses fragments, a table's cells or headings, a
   > quote that runs on past its full stop, and sentences that only point at figures. A table of
   > one column still reads as a line of a list (the handoff, §7).
6. **Choose the EPS basis.** Use adjusted EPS when present (comparable to consensus). Otherwise use
   GAAP, labelled "GAAP" in the UI with no beat/miss colour, because consensus is adjusted. This
   addresses the owner's 2026-09-27 caution (`news_insight_service.py:1519`).
7. **One extraction per accession**, kept in memory. A restart re-reads the filing once. Foreign
   filers that use 6-K fall back to FMP only.
- Each round logs `EARNINGS_LIVE_FILING sym=… acc=… accepted=… revenue=ok|dropped eps=…
  guidance=…`. Counts and reasons only, never release text.

> **Built:** a read ends as a retry, as not the release, or as the release, with at most six reads
> per filing; a model step that runs past its timeout plus 15 s is a retry. A verified filing is
> kept in `earnings_filing_cache` (migration 209), so a restart takes it back and does not read it
> again. A 6-K filer stays FMP-only; its record is checked every 30 min, with no slot. A ticker
> SEC's map lists under no company is FMP-only too, with no SEC read at all. The log line is
> `EARNINGS_LIVE_FILING sym=… day=… acc=… accepted=HH:MM ET` followed by one reason per field.

## Phase 3 — Backend: live push + "Live Results" toggle
**`app/services/notification_kinds.py`**: new `KIND_EARNINGS_LIVE = "earnings_live"`.
- `preference_key="notify_earnings_live"`, `master_preference_key="notify_earnings_alerts"`,
  `default_on=True`.
- `category=CATEGORY_EARNINGS` (cap 4/day), `LEVEL_ACTIVE`, `thread_id="earnings"`, quiet hours
  respected.
- `route_kind="ticker"`, `route_tab=TAB_FINANCIALS`, `label="Live earnings results"`,
  `expiration_hours=4`.

> **Built:** as planned, plus `night_hold_by_default` on this kind and on `earnings_result` (owner,
> 2026-11-05: "Hold until 7 AM (Recommended)"): a landing between 22:00 and 07:00 on the clock of a
> user who never set Quiet Hours buzzes at 07:00.

**New `run_earnings_live_loop()`** in `app/services/earnings_live_service.py`, spawned in
`main.py`'s lifespan beside `run_price_alert_loop` and gated the same way.
- Every 60 s:
  1. Watched tickers come from `earnings_sender._watched_tickers()` (RPC
     `get_top_watchlist_tickers`, 1000 clamp), reused.
  2. Intersect them with previous-trading-day and today rows in their watch window that are not
     yet complete.
  3. Refresh the memo with those symbols hot, then run the filing watcher.
  4. Detect the **first landing** per (symbol, date), meaning the first verified metric from
     either source.
- No cross-instance claim: one worker, and the dedup key is the lock (same as price alerts).

> **Built:** two loops under one task, each every 60 s: the FMP tick (`earnings_live_tick`) and the
> filing tick (`earnings_filing_tick`), which puts each figure on its record the moment it
> verifies. The watched list is `earnings_sender._watched_tickers_ranked`. A landing needs the
> record seen pending first.
- Dispatch, both calls with `dedup_key=f"earnings_result:{SYM}:{date}"`, the daily sender's key:
  1. `notify_watchers(kind="earnings_live")` on the first landing (only when
     `EARNINGS_LIVE_PUSH_ENABLED`). Title "INTC results are out". Body like
     "Revenue $13.7B vs $13.1B est (beat) · Adj. EPS $0.23 vs $0.01 est (beat)". A metric not
     out yet reads "EPS not out yet". The body is informational, never directive (§11.7).
  2. `notify_watchers(kind="earnings_result")` with `result_copy`, once the EPS actual on an FMP
     or adjusted basis exists and the surprise is ≥ 5%. That can be in the same round or a later
     one if revenue landed first.
  - Each step runs once per (symbol, date) in memory, and the dedup key covers restarts.
  - The dispatcher claims only users who pass the preference and cap checks
    (`push_dispatch_service.py:1254`). So a user with Live Results on gets the live push, and the
    second call skips them as a duplicate. A user with Live Results off but Results & Surprises
    on still gets the surprise. `run_earnings_notifications`' result pass later finds every key
    claimed, so there is no double send and it stays a safety net.
- Restart-safe: after a restart, rows dated today that are already reported dispatch again, and
  the dedup key makes that a no-op.
- `earnings_sender.select_results` also gets the `eps_digit_shift_suspect` guard, which it
  currently lacks.

> **Built:** `earnings_result` states FMP's adjusted EPS only, never a filing figure, and a banner
> prints a dollar figure only from the company's release: an FMP figure reads as beat or missed by
> a percentage ("Revenue beat consensus by 3%"), a figure a guard holds as "being confirmed", and a
> body showing a preliminary figure ends "From the company's release." (`live_copy`). Neither push
> starts for a report older than yesterday (`in_result_window`), and a report FMP dates twice is
> still one push (`neighbouring_result_keys`). The claim is not always "only users passing
> preference and cap": a failed settings read parks the row, and a night hold past the user's
> midnight meets the cap of the day it goes out on. A restart re-announces nothing: a report first
> seen reported, or a figure restored from the table, is not a landing, and the daily pass is the
> net for `earnings_result`.
> That pass follows the live record for a report FMP lists twice (owner, 2026-11-11: "Follow the
> record (Recommended)"), and the code does the same for a report whose EPS is 0
> (`_follows_its_record`). When it cannot read who to tell for a ticker, it tells every other
> ticker and then fails, so the next hourly wake tells the rest; the ledger stops anyone being
> told twice.

## Phase 4 — iOS (`frontend/ios/ios/`)
**Model, in `Models/TrackingModels.swift`**
- `AlertDTO` gets the new optional fields and snake_case CodingKeys. `TrackingFeedResponse` gets
  `recentEarnings` (decodeIfPresent).
- `AppAlert.EarningsData` gets `status`, `reportDate`, the actuals, surprises, source, basis and
  `checkedAt`.
- `isUpcoming` stays true, so live and reported cards stay in Upcoming, sorted first.
- Fix the consensus formatter: "-$0.25", not "$-0.25". Revenue uses adaptive units ("$23.5M",
  "$16.6B"), not "$0.0B".

> **Built:** the feed field is `recentEarningsAlerts` (`recent_earnings_alerts`), here and in the
> Tracking VM below.

**Card, in `Views/Molecules/AlertCardView.swift` and `ActivityRow.swift`**
- `ActivityRow` gains `var leadingBadge: ActivityRowBadge? = nil`. `.live` draws a new atom,
  `Views/Atoms/LiveBadge.swift`: `LiveIndicator` plus "LIVE" in `AppColors.loss` on an opaque
  `cardBackgroundLight` capsule, overlapping the bottom of the bell. All cards still render
  through `ActivityRow`, as the test pins.
- In live or reported state the trailing slot shows a new `EarningsResultChips` molecule: two mini
  capsules, "EPS ▲ 7%" and "REV ▼ 2%".
  - Colour is `gain` / `loss`, plus an `AppSentiment` arrow so the meaning doesn't rest on colour.
  - A metric not yet in reads "EPS …" in muted text.
  - Upcoming cards keep the date badge.
- Subtitle by state:
  - live: "INTC reports today. Waiting for results…"
  - reported: "INTC reported OCT 29. Revenue $17.1B vs $16.6B est · EPS $0.44 vs $0.41 est."

**Sheet: new organism `Views/Organisms/EarningsLiveResultsCard.swift`**
- Inserted at the top of `AlertDetailView.earningsDetail`. The existing details card and the
  "Open INTC" / "See the numbers" links stay below it.
- **Header:** LIVE badge and "Checked 1 min ago", or "Reported · 4:02 PM ET".
- **EPS and Revenue rows.** Each row is independent: Est, Actual, then "▲ Beat +7.3%" in green
  or "▼ Miss -2.1%" in red.
  - Reuse `EarningsQuarterResult` and `EarningsQuarterData.surpriseText`.
  - "Waiting…" placeholder until that metric lands.
  - GAAP basis shows the value with no colour, plus a note.
- **Preliminary note:** "Preliminary · from Intel's 8-K press release, 4:02 PM ET". "View filing"
  opens the in-app browser (`.inAppBrowser`) and the note disappears once FMP confirms.
- **vs last year:** "Revenue +8.2% vs Q3 '25" and "EPS $0.44 vs $0.17".
- **Last 4 quarters:** four `EarningsResultDot`s with quarter labels and surprise %.
- **Guidance**, when extracted: the text plus "From the company's release".
- **Data:** new `@StateObject EarningsLiveViewModel(ticker:)` calls
  `APIEndpoint.getEarningsLive(ticker:)`.
  - In `Core/Services/APIEndpoint.swift`, the new case needs path, `.signInRequired` and an
    explicit `authPolicy` arm.
  - Polls every 30 s while status is `live`, and stops on `reported`, `.onDisappear` and
    `willResignActive`. Resumes on `didBecomeActive`. Same lifecycle as the detail-screen polls.

> **Built:** the sheet's header follows the results card's latest answer. "Last 4 quarters" reads
> "Last quarter" or "Last N quarters", for what there is. Each quarter's caption leads with its
> verdict ("Beat +7.3%", "Miss -26.8%", "In line"), so a one-cent beat and a one-cent miss, both
> "<0.1%", no longer differ by colour alone, and VoiceOver reads each column as, for example,
> "Q3 '25: missed by 26.8%". The poll follows the server's
> `poll_seconds`: 30 s while live, 120 s while FMP can still confirm a preliminary figure, 30 s
> while the report's day is not read yet, else none. A failed first load retries every 30 s
> (bounded), and a 400 or 404 ends the poll at once. The view model and the route take the report
> date too (`getEarningsLive(ticker:reportDate:)`).
>
> The wording differs (`EarningsLiveResultsCard`): the header reads "Results vs. estimates", then
> "Checking for results · updated 1 min ago" or "Released 4:02 PM ET"; a held figure reads
> "Confirming…" and a GAAP-only EPS "Not comparable"; the note reads "Preliminary · read from
> <company>'s own press release (SEC 8-K), filed 4:02 PM ET. It updates when FMP confirms the
> figures.", with a "View the filing" button; the year-ago block is headed "Vs. the same quarter
> last year (Q3 '25)", each line the year-ago figure ("… a year ago") and its growth; the guidance
> block is headed "Outlook".

**Tracking VM, in `ViewModels/TrackingViewModel.swift`**
- Merge `recentEarnings` ahead of `alerts`.
- `startPriceRefreshTimer` keeps polling outside 04:00–20:00 ET while any filtered earnings
  alert is `live`.

> **Built:** outside the hours a price on the tab can move (`MarketHoursUtil.shouldRefreshPrices`),
> the 30 s poll no longer builds the whole feed, which pays FMP for every holding. While a LIVE
> card is on screen it asks each LIVE card's own `GET /tracking/earnings/{ticker}/live` instead
> (`refreshLiveEarningsCards`, at most 8 a round, taking turns): an answer that settles the card's
> report is written onto the card, one the server could not settle yet leaves the card LIVE, and a
> moved or unlisted report, or a 404 or 400, hands the round to one feed build. The route reads the
> shared memo and the live record, with no FMP call once the memo and the report's extras are warm.

**Alerts tab, in `Views/Organisms/AlertsTabContent.swift`**: `duplicatesUpcomingCard` also hides
`earnings_live` and `earnings_result` inbox rows for a ticker whose live or reported card is on
screen. It fails open, as today.

> **Built:** only the SAME report's rows are hidden (ticker and report date), and only while that
> card is LIVE or shows its final result.

**Push tap**
- `Models/AlertDestination.swift` gets a new target, `.earningsLive(ticker:)`, labelled "See
  live results". It is offered first for `earnings_live` and `earnings_result`.
- `tabDestination(forKind:)` gets an `earnings_live` arm with "See the numbers".
- `Views/Modifiers/AlertDestinationCover.swift` routes `.earningsLive` to a new
  `Views/Screens/EarningsLiveScreen.swift`, which wraps the same organism and VM.

> **Built:** the target is `.earningsLive(ticker:reportDate:)`, and "See live results" is offered
> only for a report date the endpoint still serves: back to the first `sheet_days` day, two trading
> days back (`MarketHoursUtil.liveResultsFloorKey`).

**Settings**
- `ViewModels/NotificationSettingsViewModel.groups` gets a new Earnings row:
  `notify_earnings_live`, "Live Results", "The moment a company you track reports: EPS and
  revenue vs. estimates".
- Default `true` in `NotificationsSettingsView.preferenceDefaults`.
- Add it to `Core/Services/SettingsSyncManager.boolKeys`. Without that, the toggle never
  reaches the server.

## Docs, flags, rollout
**Flags** in `app/config.py`:
- `EARNINGS_LIVE_ENABLED` (on) covers the hot TTL, live state, the endpoint and the loop.
- `EARNINGS_LIVE_FILINGS_ENABLED` (on) covers the SEC and Gemini fast path. It is inert while
  `SEC_EDGAR_USER_AGENT` is blank.
- `EARNINGS_LIVE_PUSH_ENABLED` (**off**) covers the `earnings_live` push.
  - It stays off until the app version with the "Live Results" toggle is in the App Store:
    shipped 1.02 has no switch for the new kind, and `device_tokens` has no app version to gate
    on.
  - The live-timed "Results & Surprises" push already has a toggle in every build, so it goes
    live with `EARNINGS_LIVE_ENABLED`.

> **Built:** there is no hot TTL. The live-timed "Results & Surprises" push has a flag of its own,
> `EARNINGS_LIVE_RESULT_PUSH_ENABLED`, and it ships OFF too (owner, 2026-11-05: "Keep it off at
> first (Recommended)"); off, the daily 16:00 ET pass sends that kind as before. Three more
> settings: `EARNINGS_LIVE_INTERVAL_SECONDS` (60), `EARNINGS_LIVE_SEC_MAX_SYMBOLS` (25) and
> `EARNINGS_LIVE_EXTRACT_MODEL`.

**Docs**
- `documents/System Design/SYSTEM_DESIGN_GUIDELINES.md`:
  - update the market-wide earnings calendar paragraph (~line 1206) with the third speed and the
    previous trading day;
  - add a new subsection "Live earnings results" (filing path, verification, statuses, push
    order, costs). Add, never renumber.
- `documents/OWNER_TASKS.md`: deploy steps, the push-flag flip after the next App Store release,
  verify steps on the next big report day, and how to turn each flag off.
- Release: the next iOS version (1.03, build 14, all four configs and `asc_review_resubmit.py`)
  carries the UI. The backend is deployed first. Old builds ignore every new field.

> **Built:** no third speed went into the market-wide calendar paragraph. The release is NOT 1.03:
> 1.03 (build 14) was cut from `main` without this work, and the UI ships in the first App Store
> build that carries it. The owner applies migration 209 before the deploy.

## Risks and how the plan handles them
- **Gemini misreads a number.** A figure survives only if its quote is verbatim in the release
  and re-parses to the same value, and it is labelled "Preliminary" until FMP confirms. Every
  drop is logged with its reason.
- **EPS basis mismatch** (GAAP vs adjusted). The basis is shown on the row, beat/miss colour only
  on adjusted or FMP basis, and the surprise push only on that basis.
- **The SEC circuit is shared with Learn and the Trillion stake finder.** Budget ≤ 0.7 req/s,
  capped symbols, skip on throttle, polling stops once a filing is found.
- **FMP bandwidth.** The 5-min hot TTL applies only to the two live days, only while a watched
  row is pending, and only 06:00–22:00 ET. That is at most about +260 calls on a report day, each
  the same single-day call shape the memo already makes.
- **Old app builds.** Every new field is optional, previous-day rows sit in a field old builds
  ignore, and the new push is held behind a flag until the toggle has shipped.
- **Reschedule duplicates.** Rows are judged by `earnings_status` rules: a future-dated row with
  actuals is garbage, and a pending past row from a reschedule never shows LIVE when the symbol
  has another pending row.

> **Built:** FMP's cost is set by the cadence in the Phase 1 note, not a 5-min TTL (one day is about
> 1,900 rows / 0.32 MB per call). The SEC budget is 25 tickers at a time, most-watched first, each
> read at its own company's cadence. Moved and duplicate rows are handled per report (A REPORT FMP
> MOVES, ONE REPORT, TWO DATES and DUPLICATE ROWS in the module docstring); a stale row FMP leaves
> beside a new date can still show LIVE and two cards until a real pair is captured (a recorded
> residual).

## Verification
**Backend (`cd backend`)**
- `./venv/bin/python -m pytest -q tests/test_earnings_live_*.py tests/test_tracking_* tests/test_notification_* tests/test_push_preference_typing.py tests/test_ios_*.py | tail -1`,
  then the full suite with `pytest -q | tail -1`.
- **New test files:**
  - `test_earnings_live_status.py`: states, the window across weekend and morning-after,
    sticky actuals, the `|e|<0.01` guard, the digit-shift hold, the revenue ratio hold.
  - `test_earnings_live_filing.py`: a quote that isn't verbatim drops the figure; a value that
    doesn't match its quote drops it; GAAP-only gets no colour; a non-USD figure is dropped;
    only one extraction per accession; the SEC budget cap and skip-on-circuit.
  - `test_earnings_live_push.py`: the live kind goes first and the surprise kind skips claimed
    users; the daily sender finds the key taken; restart is idempotent; the push flag off means
    only the surprise.
  - `test_tracking_earnings_live_feed.py`: previous-day rows appear only in `recent_earnings`;
    the hot TTL applies only while a watched row is pending; the cost bound per hour.
- **Tests to update:**
  - `test_tracking_feed_schema_parity.py`: `ALERT_KEYS` gains the 9 new keys, and the feed
    keyset becomes `{"assets", "alerts", "recent_earnings"}`. Mirror both in `AlertDTO` /
    `TrackingFeedResponse` CodingKeys.
  - `test_tracking_earnings_calendar_cache.py`: the window grows from 15 to 16 days (previous
    trading day first, so Friday + Monday..+14 on a Monday). Call counts, `_WINDOW` and the
    near/far TTL and day-roll cases update. Add a hot-TTL case.
  - `test_notification_kinds.py`, `test_push_preference_typing.py`,
    `test_ios_alerts_badge_and_filters.py`: the new kind and toggle.
  - `test_ios_auth_policy_parity.py`: add `getEarningsLive` to the "born gated" list in
    `test_account_only_endpoints_are_gated` (FMP display licence).
  - `test_ios_alerts_tab_consolidation.py`: the ActivityRow pin still holds.

> **Built:** the filing tests are `test_earnings_filing_service.py` and
> `test_earnings_filing_real_shapes.py`, the endpoint's `test_tracking_earnings_live_endpoint.py`.
> There is no `test_tracking_earnings_live_feed.py`: the feed's cards are tested in
> `test_earnings_live_service.py` and the schema parity test. `getEarningsLive` is not in
> `test_account_only_endpoints_are_gated`'s list: `test_ios_earnings_live_guards.py` pins it
> `.signInRequired`. The feed keyset is `{"assets", "alerts", "recent_earnings_alerts"}`, with 11
> new alert keys. The memo's window did not grow, so `test_tracking_earnings_calendar_cache.py`
> pins the tick's today-and-tomorrow rounds instead. The minute-by-minute cost walks are pinned in
> `test_earnings_live_service.py`. The command to run is the targeted list in the handoff (§9); a
> run is green only at exit 0. Not the globs above: `tests/test_ios_*.py` and the full suite include
> files that run the Swift toolchain, which needs the Swift build slot first (CLAUDE.md, Machine
> safety).

**iOS**
- Build the `ios` scheme in Xcode. The owner does this; there is no Xcode in this container.
- Previews for `EarningsLiveResultsCard` in each state: waiting, partial (revenue only), beat
  and miss, preliminary, GAAP-only, awaiting.

> **Built:** the app was built locally with the canonical command in CLAUDE.md, under its Machine
> safety rules: green on 2026-11-05, on 2026-11-11 and on 2026-11-12, the last with the branch's
> last Swift change (the handoff, §5). `EarningsLiveResultsCard` has one preview, on sample data,
> not one per state. The UI was checked on the Simulator against a local backend from the branch,
> with real reports (the handoff, §5).

**End-to-end on the next report day** (e.g. a watched after-close reporter):
1. Card shows LIVE from 04:00 ET.
2. Within about 1–2 min of the 8-K, `EARNINGS_LIVE_FILING … revenue=ok eps=ok` is logged, the
   sheet shows "Preliminary" numbers with green or red, and the push arrives.
3. When FMP fills, the source flips to FMP and the "Preliminary" note goes.
4. `notification_events` holds exactly one `earnings_result:<SYM>:<date>` row per watcher.
5. The next morning the card is still there as "Reported".

> **Built:** LIVE shows from 06:00 ET (earlier only once a figure is in). With both push flags off, as shipped, nothing is pushed
> live: the daily 16:00 ET pass tells a clear surprise under the same key. Step 4 also means no
> second row under a neighbouring date for a report FMP dates twice. The checks that stand are in
> OWNER_TASKS.md.
