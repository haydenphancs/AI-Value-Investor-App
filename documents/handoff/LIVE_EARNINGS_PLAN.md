# Live earnings results — the APPROVED PLAN (copied from the cloud session, 2026-10-30)

> Kept for reference. The implementation (commit `28a7438`) differs from this plan in a few places, on the planning reviewer's advice:
> * The Tracking memo (`tracking_service._cached_earnings_calendar`, 15 days, 30 min / 6 h) was left **untouched**. The live state has its **own two-day memo** (previous trading day + today) in `earnings_live_service`, refreshed only by the 60 s loop, at a **5-min** hot cadence (not 2 min).
> * The new feed field is named `recent_earnings_alerts` (not `recent_earnings`).
> * The SEC budget is `EARNINGS_LIVE_SEC_MAX_SYMBOLS = 25` (not 40); calls are spaced 1 s apart.
> * `ticker_route(..., report_date=)` carries the report date on earnings push routes; the daily sender sends it too.
> * No `MARKETING_VERSION` bump was made — that is the owner's release step (OWNER_TASKS §2.3).

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

## Phase 1 — Backend: live state from FMP
**States** (no time-of-day exists, so the window is the report day; ET throughout):
- `upcoming` — report date in the future, or report day before 04:00.
- `live` — inside the watch window (report day 04:00 → next calendar day 12:00) and results not
  complete. This is what draws LIVE: SEC every minute, FMP every 5 min.
- `reported` — complete (EPS actual, plus revenue actual when a revenue estimate exists), or the
  window ended with at least one metric in.
- `awaiting` — window ended with nothing in. "Results not in yet", no LIVE. Matches the repo's
  rule of no invented "late" status (`earnings_window_service.py:39-40`).

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

**`app/schemas/tracking.py`**: all fields Optional, so shipped builds keep decoding.
- `AlertResponse` gains `earnings_status`, `report_date` (ISO), `eps_actual`, `revenue_actual`,
  `eps_surprise_pct`, `revenue_surprise_pct`, `results_source` (`fmp`|`filing`),
  `eps_basis` (`adjusted`|`gaap`), `results_checked_at` (ISO UTC).
- `TrackingFeedResponse` gains `recent_earnings: List[AlertResponse] = []`.

**New `app/services/earnings_live_service.py`**: the single merge point.
- Per (symbol, report_date): FMP row + filing extraction (Phase 2).
- FMP wins per metric when present; values are sticky and never fall back to null.
- Surprise uses `(a-e)/|e|`, skipped when `|e| < 0.01` (as `earnings_sender.surprise_pct`). The
  EPS actual is held back as "checking" when `_earnings_common.eps_digit_shift_suspect` fires.
  Revenue is held back when actual/estimate is outside [0.5, 2].
- Pure functions for status and card fields. In-process store, the same leader/joiner dedup
  shape as the other `_inflight` maps (single worker).

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
3. **Fetch the release.** Locate EX-99.1 from the filing index: `get_filing_document(cik, acc,
   "{acc}-index.htm")`, then parse the document-type table. Fetch the exhibit and run
   `utils.filing_text.html_to_text(max_chars=40_000, log_tag="earnings-live")`.
4. **Extract.** `get_gemini_client().generate_json(..., response_schema=…, temperature=0,
   usage_tag="earnings_live_extract")`.
   - Schema: `is_quarterly_results`, `fiscal_period`, and for `revenue`, `eps_adjusted_diluted`
     and `eps_gaap_diluted` each a `{value, unit, quote}`. Plus `guidance {text, quote}`.
5. **Verify deterministically** (the anti-hallucination gate). A figure survives only when:
   - its `quote` is a substring of the release text after whitespace and quote normalisation;
   - the number re-parsed from the quote, with its unit word (thousand / million / billion),
     equals `value`;
   - the currency is USD.
   Guidance text survives only if its quote is verbatim and every number in the text appears in
   the quote. Then the Phase-1 sanity guards apply.
6. **Choose the EPS basis.** Use adjusted EPS when present (comparable to consensus). Otherwise use
   GAAP, labelled "GAAP" in the UI with no beat/miss colour, because consensus is adjusted. This
   addresses the owner's 2026-09-27 caution (`news_insight_service.py:1519`).
7. **One extraction per accession**, kept in memory. A restart re-reads the filing once. Foreign
   filers that use 6-K fall back to FMP only.
- Each round logs `EARNINGS_LIVE_FILING sym=… acc=… accepted=… revenue=ok|dropped eps=…
  guidance=…`. Counts and reasons only, never release text.

## Phase 3 — Backend: live push + "Live Results" toggle
**`app/services/notification_kinds.py`**: new `KIND_EARNINGS_LIVE = "earnings_live"`.
- `preference_key="notify_earnings_live"`, `master_preference_key="notify_earnings_alerts"`,
  `default_on=True`.
- `category=CATEGORY_EARNINGS` (cap 4/day), `LEVEL_ACTIVE`, `thread_id="earnings"`, quiet hours
  respected.
- `route_kind="ticker"`, `route_tab=TAB_FINANCIALS`, `label="Live earnings results"`,
  `expiration_hours=4`.

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

## Phase 4 — iOS (`frontend/ios/ios/`)
**Model, in `Models/TrackingModels.swift`**
- `AlertDTO` gets the new optional fields and snake_case CodingKeys. `TrackingFeedResponse` gets
  `recentEarnings` (decodeIfPresent).
- `AppAlert.EarningsData` gets `status`, `reportDate`, the actuals, surprises, source, basis and
  `checkedAt`.
- `isUpcoming` stays true, so live and reported cards stay in Upcoming, sorted first.
- Fix the consensus formatter: "-$0.25", not "$-0.25". Revenue uses adaptive units ("$23.5M",
  "$16.6B"), not "$0.0B".

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

**Tracking VM, in `ViewModels/TrackingViewModel.swift`**
- Merge `recentEarnings` ahead of `alerts`.
- `startPriceRefreshTimer` keeps polling outside 04:00–20:00 ET while any filtered earnings
  alert is `live`.

**Alerts tab, in `Views/Organisms/AlertsTabContent.swift`**: `duplicatesUpcomingCard` also hides
`earnings_live` and `earnings_result` inbox rows for a ticker whose live or reported card is on
screen. It fails open, as today.

**Push tap**
- `Models/AlertDestination.swift` gets a new target, `.earningsLive(ticker:)`, labelled "See
  live results". It is offered first for `earnings_live` and `earnings_result`.
- `tabDestination(forKind:)` gets an `earnings_live` arm with "See the numbers".
- `Views/Modifiers/AlertDestinationCover.swift` routes `.earningsLive` to a new
  `Views/Screens/EarningsLiveScreen.swift`, which wraps the same organism and VM.

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

**iOS**
- Build the `ios` scheme in Xcode. The owner does this; there is no Xcode in this container.
- Previews for `EarningsLiveResultsCard` in each state: waiting, partial (revenue only), beat
  and miss, preliminary, GAAP-only, awaiting.

**End-to-end on the next report day** (e.g. a watched after-close reporter):
1. Card shows LIVE from 04:00 ET.
2. Within about 1–2 min of the 8-K, `EARNINGS_LIVE_FILING … revenue=ok eps=ok` is logged, the
   sheet shows "Preliminary" numbers with green or red, and the push arrives.
3. When FMP fills, the source flips to FMP and the "Preliminary" note goes.
4. `notification_events` holds exactly one `earnings_result:<SYM>:<date>` row per watcher.
5. The next morning the card is still there as "Reported".
