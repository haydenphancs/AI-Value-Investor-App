# Live earnings results — handoff

Status as of 2026-11-13. This folder stays on the branch and is deleted before the branch is merged
to `main` (owner, 2026-11-05).

| File | What it is |
|---|---|
| `documents/handoff/LIVE_EARNINGS_HANDOFF.md` | **This file.** Where the work stands, the owner's decisions, what is verified and what is left. |
| `documents/handoff/LIVE_EARNINGS_REVIEW_FINDINGS.md` | The first review's 40 findings on the first commit, all fixed since. Its header says where the later reviews' results are. |
| `documents/handoff/live_earnings_review_raw.json` | The same review, raw. |
| `documents/handoff/LIVE_EARNINGS_PLAN.md` | The original plan, with **Built:** notes where the build differs: the design, and the UI and push wording one check against the code found. Not a line-by-line audit: the code decides what a note leaves out. |
| `documents/handoff/review-live-earnings.workflow.js` | The first review's workflow script. |
| `documents/System Design/SYSTEM_DESIGN_GUIDELINES.md` §11.8 | The design as built (also §7.1, §7.4, §11.2, §11.5, §11.6). The detail is in the module docstrings of `earnings_live_service`, `earnings_filing_service` and `earnings_live_sender`. |
| `documents/OWNER_TASKS.md` | §1 row 29 (the loop) and the "Live earnings results" item: migration, deploy, first-report-day checks, flags. |
| `backend/database/migrations/209_earnings_filing_cache.sql` | The restart table. Its header holds the VERIFY query and the steps after applying it. |

---

## 1. Where things stand

- **Branch** `claude/gifted-ramanujan-dpccwg`, worked in the worktree `.claude/worktrees/live-earnings`.
  The main folder holds other work in progress: never check out, stash or reset there.
- **Not pushed, no PR.** `origin` has only the first commit (`28a7438`) and the first handoff
  (`3028ac7`). Everything since is local: the fix rounds, the FMP cadence redesign, and `origin/main`
  merged up to `f9e1c985` (§8).
- **Committed on 2026-11-13.** Claude made two commits on the owner's go-ahead ("you can commit
  after you done"): first the work since the evening of 2026-11-11 (SEC's clock read as UTC,
  per-company SEC windows, the 16:00 ET pass following the live record, the verifier and
  live-service leftovers, the test-only items, the fixes for the 13 defects the first review of the
  whole change confirmed and the 20 the second one confirmed, then the filing verifier's four repair
  rounds of 2026-11-13 and a held push that is no longer buzzed with a figure its record has taken
  off, §7), then the merge of `origin/main`'s newer commits (§8). The owner pushes.
- **Backend:** built and tested (§5), not deployed. Both push flags ship off. Migration 209 is not
  applied; the owner applies it by hand (§8).
- **iOS:** builds green (§5), not released. 1.03 (build 14) was cut from `main` without this work:
  the Live Results row ships in the first App Store build that carries it.
- **Reviews:** the first review's 40 findings are fixed, and so is every defect the two reviews of
  the whole change confirmed (2026-11-11 and 2026-11-12). What is still open, and the accepted
  residuals, are in §7. The last repairs of the filing verifier and watcher, and a review of the
  held-push fix, finished on 2026-11-13.

## 2. What the owner asked for and decided

On INTC's report day (2026-10-29), the owner asked:

> "today we have earning report for INTEL. i want to add a feature. so the result will show right
> away on the second image too. we can add more on the card. like a compare results for estimate vs
> actual. so users can watch them. can we do a 'live' results? (we can mark a 'live' red color label
> at the card on the left (Upcoming section), so users can know this is live) what come out first
> will be shown right away. example, if Revenue come out, then show right away. we need to show red
> or green color for miss or beat also. check this for me if FMP can give us the earning report
> result. or anyway to implement this feature?"

The "second image" was the Earnings Alert detail sheet (Tracking → Alerts → Upcoming card).

**2026-10-29**
1. Data source: **"FMP + company filing (Recommended)"**.
2. Push: **"yes, push live. also, i need to have a toggle to let users turn on or off this
   notification also. check notification setting to see more"**.
3. Toggle: **"New 'Live Results' row (Recommended)"**, under Notification settings → Earnings, on by
   default; "Results & Surprises" stays; nobody gets two pushes for one report.
4. Extras on the sheet: **"Growth vs last year, Last 4 quarters, Guidance"**.

**2026-11-05**
- Restart data: "Add the table (Recommended)" = migration 209 `earnings_filing_cache`. Claude never
  applies migrations; the owner does.
- Night pushes: "Hold until 7 AM (Recommended)": a user with no Quiet Hours of their own gets
  `earnings_result` / `earnings_live` held 22:00-07:00 in their own time zone.
- Outlook text: "Company's own sentence (Recommended)": only a verbatim, verified quote from the
  release reaches users; no model-written words.
- First deploy: "Keep it off at first (Recommended)": EARNINGS_LIVE_RESULT_PUSH_ENABLED defaults False.
  EARNINGS_LIVE_PUSH_ENABLED also ships False.
