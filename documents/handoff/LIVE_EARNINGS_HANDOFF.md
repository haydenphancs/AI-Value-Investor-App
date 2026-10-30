# Live earnings results — handoff (cloud session → next local session)

Written 2026-10-30 at the end of a Claude Code cloud session (the owner ran out of cloud credit).
Everything needed to continue is in this folder and on the branch below. Nothing lives only in
the old session.

| File | What it is |
|---|---|
| `documents/handoff/LIVE_EARNINGS_HANDOFF.md` | **This file. Read it first.** State, next steps, the paste-ready prompt. |
| `documents/handoff/LIVE_EARNINGS_REVIEW_FINDINGS.md` | 40 findings from an independent multi-agent review of the commit, each with status (CONFIRMED / PARTIAL / UNVERIFIED), the failure scenario, the suggested fix and the panel's reasoning. |
| `documents/handoff/live_earnings_review_raw.json` | The same review, raw (every reviewer's output and every panel verdict). |
| `documents/handoff/LIVE_EARNINGS_PLAN.md` | The approved plan, with a note on where the implementation deviated. |
| `documents/handoff/review-live-earnings.workflow.js` | The review workflow script, to re-run the review locally (adjust the paths at the top). |
| `documents/System Design/SYSTEM_DESIGN_GUIDELINES.md` §11.8 | The design as built (also §7.1, §7.4, §11.4). |
| `documents/OWNER_TASKS.md` | Row 29 (the new loop) and the "Live earnings results" item at the top of §2.1 (deploy / verify / flip-the-flag steps). |

---

## 1. Where things stand

- **Branch:** `claude/gifted-ramanujan-dpccwg` (pushed to `origin`). **No PR opened** (the owner did
  not ask for one).
- **Feature commit:** `28a7438` "Live earnings results: LIVE card, estimate-vs-actual sheet, filing
  fast path, live push". Parent: `84788ba` (main at the time).
- **Handoff commit:** the commit that added this folder (on top of `28a7438`).
- **Backend:** written, tested (see §4). **Not deployed.**
- **iOS:** written, **NOT compiled** — the cloud container has no Xcode/Swift compiler. Only a
  syntax check (tree-sitter) and the repo's Python source-scan tests were run. **First local step:
  build in Xcode.**
- **Review:** an independent review found **10 CONFIRMED defects** (1 high, 5 medium, 4 low) plus 29
  unverified leads. **None are fixed yet.** See §5. Fix them before merging.

Resume locally:

```bash
git fetch origin claude/gifted-ramanujan-dpccwg
```

```bash
git checkout claude/gifted-ramanujan-dpccwg
```

```bash
git diff 84788ba..28a7438 --stat
```

---

## 2. What the owner asked for (and decided)

On INTC's report day (2026-10-29), the owner asked:

> "today we have earning report for INTEL. i want to add a feature. so the result will show right
> away on the second image too. we can add more on the card. like a compare results for estimate vs
> actual. so users can watch them. can we do a 'live' results? (we can mark a 'live' red color label
> at the card on the left (Upcoming section), so users can know this is live) what come out first
> will be shown right away. example, if Revenue come out, then show right away. we need to show red
> or green color for miss or beat also. check this for me if FMP can give us the earning report
> result. or anyway to implement this feature?"

The "second image" was the Earnings Alert detail sheet (Tracking → Alerts → Upcoming card → sheet
with Ticker / Company / Consensus / Date, "Open INTC", "See the numbers").

Decisions the owner made (their own words in quotes):

1. Data source: **"FMP + company filing (Recommended)"** — FMP, plus a fast path that reads the
   company's own 8-K press release from SEC.
2. Push: **"yes, push live. also, i need to have a toggle to let users turn on or off this
   notification also. check notification setting to see more"**.
3. Toggle shape: **"New 'Live Results' row (Recommended)"** — a new row under Notification settings →
   Earnings, on by default; "Results & Surprises" stays (and is now sent live too); nobody gets two
   pushes for one report.
4. Extras on the sheet: **"Growth vs last year, Last 4 quarters, Guidance"**.

## 3. The answer to "can FMP give us the earnings result?"

- **Yes.** FMP's `/stable/earnings-calendar` rows carry `epsActual` and `revenueActual` next to
  `epsEstimated` / `revenueEstimated` (adjusted EPS — the consensus's own basis). The app already
  downloaded them for the Upcoming card and ignored them.
- **But not live.** FMP fills the actuals roughly 1–3 h after a release (FMP's cycle-times page and an
  FMP guide, as surfaced by web search — the FMP site itself was blocked from the cloud container),
  and the repo's own notes say an after-close report can wait until the next morning
  (`earnings_window_service.py`). The Tracking memo also refreshed "today" only every 30 min.
- **Fastest source:** the company's 8-K Item 2.02 press release (EX-99.1) on SEC EDGAR, usually
  minutes after the release. The repo already had an SEC client (`app/integrations/sec_edgar.py`).
  FMP's press-release feed was seen 16 min late for ORCL (OWNER_TASKS).

## 4. What was built (commit `28a7438`) and how it was verified

Design write-up: SYSTEM_DESIGN_GUIDELINES §11.8. In short:

**Backend**
- `app/services/earnings_live_service.py` (new): one sticky record per (symbol, report date) merging
  FMP's calendar row with the filing figures; statuses `upcoming` / `live` (report day 06:00–22:00 ET,
  results incomplete) / `reported` / `awaiting` (never "late"); guards (dropped-digit EPS, implausible
  revenue vs a ≥ $100M consensus, GAAP-only EPS shown but never coloured, no % under a $0.01
  estimate); a two-day memo (previous trading day + today) refreshed by a new 60 s loop
  (`run_earnings_live_loop`, spawned in `main.py` with the notification loops); history extras (last 4
  quarters, year-ago quarter) from `earnings_service.get_earnings`, shielded; `live_payload` for the
  sheet.
- `app/services/earnings_filing_service.py` (new): finds the 8-K Item 2.02, locates EX-99.1 from the
  filing index page, Gemini extraction (`generate_json`, temperature 0, no cache), and a verbatim
  check (`filing_text.verify_peg_span` + re-reading the number from the quote). Budget: 25 symbols,
  1 call/min in the release windows (06:00–09:35, 15:55–18:30 ET), 5 min otherwise, calls 1 s apart,
  parks on SEC throttling.
- `app/services/notification_senders/earnings_live_sender.py` (new): `earnings_live` push first, then
  the live-timed `earnings_result` (FMP numbers only, ≥ 5% EPS surprise), both under the daily
  sender's dedup key `earnings_result:{SYM}:{date}` → one push per report.
- `notification_kinds.py`: new kind `earnings_live` (pref `notify_earnings_live`, default on, Earnings
  master, category cap shared); `ticker_route(..., report_date=)`.
- `earnings_sender.py`: `result_dedup_key()`, `_watched_tickers_ranked()`, dropped-digit guard in
  `select_results`, report date on its routes.
- `tracking_service.py`: live fields on today's earnings cards; new
  `TrackingFeedResponse.recent_earnings_alerts` (previous day's cards — NOT in `alerts`, so shipped
  1.02 builds never show a past-dated card); `get_earnings_live()` for the new endpoint.
- `schemas/tracking.py`: 9 optional `AlertResponse` keys; `EarningsLive*Response` models.
- `api/v1/endpoints/tracking.py`: `GET /api/v1/tracking/earnings/{ticker}/live?report_date=` (signed-in
  only, StandardRateLimit).
- `config.py` flags: `EARNINGS_LIVE_ENABLED` (on), `EARNINGS_LIVE_FILINGS_ENABLED` (on; inert without
  `SEC_EDGAR_USER_AGENT`), `EARNINGS_LIVE_PUSH_ENABLED` (**off** until 1.03 ships the toggle),
  `EARNINGS_LIVE_RESULT_PUSH_ENABLED` (on), `EARNINGS_LIVE_INTERVAL_SECONDS` (60),
  `EARNINGS_LIVE_SEC_MAX_SYMBOLS` (25), `EARNINGS_LIVE_EXTRACT_MODEL` (gemini-2.5-flash).

**iOS** (`frontend/ios/ios/`)
- New: `Models/EarningsLiveModels.swift`, `ViewModels/EarningsLiveViewModel.swift`,
  `Views/Atoms/LiveBadge.swift`, `Views/Molecules/EarningsResultChips.swift`,
  `Views/Organisms/EarningsLiveResultsCard.swift`, `Views/Screens/EarningsLiveScreen.swift`.
- Changed: `TrackingModels.swift` (live fields, state-aware copy, consensus "-$0.25" / "$23.5M" fix),
  `ActivityRow.swift` (`leadingBadge: .live`), `AlertCardView.swift`, `AlertDetailView.swift` (results
  card on top), `AlertDestination.swift` ("See live results" for result pushes), `AlertDestinationCover`,
  `AlertsTabContent.swift` (hide a result row only when the SAME report's card is on screen),
  `TrackingViewModel.swift` (merge recent cards; keep polling past 20:00 ET while LIVE),
  `APIEndpoint.swift` (`getEarningsLive`, `.signInRequired`), settings row "Live Results" +
  `SettingsSyncManager.boolKeys`.

**Verification done in the cloud**
- New backend tests (all pass): `tests/test_earnings_live_status.py`, `test_earnings_live_service.py`,
  `test_earnings_live_push.py`, `test_earnings_filing_service.py`,
  `test_tracking_earnings_live_endpoint.py`, `test_ios_earnings_live_guards.py` (iOS source scans,
  mutation-checked). Updated: feed parity, lifespan loops, in-flight safety, design-doc parity.
- Targeted run: **805 passed**. Full backend suite: **74,553 passed, 40 failed, 2 collection errors** —
  **no regressions**: 39 failures also fail on a clean checkout of `84788ba` in that container
  (missing gitignored `CLAUDE.md` / `.claude/rules`, no `SUPABASE_JWT_SECRET`, missing local data
  files, fonts…), and the 40th was the old name of a test renamed mid-run. The 2 errors
  (`test_ios_symbol_availability.py`, `test_password_rejection_contract.py`) are environment-only too.
  On your machine, with your real `backend/.env`, most of those should pass.
- iOS: tree-sitter syntax check clean on every changed Swift file; `frontend/ios/scripts/theme-lint.sh`
  clean; all `tests/test_ios_*.py` pass except the pre-existing screenshot-fixture test.
- **NOT verified:** an Xcode build; a real report day end to end; that SEC's `acceptanceDateTime`
  digits are Eastern time (the code assumes so — OWNER_TASKS step 2 says how to check); FMP's docs
  (blocked from the container).

## 5. What to do next (in this order)

### 5a. Fix the review findings

Full details, failure scenarios and suggested fixes: `LIVE_EARNINGS_REVIEW_FINDINGS.md` (R-numbers).
Several reviewers found the same issue independently; grouped here.

**P0 — confirmed, high**
1. **Stale landings are re-dispatched every 60 s** (R1, R8). `EarningsLiveService.refresh()` prunes
   records outside {previous trading day, today} only when a fetch is due, and `landed_snapshots()`
   does not filter by day; the sender's `_prune()` then forgets the key, so every tick re-dispatches
   the old report. The DB dedup key stops a second buzz, but each tick re-runs the watcher lookup and
   claims for every watcher. Fix: prune records on every tick (or in `landed_snapshots`, return only
   days in {prev, today}), and make the sender's memory and the service agree on the window.

**P1 — confirmed, medium**
2. **FMP's duplicate same-date rows** (a pending + a reported row for one symbol/date) are folded one
   at a time, so a fresh process counts the pair as a landing (late push) and can pair one row's
   estimate with the other's actual (R2, R10, R18). Fix: group rows by (symbol, date) per fold, prefer
   the reported row's values, and treat "reported at first sight" as not a landing.
3. **Every company's landing reaches the push sender** (the loop's own fetch folds the whole market),
   so unwatched landings use up `MAX_SENDS_PER_ROUND` before watched ones (R3, R9). Fix: filter
   landed snapshots to the watched set before dispatch.
4. **Filing verification is looser than claimed** (R19, R20, R21, R32): the EPS sign check looks at the
   whole quote instead of the matched number; a revenue with unit "units" accepts a table figure that
   the table header scales (e.g. "in millions"); EPS accepts ±0.5% and returns the model's value
   instead of the printed one. Fix: match the exact printed token, derive the sign from that token,
   refuse "units" for revenue unless the quote prints a $ amount ≥ $1,000,000 with no header scale,
   return the printed number.

**P2 — confirmed, low**
5. Tracking-memo rows (up to 30 min old) are folded AFTER the loop's fresher fetch and overwrite newer
   FMP figures (R4; also R13 for feed builds / sheet polls). Fix: never let an older fetch replace a
   newer one — keep a fetched-at per source and skip memo rows older than the loop's own fetch.
6. Guidance grounding only checks bare digits (R22). Fix: require the scale words and sign too, or
   show the verbatim quote instead of Gemini's paraphrase.
7. A permanent `SECEdgarDocumentTooLargeException` on the exhibit is retried every round all day (R23).
   Fix: treat it as final for that accession.

**Unverified leads worth checking (likely real, cheap to fix)**
- The sheet endpoint ignores `EARNINGS_LIVE_ENABLED=false` (R15, R28, R33) → return the plain upcoming
  payload (no LIVE, no poll) when the flag is off.
- "See live results" is offered on result notifications whose report date is older than the previous
  trading day, and the endpoint answers 400 for those (R11, R14, R24) → offer it only for recent
  dates, or let the endpoint serve older dates read-only.
- The sheet header (`AlertDetailView`) keeps the text from the moment of the tap and can contradict the
  live card below it (R25).
- A failed FIRST load in the Tracking sheet stops polling for good with no retry (R16, R26).
- The new `earnings_live` kind has no inbox icon (`NotificationModels.swift`, R12, R31).
- The footnote says "EPS is adjusted" even when the row shows GAAP EPS (R30).
- The filing path makes a report `reported` while its figures are still preliminary, so the sheet stops
  polling before FMP confirms (R27).
- A Friday "awaiting" card disappears Saturday noon and reappears Sunday/Monday (R7, R39).
- The "next day" filing window uses the next CALENDAR day, so Friday/pre-holiday evening 8-Ks are
  missed (R38).
- The SEC cap is per round, not "25 tickers a day" as documented (R34); foreign filers are polled
  although the docs say FMP-only (R35); a `revenueActual` of 0 bypasses the guard (R6).
- Test gaps: nothing pins that the daily sender uses the shared dedup key (R36); one filing test passes
  for the wrong reason (R37); the OWNER_TASKS SQL check cannot fail (R40).

After fixing: re-run the targeted tests (§6), re-run the review on the new diff if possible
(`review-live-earnings.workflow.js`), update §11.8 / OWNER_TASKS where behaviour changed.

### 5b. Build the iOS app in Xcode
Open `frontend/ios/ios.xcodeproj`, build the `ios` scheme, fix any compile errors (the code was written
without a compiler: watch for MainActor-isolation warnings, memberwise-init label order on
`AppAlert.EarningsData` / `ActivityRow`, and the new `AlertDestination.Target.earningsLive` case).
Check the previews of `EarningsLiveResultsCard`, `LiveBadge`, `EarningsResultChips`, light and dark.

### 5c. Run the backend tests locally
See §6.

### 5d. Open the PR, deploy, verify, ship
1. PR from `claude/gifted-ramanujan-dpccwg` (owner's call).
2. Deploy the backend; follow OWNER_TASKS §2.1 "Live earnings results" (verify on the next big report
   day: the `EARNINGS_LIVE_FILING … accepted=HH:MM ET` log line, figures vs the press release, one
   `notification_events` row per watcher per report).
3. Ship iOS 1.03 (build 14) per OWNER_TASKS §2.3 (bump `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION`
   in all four configs and `_VERSION` in `scripts/asc_review_resubmit.py`).
4. After 1.03 is widely installed: `EARNINGS_LIVE_PUSH_ENABLED=true` on Railway.

## 6. Commands

From `backend/` (your local `.env` and venv):

```bash
./venv/bin/python -m pytest -q tests/test_earnings_live_status.py tests/test_earnings_live_service.py tests/test_earnings_live_push.py tests/test_earnings_filing_service.py tests/test_tracking_earnings_live_endpoint.py tests/test_ios_earnings_live_guards.py
```

```bash
./venv/bin/python -m pytest -q tests/test_tracking_feed_schema_parity.py tests/test_tracking_earnings_calendar_cache.py tests/test_notification_kinds.py tests/test_push_preference_typing.py tests/test_notification_senders.py tests/test_earnings_sender_per_day_calendar.py tests/test_notification_route_targets.py tests/test_notification_schema_parity.py tests/test_ios_alerts_badge_and_filters.py tests/test_ios_auth_policy_parity.py tests/test_lifespan_local_notification_jobs.py tests/test_inflight_cancellation_safety.py tests/test_ios_theme_parity.py tests/test_system_design_doc_parity.py
```

From the repo root:

```bash
bash frontend/ios/scripts/theme-lint.sh
```

## 7. Paste this into the new session

> Continue the "live earnings results" feature on branch `claude/gifted-ramanujan-dpccwg`. Read
> `documents/handoff/LIVE_EARNINGS_HANDOFF.md` first, then `documents/handoff/LIVE_EARNINGS_REVIEW_FINDINGS.md`.
> The feature is implemented in commit `28a7438` but not merged. Do, in order: (1) fix the CONFIRMED
> review findings (P0 → P2 in §5a of the handoff), verifying each unverified lead before fixing it;
> (2) add tests for every fix and run the targeted tests in §6; (3) update SYSTEM_DESIGN_GUIDELINES
> §11.8 and OWNER_TASKS where behaviour changed; (4) build the iOS app in Xcode and fix compile
> errors; (5) commit and push to the same branch. Don't open a PR unless I ask.