- Plan defaults accepted the same day:
  - A result push never goes out for a report older than yesterday (the 16:00 pass's window); the
    card and the sheet still show it.
  - A Friday "awaiting" card stays until Monday noon.
  - "See live results" is hidden on older rows; the endpoint answers 400 for a report date before
    the first `sheet_days` day or after today + 14.
  - SEC budget: 25 tickers at a time (EARNINGS_LIVE_SEC_MAX_SYMBOLS), not 25 a day; one Gemini call
    per filing read.
  - No previous-day 8-K watcher; the restart table covers restarts.
  - The LIVE window is 06:00-22:00 ET on the report day.
  - Live Results is on by default, even for a user who turned off Results & Surprises; it shares
    the 4-a-day earnings cap once its flag is on.
  - Tickers past the 1,000-row watched limit get no live push (as on main), with a log warning.
  - `documents/handoff/` stays on the branch; it is removed before merging to main.
  - A day nobody has read answers upcoming/awaiting with a 30 s recheck, never "none" and never
    502; with EARNINGS_LIVE_ENABLED off the sheet returns estimates only, no recheck, and iOS hides
    the card.

**2026-11-11**
- A report FMP lists twice: "Follow the record (Recommended)": the 16:00 ET pass tells each report
  from the row the live record reads.
- A ~10x surprise only the filing's GAAP EPS backs: "Keep it quiet (Recommended)": told to nobody
  while the live result push is off.
- FMP cadence, the owner's design: for a report day with a watched report that still wants FMP,
  every 3 min inside a release window (06:00-09:35 / 15:55-18:30 ET) that one of that day's
  still-waiting watched companies uses (learned from its own past SEC 8-K Item 2.02 acceptance
  times; unknown = both), every 15 min otherwise, 06:00-22:00 ET; nothing when no watched report
  wants FMP or on a day with no watched report. The previous trading day's reports: the same, next
  trading day from 06:00 ET, morning window only. The tick refreshes the Tracking memo at 06:00 and
  15:30 ET on a trading day (plus a restart, an old held row, or an end it caused); feed builds
  still refresh on demand.
- Live earnings costs apply only to watched companies, only on their report day (and the next
  morning).
- Commits, that evening: "just don't commit and push, i will do this part."

## 3. What the sources can and cannot do (measured)

- **FMP** `/stable/earnings-calendar` rows carry `epsActual` / `revenueActual` beside the estimates
  (adjusted EPS, the consensus's own basis), but no time of day. One day is about 1,900 rows /
  0.32 MB, one call per day serves every company, and a call is cut at 4,000 rows. FMP fills the
  actuals anywhere from minutes to ~1–3 h after a release, and an after-close report can wait until
  the next morning. On 2026-11-12 it filled CSCO, QXO and KLC 3 to 19 min after their 8-Ks were
  accepted, and DDS not by 19:00 ET (§5). Rate limit 3,000/min; bandwidth matters more than the
  call count.
- **SEC EDGAR**: the company's own 8-K Item 2.02 press release (EX-99.1) is public within minutes.
  `acceptanceDateTime` is **UTC**, proved on 30 real filings on both sides of the 2026-11-01 clock
  change. The code reads it as UTC (`_parse_accepted`), and the log line
  `EARNINGS_LIVE_FILING … accepted=HH:MM ET` prints it in ET. EDGAR accepts nothing on weekends or
  federal holidays (2026-11-11, Veterans Day), even when the NYSE is open.

## 4. What is built

The design is SYSTEM_DESIGN_GUIDELINES §11.8; the detail is in the module docstrings. In short:

**Backend**
- `earnings_live_service`: one sticky record per (symbol, report date), merging FMP's row with the
  figures from the company's filing. Statuses `upcoming` (a later date, or the report day before
  06:00 ET while nothing is in), `live` (the report day, 06:00–22:00 ET, results incomplete, and
  before 06:00 once a figure is in: `live_status`), `reported`, and `awaiting` (never "late"; the
  card stays until 12:00 ET on the first trading day after the report). Guards against a
  dropped-digit EPS, an implausible revenue, a GAAP-only EPS (shown, never coloured), a surprise
  inside a figure's own rounding and FMP's zero-filled rows. FMP's duplicate and moved rows are
  decided per report. The live days (`live_days`: every calendar day from the previous trading day
  through today) hold the cards and the push sender's memory; a record stays readable one trading
  day longer (`sheet_days`), for the sheet and a held push's wake.
- Two loops under one task (`run_earnings_live_loop`), each every 60 s: the FMP tick
  (`earnings_live_tick`) and the filing tick (`earnings_filing_tick`), so a slow SEC or Gemini call
  never holds up FMP or the pushes.
- **FMP cadence** (`_fmp_cadence` and `due_days`, the owner's design above): a day is asked about
  only while a watched report on it still wants FMP (`needs_fmp`).
  - On the report day, a closed day included: every 3 min inside a release window one of that
    day's waiting companies uses (`release_windows`; both while unknown), every 15 min otherwise,
    06:00–22:00 ET. This report-day pace stops at 22:00 ET.
  - On the next trading day, a report dated on a trading day is asked again in the same hours:
    every 3 min in the morning window alone, whatever its company's windows, every 15 min otherwise.
  - Any other day before today is asked every 30 min, or every 2 h while today is a closed day, at
    any hour: a closed day's report on the days after its own, and every day before a closed
    today. So a Friday report FMP has not filled is read every 2 h from Saturday 00:00 ET.
- **The Tracking memo** (`tracking_service._cached_earnings_calendar`: today to +14 days, 30 min /
  6 h) keeps its window and its two speeds. The tick pays it a today-and-tomorrow round at 06:00
  and 15:30 ET on a trading day, and at any hour when it holds no row of today (a restart, or a
  closed day no round reached), when a held row is too old to open its record, or when its own
  fetches ended a watched report the held rows still list (`tick_refreshes_memo`). While no other
  day the memo holds lists anyone, as after a restart and about once a fortnight, that round reads
  the whole window instead.
- `earnings_filing_service`: finds the 8-K Item 2.02 (`find_results_filings`), reads EX-99.1 and
  asks the model for revenue, adjusted and GAAP EPS and the outlook, each with a quote. A figure is
  kept only when its quote is a verbatim span of the release that prints it, in dollars, at the
  metric's name, with its own sign (a sign the label's words leave open is refused,
  `_words_point_both_ways`); what is stored is the printed number. The outlook is the company's own
  verified sentence or nothing: never a piece of a sentence, a table's cells or headings, a quote
  that runs on past its full stop, or a sentence that only points at figures (`_outlook_problem`;
  a table of one column still passes: §7).
- **SEC watch** (`EarningsFilingWatcher`): each company is read every minute inside its own release
  windows (`release_windows`; both while unknown), every 15 min outside, 06:00–22:00 ET, never on
  weekends. At most 25 tickers hold a slot at a time (`EARNINGS_LIVE_SEC_MAX_SYMBOLS`),
  most-watched first, each keeping for the day the place it was first given, and a ticker that
  joins the day's list later going ahead of those the list ranks below it (`_placed`). Inside a
  release window its own company does not use, a ticker holds a slot only in the round its read is
  due (`_holds_a_slot`). No minute reads more than 25, however often the loop runs, and calls are
  1 s apart. A round works through its tickers one at a time. After a model call, once the round
  has run into the loop's next tick, it gives way to a ticker it already read that the next round
  would read again, but never when a ticker it would leave is not due in the next round
  (`_gives_way_for`); so in a burst of releases the most-watched are read every minute and the
  others wait their turn. Each read's model step is capped at its own timeout plus 15 s
  (`_model_deadline_seconds`). A filing gets at most six reads. 6-K filers, and tickers SEC's map
  lists under no company, stay FMP-only.
- **Restart table** `earnings_filing_cache` (migration 209): each verified filing is kept
  (`keep_filing`) and taken back once per process for `sheet_days` (`restore_filings`), so a deploy
  neither loses a figure nor reads the filing again. A row of another `FILING_VERIFIER_VERSION`
  reads as no row. Best effort: until the migration is applied, a restart behaves as before, with a
  WARNING.
- **Pushes** (`earnings_live_sender`): `earnings_live` the first time a watched report's figure
  lands, then `earnings_result` once FMP's adjusted EPS clears a 5% surprise (FMP numbers only).
  Both share one dedup key per report (`result_dedup_key`, and `neighbouring_result_keys` for a
  report FMP dates twice): at most one push per report. Neither starts for a report older than
  yesterday (`in_result_window`). With both flags off, as shipped, nothing is sent live, and the
  daily 16:00 ET pass tells results as before, now following the live record for a report FMP
  lists twice or whose EPS is 0 (`select_results`). When the pass cannot read who to tell for a
  ticker, it tells every other ticker and then fails (`WatcherLookupFailed`), so the day stays open
  and the next hourly wake tells the rest; the ledger stops anyone being told twice.
- **Night hold** (`night_hold_by_default` on both kinds): a landing between 22:00 and 07:00 on the
  clock of a user who never set Quiet Hours buzzes at 07:00, in the words the record gives at that
  wake (`copy_at_wake`), never a figure the record has taken off since (one gap, a restart: §7).
  `PushDispatchService.decide` and `flush_deferred` hold the rest: a hold
  past the user's midnight meets the cap of the day it goes out on, a failed settings read parks
  the row, a held row opened in the app ends unbuzzed (mark-all leaves it unread), and a live row
  turned off by its wake goes on as `earnings_result` (`instead_at_wake`) or is deleted, freeing
  the key. This reaches the existing 16:00 ET result push from the first deploy, flags off included.
  A flush that claimed a full batch and settled a row runs again after 1 s, not the 60 s interval,
  so the 07:00 wake drains faster: simulated, the last of 1,000 / 5,000 / 20,000 held rows goes out
  about 3 / 16 / 63 min after 07:00 (13 / 65 / 260 min with the 60 s wait).
- **Wire**: 11 optional `AlertResponse` keys; `TrackingFeedResponse.recent_earnings_alerts` (cards
  from before today, kept out of `alerts` so shipped builds never draw them);
  `GET /api/v1/tracking/earnings/{ticker}/live?report_date=` (signed in, `StandardRateLimit`);
  `poll_seconds` (`sheet_poll_seconds`: 30 s while live, 120 s while a preliminary figure can still
  be confirmed, 30 s while the day is not read yet, else none); `sent_at` on inbox rows.
- **Settings** (`config.py`): `EARNINGS_LIVE_ENABLED` (True), `EARNINGS_LIVE_FILINGS_ENABLED` (True;
  inert without `SEC_EDGAR_USER_AGENT`), `EARNINGS_LIVE_PUSH_ENABLED` (**False**),
  `EARNINGS_LIVE_RESULT_PUSH_ENABLED` (**False**), `EARNINGS_LIVE_INTERVAL_SECONDS` (60, 15–300),
  `EARNINGS_LIVE_SEC_MAX_SYMBOLS` (25, 0–50), `EARNINGS_LIVE_EXTRACT_MODEL` (gemini-2.5-flash).

**Costs**, walked minute by minute and pinned in the tests (figures from the module docstrings of
`earnings_live_service` and `earnings_filing_service`):
- FMP: 151 calls on a day of after-close reports with every company's windows known, 199 on a mixed
  day, 208 with no windows known, 288 on the worst day; 5 on a trading day with no watched report;
  1 a day on a weekend. On top of those:
  - reading the memo whole adds 13 (after a restart, and about once a fortnight);
  - a Friday report FMP never fills makes Saturday 12 calls and Sunday 13 (every 2 h), and adds
    121 on Monday;
  - a watched report dated on a closed day adds up to 163 on that day (the report-day pace);
  - rows too old to open a record add one round, 2 calls (midnight on a Monday with a watched
    report), and a blank first look up to 11, then the slow pace while FMP leaves the day blank;
  - a held push that asks again adds one read per look (`read_before_look`);
  - feed builds still pay for the memo's other rounds when someone looks.
- SEC, at the loop's 60 s: 53, 86, 253–286 and 409 reads on those four days. One company known to
  release after the close, filing at 16:05 ET, takes 51 submissions reads to find the release;
  each read of a filing adds two document reads and one Gemini call. The ceiling is 10,225
  submissions reads a day (25 × 409 rounds; no minute reads more than 25). A busy day spends more
  of it now that a ticker gives up its slot inside a window its own company does not use: a
  simulated peak day takes 3,651 submissions reads and gets all 30 of its morning reporters an SEC
  figure, the slowest 3 min after its release, against 2,209 reads and 8 of them with no SEC
  figure when such a ticker kept its slot.
- The Tracking tab with a LIVE card on screen, outside the hours a price can move: one read of the
  card's route per 30 s, from the shared memo and the live record, with no FMP call once the memo
  and that report's extras (6 h) are warm
  (`test_asking_a_live_cards_route_again_costs_no_fmp_call`), plus one feed build when a report
  moves under a LIVE card.

**iOS** (`frontend/ios/ios/`): new `EarningsLiveModels`, `EarningsLiveViewModel`, `LiveBadge`,
`EarningsResultChips`, `EarningsLiveResultsCard`, `EarningsLiveScreen`. The Upcoming card shows LIVE
and result chips. The alert sheet opens on the results card, and its header follows the card's
latest answer. Each past quarter's caption leads with its verdict ("Beat +7.3%", "Miss -26.8%",
"In line"), and VoiceOver reads it as, for example, "Q3 '25: missed by 26.8%". The sheet polls as
`poll_seconds` asks, retries a failed first load (bounded) and stops at once on a 400 or 404. "See
live results" leads a result row back to the first `sheet_days` day
(`MarketHoursUtil.liveResultsFloorKey`). The Alerts tab hides a result row only behind the same
report's card, while that card is LIVE or shows its final result. The Tracking tab's 30 s poll
builds the whole feed only while a price on the tab can move
(`MarketHoursUtil.shouldRefreshPrices`). Otherwise, while a LIVE card is on screen, it asks each
LIVE card's own route (`refreshLiveEarningsCards`, at most 8 a round, taking turns): an answer
that settles that card's report is written onto the card, one the server could not settle yet
leaves it LIVE, and a moved or unlisted report, or a 404 or 400, hands the round to one feed
build. The inbox reads `sent_at` and says "held back" on a held row.
Settings → Notifications → Earnings → "Live Results", on by default, synced through
`SettingsSyncManager.boolKeys`.

## 5. What is verified, and how

- **Backend tests.** The 38-file targeted list (§9) passed on the merged tree, every fix in: 12,143
  tests, exit 0, no blocked network call (2026-11-13). Each code fix came with a test that failed
  before it, except the FMP cadence redesign, written together with its tests and cost walks; the
  test-only items and the new source-scan guards were checked by hand mutation. The full suite
  passed on one export of the merged tree, the Swift-harness files first under the build slot: every
  test passed, exit 0, no blocked network call (2026-11-13; the counts and the tree ids are in
  Claude's final report).
- **iOS.** The canonical build passed on 2026-11-05, on 2026-11-11 (22:37 MT, the merged lanes, no
  warnings in the feature's files), on 2026-11-12 (13:25 MT, with the iOS fixes, the branch's last
  Swift change) and on 2026-11-13 with `origin/main`'s newer commits merged in (they change Swift).
  There is no XCTest target: the Swift behaviour is pinned by source-scan guards
  (`tests/test_ios_earnings_live_guards.py` and others).
- **Real releases.** 30 real Q3 2026 EX-99.1 releases were replayed twice through the real model and
  verifier. The second replay (2026-11-11, the current prompt): revenue verified on 23 of 29 and EPS
  on 28 of 29, every one correct; the outlook shown for 11, one of them ETSY's flattened table row,
  and every other non-outlook refused. The remaining refusals are the safe kind (a scale header not
  proven unique in the release, foreign currencies named in it, a label on the row above, BAC's
  footnote digit). The same stored answers through the verifier as staged on 2026-11-12 (no new
  model call) give the same verdicts and figures, except ETSY's table row, now refused as
  `table_cell`: the outlook shows for 10 of the 29. The first replay had read XOM's earnings as
  revenue; that is refused now. The 2026-11-13 verifier rounds change none of these: over all 34
  stored releases (those 30 and 2026-11-12's four), no sentence's verdict changed.
- **Shadow runs** (2026-11-12): the real loops against live FMP, SEC and Gemini, pushes recorded
  instead of sent, no database. Both ran that morning's code, before the second review's fixes.
  - Morning, 09:27–12:00 ET, 17 reports (12 of the day, 5 of the day before): no failed call, no
    landing (the morning releases were already out). The day before was Veterans Day, with EDGAR
    closed, and its reports came from FMP alone. Six foreign filers were recognised from their SEC
    records and rested. Cost: 32 FMP calls, 75 SEC submissions reads, no document read or model
    call.
  - Evening, 15:30–19:00 ET, 12 of the day's reports. Release windows learned from SEC history:
    after the close for CSCO, AMAT, DDS, KLC and ANDG, both for the rest.
    - CSCO: 8-K accepted 16:06:24 ET, read 16:09: revenue $18.5B verified, both EPS refused (not
      verbatim), the outlook kept. FMP's EPS at 16:10.
    - KLC: accepted 16:15:14, read 16:16: EPS −$0.03, revenue $665.3M and its outlook sentence.
      FMP at 16:34.
    - QXO: accepted 16:15:43, read 16:17: EPS $0.13, revenue $4.895B. FMP at 16:19.
    - DDS: accepted 16:39, read 16:40: GAAP EPS $8.38 kept; revenue refused (`wrong_metric`) and the
      outlook (`lead_in`). FMP had not filled it by 19:00.
    - AMAT and ANDG came from FMP alone at 16:04, FLO at 16:10.
    - Cost over the 3.5 h: 74 FMP calls, 630 SEC submissions reads, 8 document reads, 4 model
      calls, no failed call.
    - The harness's push recorder failed from the first landing on (it called a helper the code had
      renamed), so only AMAT's live copy was recorded: "AMAT results are out", "Revenue beat
      consensus by 1% · EPS beat consensus by 2%." The loop logged each failed dispatch at WARNING
      and ran on.
- **UI checks** (2026-11-12, Simulator, the 13:25 MT build against a local backend from this
  branch as it stood then, the owner's account, with the owner's approval):
  - First, with no watched report that day: every request answered 200, nothing failed to decode,
    an upcoming earnings card showed its estimates, inbox rows from before the feature (no report
    date) offered no "See live results", and the largest accessibility text size rendered.
  - Then, with CSCO, AMAT and NU added by the owner: Tracking → Alerts showed CSCO's and AMAT's
    result cards with FMP's figures and beat chips minutes after their after-close releases, and
    NU's card LIVE with "Waiting for results…". CSCO's sheet showed the results card (revenue $18.5B
    against $18.1B, EPS $1.40 against $1.33, the year-ago block, the last 4 quarters, the FMP
    footnote); NU's showed the LIVE "Checking for results" state. The largest accessibility text
    size rendered.
  - Not seen: the Preliminary (filing) state in the app, since a local server runs no SEC loop, and
    light mode (an account-synced setting, left unchanged).
- **Foreign reporters** (measured on FMP, 2026-11-12): for TSM, ASML, NVO, SAP, SONY, TM and BABA,
  FMP's per-symbol earnings history (`/stable/earnings`, which feeds the Financials tab's quarters
  and so the sheet's year-ago and last-quarters blocks) holds actuals and estimates on a US-dollar
  scale and on one basis. Their live actual, estimate and verdict on the sheet come from FMP's
  calendar rows (`/stable/earnings-calendar`; a 6-K filer has no filing figures), which were not
  read for them: those are assumed to match the history (§6). Not proven for every foreign
  reporter (§7).
- **Measured live** (§3): FMP's row count, size and missing time of day; SEC's UTC clock.
- **Reviews.** The first review's 40 findings, a full check of the branch on 2026-11-05, a review of
  the whole change on 2026-11-11 (13 defects confirmed, none refuted) and a second one on 2026-11-12
  (20 confirmed: 2 high, 9 medium, 9 low; 4 refuted) were all worked through, each fix reviewed
  again before it was merged. The later reviews' full results, each finding with its trace and its
  fix, are kept outside git, in the folder named in `LIVE_EARNINGS_REVIEW_FINDINGS.md`'s header.
  On 2026-11-13 four more verifier rounds each had their own review and recheck, and the held-push
  fix had a review of its own (6 low gaps confirmed and fixed). What the last rechecks left is in §7.

## 6. What is NOT verified

Only a real report day after the deploy can prove: FMP's fill lag and row order, Railway timing
from 8-K to figure, one push per report in production, the Preliminary-to-FMP flip on a device,
and the SEC limits shared with the 18:30 ET stake finder. The evening shadow run (§5) measured the
fill lag and the 8-K-to-figure timing for one evening, from this Mac, not from Railway.

Not done when this was written:
- a live run of the second review's fixes and of the 2026-11-13 repairs: both shadow runs ran the
  code of 2026-11-12's morning, so those rest on the tests and the stored-answer replay;
- what a live run would push: the shadow run's recorder failed from its first landing on, so the
  push decisions and their words rest on the tests;
- the Preliminary (filing) state in the app, and light mode (§5, UI checks);
- one FMP `/stable/earnings-calendar` read of a day a foreign reporter reported on, to confirm that
  the calendar rows the sheet's live figures come from match the measured history (§5); and every
  foreign reporter's currency on the sheet (§7).

## 7. Open items and recorded residuals

**Open** (known):
- Filing verifier, what its last recheck left (2026-11-13, after the fourth repair round). The
  counts are rows of synthetic sweeps; none of these shapes is in the 34 stored releases. A kept
  figure shows as Preliminary until FMP's replaces it, and could be stated in a live push once that
  push is on. Each round closed the spellings its recheck named, and each recheck found others: a
  word list cannot tell the release's own quarter from another. The fix that closes the class is
  to thread the answer's `fiscal_period` into the reading.
  - medium: another quarter's or period's figure can verify as GAAP EPS through the newer holder
    spellings when the period stands where the fourth round does not look: behind an ordinal and a
    possessive ("…, and in the second quarter the company's net income was $250 million, or $1.00
    per diluted share."), after the figure's own words ("Results, including net income
    attributable to the Company of $45 million ($0.12 per share), exceeded expectations for the
    fourth quarter."; "…, and both were records for the second quarter."), or at the start of the
    sentence ("A year ago, …, and net income attributable to the Company was $240 million, or
    $0.96 per diluted share."; also "For the full year", "In fiscal 2025", "For the quarter ended
    June 30"). The class was already kept in the older spellings ("…, and in the second quarter
    our net income was …"). The fourth round closed an ordinal beside those spellings in front of
    the figure, in its subject and in its own words.
  - medium: an "earnings results" front with another quarter right behind the figure or after one
    of the results' verbs ("Earnings results, including a net loss of $45 million ($0.12 per
    share), exceeded expectations for the fourth quarter."), and a bare "results" front with a
    total in the newer holder spellings whose sentence makes it a sold unit's or another period's
    ("Results, including net income attributable to the Company of $45 million ($0.12 per share),
    are for the prior-year quarter."). The fourth round holds an "earnings results" total only
    behind the results' own verbs or their own "included", with nothing in front of the front;
    bare "results" was outside its scope. Fix: the same check for every results front with those
    spellings, read to the end of the sentence.
  - low: "earnings results" whose next sentence makes them a unit's ("… These results are
    reported in discontinued operations."); the bare "Results" twin was already kept.
  - low: a one-word name that may be a unit's reads as the company's ("attributable to Mobileye
    common shareholders"); closing it needs the company's name threaded in.
  - low, honest figures refused (FMP fills them): any ordinal quarter beside the newer holder
    spellings, the release's own "third quarter" included ("…, and net income attributable to the
    Company was $300 million, or $1.20 per diluted share, in the third quarter."), since the
    reading cannot tell which quarter is the release's; "earnings results" with a clause in front
    ("Revenue rose 5%, and earnings results, …"), as a fragment or a cut quote, closed by ";", or
    followed by "were restated"; "in the third quarter" in a later clause, a holder name longer
    than one word and its legal form ("Duke Energy Corporation"), a lead-in's earlier period with
    no comma before it, rare tails (", respectively"), and "earnings results … included a net
    loss" (every version).
  - low, kept by every version: an item's per-share figure behind "totaling" or "amounting to"
    with no direction word in front ("reflecting income from discontinued operations totaling
    $0.05 per share"); a figure of the other basis ("Adjusted EPS was $1.35, reflecting a net loss
    totaling $0.05 per share" keeps GAAP −$0.05); the adjusted twin of the item rule ("recorded an
    adjusted loss of $20 million, or $0.05 per share, on the sale"); bare participle arrivals
    ("the litigation charge settled at $0.05 per share"); other-period labels ("For the full year,
    diluted EPS was $4.80."); an ordinal "results" front with an older total ("Fourth-quarter
    results, including …"); a comparison print ("Net income was $300 million, or $1.20 per diluted
    share, versus diluted EPS of $1.00." keeps $1.00); an excluded item behind "Results,
    excluding" ("Results, excluding a net loss of $45 million ($0.12 per share), exceeded
    expectations." keeps adjusted −$0.12).
  - Under a "Low High" guide table above the quote, the fourth round keeps a few quotes the tree
    before it refused (118 of 180,000 fuzz rows): two equal prints, the first now refused as
    another quarter's and the second read instead. That tree refused them only because it read the
    first print, which the guide takes back when words follow it; without the guide it keeps every
    one of those quotes, so nothing is kept that it did not keep elsewhere.
  - Quotes of 1,000 characters and more built to be slow read 3 to 4.5 times slower since the
    fourth round; the 400-character quote cap bounds it, and the suite's timing tests pass.
- To decide: `live_status` shows LIVE before 06:00 ET on the report day once a figure is in
  (`test_an_early_filing_before_six_makes_the_card_live_not_upcoming`), outside the accepted
  default "The LIVE window is 06:00-22:00 ET on the report day".
- To decide: the SEC round reads one filing at a time, so one slow model call holds back every
  company behind it. Simulated with 0.3 s per SEC call and the 1 s spacing: 25 new filings take
  about 4 min with a 6 s model, about 8 min with a 15 s one, and about 10 min when a fifth of the
  calls stall for 90 s (the last figure then lands about 15 min after its release). Running a few
  filings' model steps at once, the SEC calls kept one at a time, would cut that; it is a design
  change, and it puts more load on the shared Gemini quota breaker.

**Recorded residuals** (accepted; reopen only with harm beyond the description):
- A leftover FMP row dated on a trading day; on D+1 a held figure with no filing figure gets no
  sheet poll.
- A stale row FMP leaves beside a new date shows LIVE and two cards until a real pair is captured.
- A failed claim or device read uses up an `earnings_live` dispatch.
- A short answer on a near memo round can drop today's card for up to 30 min.
- A sheet open across two trading days keeps its last LIVE status after a refusal; on a `none`
  answer the bottom rows keep the tap's values.
- BAC glues a footnote digit to a figure ("$1.082(B)"), so it waits for FMP.
- The verifier's word lists cannot be complete: an unknown verb refuses an honest figure, a listed
  verb inside a modifier (with no "that" or "which" in front) still reads as a verb, and "EPS rose
  as losses narrowed to $0.46" still reads as a loss.
- Outlook: a table of one column reads as a line of a list. CSCO's outlook on 2026-11-12 (a
  lead-in, a one-column guidance table and a sentence, every figure verbatim and of one quarter)
  verified, and the sheet shows it as one run-on line. A heading in front of the sentence stays
  ("Business Outlook Acme expects …"), and so does a table row the sentence leads into with ":"
  and no table heading ("as follows: Revenue $15.0 - $16.0").
- Fills FMP makes after 22:00 ET for a report dated on a trading day land at the next 06:00 ET
  read when the next day is a trading day. Otherwise they land at the first read from 00:00 ET:
  every 2 h when the next day is closed (Saturday 00:00 ET for a Friday report), every 30 min when
  it is a trading day (Monday 00:00 ET for a report FMP dates on a Sunday). The night hold applies
  either way.
- A report wrongly ended by 10+ min of short FMP answers stays ended until the next answer that
  lists it.
- If SEC ever changed its stamp format, the unreadable-stamp WARNING would repeat each round.
- Night hold and inbox: a failed read in ET's daytime meets the cap counted on the ET day before it
  is parked; a settings outage over the next flush too sends ~1 min late on ET's clock; a figure
  that moves after the user read a held row is not told; mark-all-read in the same second a held
  row is pending stamps it; the 21:59 ET edge of a failed-read park.
- Builds 1.02 and 1.03 read only `createdAt`: a held row shows its landing time, and "held during
  quiet hours" even for a user who never set Quiet Hours.
- A 404 carrying a structured error code is not mapped by `AppError`, so the sheet retries it
  20 × 30 s.
- A device clock seconds behind ET around midnight can offer a date the server refuses (the sheet
  shows the refusal).
- An FMP-only report loses its record on a restart once its day has left the live days.
- One FMP answer listing a report twice, a zero-EPS row with a consensus beside a real-EPS row with
  none: the EPS agrees in every order, but the estimate and the verdict depend on whether the
  company's release came first.
- A held push whose figure its record has taken off since the landing (an FMP zero its row of
  zeros showed unfilled) is not buzzed at the wake, but a restart between the landing and the
  wake still lets it out in its landing's words: the new process never held that figure, so its
  record says nothing against the row. A row a wake has already held waits through a restart.
- A lone FMP zero that replaces a real FMP figure the record already showed, then FMP's row of
  zeros: the record drops both and shows no EPS (the real figure is not restored), and a held row
  stating that real figure ends unsent. It needs FMP to send a real figure, then that field as 0
  alone, then zeros.
- A held row that ends unsent at the flush's bound notes "suppressed on flush: a figure is still
  being confirmed" for every wait, a figure the record took off included (log wording only).
- Loops set under 60 s (production runs 60 s): the minute's count can cost a release its window's
  last minute read, and once more than half the slots are in their windows a company in its
  windows is read about half as often at 55–59 s, and about three quarters as often at 19, 28 or
  29 s.
- Foreign reporters: the sheet takes FMP's figures as US dollars. A reporter whose FMP calendar
  rows are in its own currency would show them with "$", as the Financials tab already does.
- A rollback to code from before this feature, once `EARNINGS_LIVE_PUSH_ENABLED` is on, ends held
  `earnings_live` rows unsent: that code does not know the kind and marks each one failed
  (`unknown kind 'earnings_live'`). The row keeps the report's key, so `main`'s 16:00 ET pass skips
  those users for that report: they get no result push either. Held Results & Surprises rows still
  go out at their wake, in the words the row holds then; one a wake had held for FMP's answer goes
  out in that wake's words, which can state the release's figures.

## 8. Next steps

Claude, done on 2026-11-13:

1. The last verifier and watcher repairs landed; the targeted list (§9) and the replay of the
   stored answers passed again.
2. The filing verifier's fourth repair round (§7) was staged on top, through the same checks.
3. The gate on the staged tree: the full suite on one export of the exact tree (§5), with no iOS
   build needed, as no Swift changed after the last green one. Then committed, on the owner's
   go-ahead ("you can commit after you done").
4. `origin/main`'s newer commits merged on top and committed: `3be74338..f9e1c985`, the Holdings
   after-hours tail, a news retry and an OWNER_TASKS note. They change 5 files this branch changes
   too (`tracking_service.py`, `schemas/tracking.py`, `test_tracking_feed_schema_parity.py`,
   `TrackingModels.swift`, `OWNER_TASKS.md`), and the merge had no conflict. The full suite on one
   export of the merged tree and one canonical iOS build passed before the merge commit. Local
   `main`'s unpushed `1eaf9740` (Updates web-only cards) is not in it: it comes with the next merge
   once it is on `origin`.

The owner, in this order. The list to follow is OWNER_TASKS → "Live earnings results": it holds
every condition, the log lines and the queries. In short:

5. Push the branch.
6. Before the deploy, apply `backend/database/migrations/209_earnings_filing_cache.sql` by hand in
   Supabase Studio and run the VERIFY query in its header (one row: `t | f | f | t`). Then ask
   Claude for the bookkeeping its header lists (schema re-dump, test lists, atlas).
7. Decide the PR. Before the merge, ask Claude to remove `documents/handoff/` from the branch in its
   own commit.
8. Merge into `main` only once 1.03 is approved in App Store Connect (every archive of `main` made
   after the merge carries this work, so a second 1.03 build after a rejection would ship it), and
   outside 15:55–18:30 ET on a report day (and 06:00–09:35 ET on a day a watched company reports
   before the open). Railway deploys `main`, so the merge is the deploy, and every later redeploy
   keeps the same windows.
9. On the first report day, read the logs OWNER_TASKS lists, and from about 17:00 ET, once the
   16:00 ET pass has run, run its one-push check.
10. Only then set `EARNINGS_LIVE_RESULT_PUSH_ENABLED=true` on the Railway web service and redeploy,
    outside those windows (after 18:30 ET on a report day): the setting takes effect only with the
    redeploy. Run the check again on the next report day.
11. Ship the first App Store build that carries the Live Results row; before submitting, check its
    TestFlight build on a report day.
12. Only once that build is widely installed, set `EARNINGS_LIVE_PUSH_ENABLED=true` and redeploy,
    outside the same windows.

## 9. Commands

Tests, from the worktree's `backend/` with the main folder's venv (a worktree has none). A run is
green only at exit 0: the conftest blocks the network and exits 1 on any blocked call, even when
every test passed. None of these files runs the Swift toolchain.

```bash
MAIN=/Users/haiphan/BIGDATA/myApp/AI-Value-Investor-App
cd "$MAIN/.claude/worktrees/live-earnings/backend" && "$MAIN/backend/venv/bin/python" -m pytest -q --no-header -p no:cacheprovider \
  tests/test_earnings_live_status.py tests/test_earnings_live_service.py tests/test_earnings_live_push.py \
  tests/test_earnings_filing_service.py tests/test_earnings_filing_real_shapes.py tests/test_filing_text_utils.py \
  tests/test_tracking_earnings_live_endpoint.py tests/test_ios_earnings_live_guards.py tests/test_notification_senders.py \
  tests/test_notification_dispatch.py tests/test_notification_kinds.py tests/test_earnings_sender_per_day_calendar.py \
  tests/test_tracking_feed_schema_parity.py tests/test_tracking_earnings_calendar_cache.py tests/test_tracking_feed_inflight.py \
  tests/test_lifespan_local_notification_jobs.py tests/test_inflight_cancellation_safety.py tests/test_settings_attribute_parity.py \
  tests/test_patch_targets_exist.py tests/test_identity_rule_coverage.py tests/test_table_grants_service_role_only.py \
  tests/test_ios_alerts_badge_and_filters.py tests/test_ios_push_tap_opens_detail.py tests/test_push_dispatch.py \
  tests/test_push_preference_typing.py tests/test_push_audience_cap_after_preferences.py tests/test_quiet_hours.py \
  tests/test_quiet_hours_flush_shutdown.py tests/test_notification_inbox_cursor.py tests/test_ios_alerts_tab_consolidation.py \
  tests/test_ios_notification_funnel.py tests/test_ios_settings_push_paths.py tests/test_ios_notification_category_parity.py \
  tests/test_ios_alerts_gate_from_refusal.py tests/test_ios_market_times_eastern.py tests/test_notification_read_and_flush.py \
  tests/test_push_flush_badge_and_shutdown.py tests/test_env_example_boots.py
```

In a worktree, `tests/test_system_design_doc_parity.py::test_every_path_the_doc_names_resolves`
fails only because four gitignored inputs the doc names are absent there.

From the worktree root: `bash frontend/ios/scripts/theme-lint.sh`. iOS builds: only the canonical
command in CLAUDE.md, under its Machine safety rules.
