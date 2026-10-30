export const meta = {
  name: 'review-live-earnings',
  description: 'Exhaustive review of the live earnings change: 7 dimension finders, 3-lens adversarial verification, completeness critic',
  phases: [
    { title: 'Find', detail: 'one reviewer per dimension of commit 28a7438' },
    { title: 'Verify', detail: '3 independent lenses per finding; survives on 2 of 3' },
    { title: 'Critic', detail: 'what did the dimensions miss?' },
    { title: 'Verify missed', detail: '3-lens panel on the critic findings' },
  ],
}

// Review workflow used in the cloud session (2026-10-30) on commit 28a7438.
// To re-run locally: set REPO / SP below to your paths (SP = any scratch folder; the
// tree-sitter syntax checker it mentions only existed in the cloud container — drop those
// lines or install tree-sitter + tree-sitter-swift), update RANGE to the diff you want reviewed,
// then pass this file to the Workflow tool ({scriptPath: ...}).

const REPO = '/home/user/AI-Value-Investor-App'
const SP = '/tmp/claude-0/-home-user-AI-Value-Investor-App/adccd9bd-8f87-5910-a257-aea64010ffdf/scratchpad'
const RANGE = '84788ba..28a7438'

const PREAMBLE = [
  'You are reviewing ONE git commit in the repo at ' + REPO + ' (Caydex: a SwiftUI iOS app under frontend/ios/ios and a Python 3.11 FastAPI backend under backend/app). Commit range: ' + RANGE + ' (run: git -C ' + REPO + ' diff ' + RANGE + ' -- <paths>, and read the full files, not only the hunks).',
  '',
  'WHAT THE COMMIT DOES ("live earnings results", owner request 2026-10-29): On an earnings report day, the Tracking -> Alerts -> Upcoming earnings card shows a red LIVE badge; its detail sheet shows estimate vs actual for EPS and revenue the moment EACH lands (green beat / red miss + surprise %), plus the same quarter last year, the last 4 quarters and the company guidance; and a push goes out when results land, behind a new "Live Results" notification toggle (on by default). Owner decisions: FMP data + a company-filing fast path (SEC 8-K Item 2.02 press release EX-99.1, read with Gemini, every figure verified verbatim against the release text); live push with its own toggle; "Results & Surprises" (existing earnings_result kind, EPS surprise >= 5%) also sent live; nobody gets two pushes for one report (shared dedup key earnings_result:{SYM}:{YYYY-MM-DD}); the new push ships behind EARNINGS_LIVE_PUSH_ENABLED=False until the app version with the toggle is released. Design intent: "documents/System Design/SYSTEM_DESIGN_GUIDELINES.md" section "### 11.8 Live earnings results", and documents/OWNER_TASKS.md (row 29 and the "Live earnings results" item in 2.1).',
  '',
  'KEY FILES: backend/app/services/earnings_live_service.py (records, statuses, guards, 2-day memo, hot gate, loop tick, history extras, payload), backend/app/services/earnings_filing_service.py (SEC fast path + verification), backend/app/services/notification_senders/earnings_live_sender.py (push), backend/app/services/notification_senders/earnings_sender.py (daily sender changes), backend/app/services/notification_kinds.py, backend/app/services/tracking_service.py (feed integration, get_earnings_live), backend/app/schemas/tracking.py, backend/app/api/v1/endpoints/tracking.py (GET /tracking/earnings/{ticker}/live), backend/app/config.py (EARNINGS_LIVE_* flags), backend/app/main.py (loop spawn). iOS (under frontend/ios/ios): Models/EarningsLiveModels.swift, Models/TrackingModels.swift, Models/AlertDestination.swift, ViewModels/EarningsLiveViewModel.swift, ViewModels/TrackingViewModel.swift, ViewModels/NotificationSettingsViewModel.swift, Views/Atoms/LiveBadge.swift, Views/Molecules/ActivityRow.swift, Views/Molecules/AlertCardView.swift, Views/Molecules/EarningsResultChips.swift, Views/Organisms/EarningsLiveResultsCard.swift, Views/Organisms/AlertsTabContent.swift, Views/Screens/AlertDetailView.swift, Views/Screens/EarningsLiveScreen.swift, Views/Modifiers/AlertDestinationCover.swift, Core/Services/APIEndpoint.swift, Core/Services/SettingsSyncManager.swift, Views/Screens/NotificationsSettingsView.swift. Tests: backend/tests/test_earnings_live_*.py, test_earnings_filing_service.py, test_tracking_earnings_live_endpoint.py, test_ios_earnings_live_guards.py (iOS source scans).',
  '',
  'ENVIRONMENT: Linux container, no network to FMP/SEC/Gemini. Backend tests: cd ' + REPO + '/backend && ./venv/bin/python -m pytest -q <files> (conftest blocks network; do NOT run the whole suite, it takes 20 min). There is NO Swift compiler; a tree-sitter syntax checker exists: ' + SP + '/tsenv/bin/python ' + SP + '/tools/swift_syntax_check.py <files> (syntax only, no type checking). The iOS project uses SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor, SWIFT_APPROACHABLE_CONCURRENCY = YES, Swift language mode 5, iOS 18 target, file-system-synchronized groups (new files are auto-included). DO NOT modify any file in the repository. You may write scratch scripts ONLY under ' + SP + '/review/ (mkdir -p it) to reproduce behaviour.',
  '',
  'WHAT COUNTS AS A FINDING: a concrete defect introduced or exposed by this commit — wrong behaviour, a crash, a compile error, a race, a data-correctness bug, a broken contract with shipped app builds, a claim in the docs/tests that is false, a security/privacy problem, a performance/cost problem that contradicts the stated budget. Each finding needs a specific file and line, and a concrete failure scenario (inputs/state -> wrong output). Do NOT report style nits, speculative "could be cleaner" refactors, or things the commit deliberately decided (e.g. the push flag shipping off, FMP-only for earnings_result, the LIVE window 06:00-22:00 ET) unless the implementation contradicts that decision. Prefer fewer, real findings over many weak ones; but be thorough — read callers and callees to confirm.',
].join('\n')

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          line: { type: 'number' },
          title: { type: 'string' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          description: { type: 'string' },
          failure_scenario: { type: 'string' },
          suggested_fix: { type: 'string' },
        },
        required: ['file', 'line', 'title', 'severity', 'description', 'failure_scenario', 'suggested_fix'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT = {
  type: 'object',
  properties: {
    real: { type: 'boolean' },
    severity: { type: 'string', enum: ['high', 'medium', 'low', 'none'] },
    reasoning: { type: 'string' },
    evidence: { type: 'string' },
  },
  required: ['real', 'severity', 'reasoning', 'evidence'],
}

const DIMENSIONS = [
  {
    key: 'live-core',
    focus: 'backend/app/services/earnings_live_service.py end to end: LiveMetric/eps_metric/revenue_metric guards (eps_digit_shift_suspect usage, revenue plausibility, GAAP basis, MIN_ABS_EPS_ESTIMATE), live_status / recent_card_visible windows (ET conversion, before 06:00, after 22:00, weekends, holidays, DST, previous_trading_day semantics, a report dated on a non-trading day), sticky folding in observe_rows/_fold (fetched flag, checked_at, revenue 0, duplicate rows), landing detection (_note_landing, seen_pending, landed_from_pending, absorb_filing), due_days/_is_hot gate and costs, refresh/_fetch/_run_once (leader/joiner, cancellation, failure wait, pruning across the ET day roll), history_extras (shielded task, timeouts, cache TTLs, _consume_result), history_extras_from (current-quarter detection, year-ago matching, last 4), live_payload, earnings_live_tick (ordering of steps, tracking memo read, watched cache _watched_ranked_cached, filing watcher invocation, dispatch), run_earnings_live_loop. Also check every caller in tracking_service.py and the tests in backend/tests/test_earnings_live_status.py and test_earnings_live_service.py for wrong assumptions.',
  },
  {
    key: 'filing',
    focus: 'backend/app/services/earnings_filing_service.py: find_results_filings (submissions JSON parallel arrays, items parsing, filingDate window, accession normalisation), _parse_accepted (the acceptanceDateTime timezone claim), _IndexTableParser/find_ex99_document (real EDGAR index page HTML shapes: tableFile rows, iXBRL /ix?doc= links, document names, EX-99.1 vs EX-99.01 vs EX-99), the number regexes (_NUMBER_IN_QUOTE_RE, unit words incl. single letters k/m/b, negative detection via parentheses/minus, commas, decimals, ranges), revenue_from_quote/eps_from_quote/_guidance_numbers_grounded/verify_extraction (can a hallucinated or wrong-scale figure survive? can a correct figure be wrongly refused in common press-release phrasing like "$13.7 billion", "$13,653 million", "(0.46)", "loss per share of $0.46", "$0.23 per share"?), interaction with app/utils/filing_text.verify_peg_span (40-400 chars, digit rules, normalisation) and html_to_text (signature, max_chars, log_tag rules), the GeminiClient.generate_json call (read app/integrations/gemini.py: parameter names, return wrapper, response_schema format accepted, temperature/cache/thinking_budget), the SEC client calls (read app/integrations/sec_edgar.py: get_submissions, get_filing_document document-name regex, exceptions and retry_after, user_agent_configured), ticker_cik_map/cik_for usage (app/services/learn_weekly/signals.py), EarningsFilingWatcher.run_once cadence/budget/spacing/parking/retry semantics, and the tests in backend/tests/test_earnings_filing_service.py.',
  },
  {
    key: 'push',
    focus: 'the push path: backend/app/services/notification_senders/earnings_live_sender.py (dispatch_landings ordering live-then-result, once-per-process sets and _prune, flags, MAX_SENDS_PER_ROUND, copy: live_copy/_phrase/_money/_vs_consensus incl. the FMP lock-screen licence rule), the daily sender changes in earnings_sender.py (result_dedup_key equality with the old key format, _watched_tickers_ranked, the eps_digit_shift_suspect guard in select_results, ticker_route(report_date=...)), notification_kinds.py (new KIND_EARNINGS_LIVE registry entry, ticker_route report_date), and how app/services/push_dispatch_service.py notify_watchers/notify_users actually claim, defer (quiet hours), cap and expire — verify the claim "a user with Live Results on gets only the live push and is skipped by the earnings_result dispatch; a user with Live Results off but Results & Surprises on still gets the surprise; the daily sender finds the key taken". Check the category cap interplay, quiet-hours deferred rows, collapse ids, and what the iOS app does with the new kind (frontend/ios/ios/Models/AlertDestination.swift tabDestination/destinations, Core/AppDelegate.swift categories, ActivityFilter buckets, NotificationRouter) and the new route key report_date. Also check landing semantics from earnings_live_service.landed_snapshots that feed this sender (could a push go out for stale results, twice, or never?).',
  },
  {
    key: 'feed-api',
    focus: 'the feed and endpoint integration: backend/app/services/tracking_service.py (_get_earnings_alerts changes: today computation, observe_rows on every feed build, card_fields for today/future rows; _get_recent_earnings_alerts and _recent_earnings_alert; the asyncio.gather position of the new task and section_names; TrackingFeedResponse construction; get_earnings_live report-date choice, requested-date validation, found/none logic, history_extras call, latency), backend/app/schemas/tracking.py (new AlertResponse fields, TrackingFeedResponse.recent_earnings_alerts, EarningsLive* models), backend/app/api/v1/endpoints/tracking.py (ticker regex, report_date parsing, error responses and status codes, rate limit, auth dependency, response_model serialisation), and compatibility with SHIPPED iOS builds that decode AlertDTO/TrackingFeedResponse WITHOUT the new keys (frontend/ios/ios/Models/TrackingModels.swift at the parent commit 84788ba — use git show 84788ba:<path>): could an old build render something wrong or fail to decode? Also check the per-user 30 s feed cache and the 30-min Tracking calendar memo interplay with live freshness, NaN/inf safety on the wire (Starlette allow_nan=False), and the tests backend/tests/test_tracking_earnings_live_endpoint.py / test_tracking_feed_schema_parity.py.',
  },
  {
    key: 'ios-compile',
    focus: 'COMPILE-SAFETY of every Swift change (there is no compiler here, so read declarations to type-check by hand): new files Models/EarningsLiveModels.swift, ViewModels/EarningsLiveViewModel.swift, Views/Atoms/LiveBadge.swift, Views/Molecules/EarningsResultChips.swift, Views/Organisms/EarningsLiveResultsCard.swift, Views/Screens/EarningsLiveScreen.swift, and the edits in TrackingModels.swift, AlertDestination.swift, ActivityRow.swift, AlertCardView.swift, AlertsTabContent.swift, AlertDetailView.swift, AlertDestinationCover.swift, TrackingViewModel.swift, APIEndpoint.swift, SettingsSyncManager.swift, NotificationSettingsViewModel.swift, NotificationsSettingsView.swift. Check: every referenced type/member exists with that exact signature (AppTypography.*, AppColors.*, AppSpacing.*, AppCornerRadius.*, AppSentiment.symbolName, EarningsQuarterResult, EarningsQuarterData.surpriseText, EarningsResultDot init, LiveIndicator, InlineRetryNotice init, BrowserLink/openExternal/.inAppBrowser, .cardSurface, APIClient.request signature and APIClient.shared, AppError.from/isCancellation/title); memberwise-initializer label ORDER for structs whose properties were extended (AppAlert.EarningsData, ActivityRow incl. its generic Trailing and the EmptyView extension init, every existing ActivityRow/EarningsData call site in the whole app); switch exhaustiveness over AppAlert, EarningsLiveStatus, AlertDestination.Target (search the whole app for switches over these, incl. NotificationRouteDestination and any switch on destination.target); the APIEndpoint exhaustive switches (path, authPolicy) and any other file that switches over APIEndpoint; MainActor default isolation problems (static stored properties, nonisolated contexts calling MainActor code, Codable conformance of plain structs decoded inside the APIClient actor, @StateObject init patterns); Optional-enum .none pitfalls; tuple-label conversions; closures capturing non-escaping parameters; string interpolation with nested quotes; ViewBuilder if/else-if without else; ForEach(Array(x.enumerated()), id: \\.offset) { _, q in ... }; Equatable/Hashable synthesis requirements. Report only things that would FAIL TO COMPILE or produce a compiler warning that the project treats as an error, with the exact line.',
  },
  {
    key: 'ios-behavior',
    focus: 'RUNTIME behaviour of the iOS changes: EarningsLiveViewModel polling (start/stop/loop termination when pollSeconds is nil, duplicate pollers on re-arm, restart on didBecomeActive when the sheet is not visible, weak self, Task cancellation during sleep, error handling, loadFailed semantics, isLoading); EarningsLiveResultsCard (uses a fallback until the first answer, status/eps/revenue unification, @StateObject identity when the sheet alert value changes, .inAppBrowser inside a sheet, onAppear/onDisappear/willResignActive/didBecomeActive lifecycle, "updated N min ago" freshness, what each status renders); AlertCardView/ActivityRow (LIVE badge overlay layout and hit testing, accessibility label, trailing chips vs date badge), EarningsResultChips (wording, colours via text-safe tokens, VoiceOver text); TrackingModels (status decoding fallback for old backends, description copy in each status incl. awaiting/notScheduled, title changes, consensus formatting, AppAlert.id stability), TrackingViewModel (merge of recentEarningsAlerts before alerts, id de-dup, the 30 s poll gate extension and its interaction with background/stop), AlertsTabContent (upcomingReport/duplicatesLiveResultCard/duplicatesUpcomingCard — could it hide a notification the user needs, or fail to hide a true duplicate?), AlertDestination ("See live results" first for result kinds, report_date route key, earnings_live tab destination), AlertDestinationCover -> EarningsLiveScreen (NavigationStack, Done button, dismiss), the Notification settings row (key, default, sync list) — and theme rules (text-safe colours, opaque backgrounds behind text, Dynamic Type truncation; run: cd ' + REPO + '/backend && ./venv/bin/python -m pytest -q tests/test_ios_theme_parity.py ; and: bash ' + REPO + '/frontend/ios/scripts/theme-lint.sh).',
  },
  {
    key: 'tests-docs',
    focus: 'the TESTS and DOCS of the commit: are the new tests meaningful (not vacuous, not passing for the wrong reason), deterministic (wall-clock/monotonic dependence, real sleeps, ordering, global singleton state leaking between tests such as els.get_earnings_live_service(), efs._watcher, earnings_live_sender._live_done, earnings_live_service._watched_memo, the tracking calendar memo), and do they cover the stated invariants? Run them: cd ' + REPO + '/backend && ./venv/bin/python -m pytest -q tests/test_earnings_live_status.py tests/test_earnings_live_service.py tests/test_earnings_live_push.py tests/test_earnings_filing_service.py tests/test_tracking_earnings_live_endpoint.py tests/test_ios_earnings_live_guards.py ; also run them with the file order reversed and run single test ids alone to expose order dependence. Then check the documentation claims in "documents/System Design/SYSTEM_DESIGN_GUIDELINES.md" (sections 7.1, 7.4, 11.4, 11.8) and documents/OWNER_TASKS.md (row 29, the new 2.1 item) against the code: cadences, windows (06:00-22:00, 12:00, release windows), budgets (25 symbols, ~190 calls), flags and defaults, file names, the claim "one push per report", the SQL in the owner verify step (valid against the notification_events schema in backend/database or the schema snapshot?), and the loop/task counts (29 tasks, 27 loops — count the _spawn calls in backend/app/main.py).',
  },
]

const LENSES = [
  {
    key: 'trace',
    text: 'Independently VERIFY this claimed defect by tracing the real code path end to end. Read the cited file and line, the callers and the callees. Where possible, write and run a minimal reproduction under ' + SP + '/review/ (a pytest or a python snippet that imports the real module; never modify repository files). Decide whether the failure scenario actually happens in this codebase as committed. real=true only if you can show the concrete path (or reproduction) that produces the wrong outcome.',
  },
  {
    key: 'refute',
    text: 'You are a SKEPTIC. Try hard to REFUTE this claimed defect: look for guards, callers that never pass the bad input, upstream validation, framework guarantees, tests that already prove the opposite, or a misreading of the code. Default to real=false if after careful checking you are not convinced the defect is real and reachable. Cite the exact code that refutes or confirms it.',
  },
  {
    key: 'intent',
    text: 'Judge this claimed defect against the commit INTENT and the repo conventions: read "documents/System Design/SYSTEM_DESIGN_GUIDELINES.md" section 11.8 and the relevant code. Is the behaviour actually WRONG (it contradicts the stated design, the user decisions, a shipped-client contract, or correctness), or is it a deliberate, documented choice / harmless? real=true only for a genuine defect worth fixing now; give the severity it truly deserves.',
  },
]

function findingText(f) {
  return [
    'CLAIMED DEFECT',
    'file: ' + f.file + ':' + f.line,
    'title: ' + f.title,
    'claimed severity: ' + f.severity,
    'description: ' + f.description,
    'failure scenario: ' + f.failure_scenario,
    'suggested fix: ' + f.suggested_fix,
  ].join('\n')
}

async function verifyFinding(f, phaseName) {
  const votes = await parallel(LENSES.map((lens) => () =>
    agent(PREAMBLE + '\n\n' + findingText(f) + '\n\nYOUR ROLE (' + lens.key + '): ' + lens.text, {
      label: 'verify:' + lens.key + ':' + String(f.file).split('/').pop() + ':' + f.line,
      phase: phaseName,
      schema: VERDICT,
    })))
  const ok = votes.filter(Boolean)
  const realVotes = ok.filter((v) => v.real)
  return Object.assign({}, f, { votes: ok, survives: realVotes.length >= 2 })
}

const results = await pipeline(
  DIMENSIONS,
  (d) => agent(PREAMBLE + '\n\nYOUR DIMENSION: ' + d.key + '\nReview, in depth: ' + d.focus + '\n\nReturn every real defect you find (empty list if none).', {
    label: 'find:' + d.key,
    phase: 'Find',
    schema: FINDINGS,
  }),
  (res, d) => {
    const findings = (res && res.findings) ? res.findings : []
    log(d.key + ': ' + findings.length + ' candidate finding(s)')
    return parallel(findings.map((f) => () => verifyFinding(Object.assign({}, f, { dimension: d.key }), 'Verify')))
  },
)

const all = results.filter(Boolean).flat().filter(Boolean)
const confirmed = all.filter((f) => f.survives)
const rejected = all.filter((f) => !f.survives)
log('verified: ' + confirmed.length + ' confirmed, ' + rejected.length + ' rejected')

phase('Critic')
const summary = all.map((f) => '- [' + (f.survives ? 'CONFIRMED' : 'rejected') + '] (' + f.dimension + ') ' + f.file + ':' + f.line + ' ' + f.title).join('\n') || '(none)'
const critic = await agent(PREAMBLE + '\n\nSeven reviewers each covered one dimension of this commit (live-core, filing, push, feed-api, ios-compile, ios-behavior, tests-docs). Their findings so far:\n' + summary + '\n\nYOUR ROLE: completeness critic. What did they MISS? Look for defects in areas between dimensions (e.g. backend<->iOS contract details, the interplay of the loop with the per-user feed cache, day-roll / DST edge cases, the shipped 1.02 app receiving the new pushes or route keys, docs vs code), and anything a dimension reviewer plausibly skimmed. Do NOT repeat the findings listed above. Return only NEW, concrete defects with file, line and failure scenario (empty list if you find none).', {
  label: 'critic',
  phase: 'Critic',
  schema: FINDINGS,
})
const missed = (critic && critic.findings) ? critic.findings : []
log('critic: ' + missed.length + ' new candidate(s)')
const missedVerified = (await parallel(missed.map((f) => () => verifyFinding(Object.assign({}, f, { dimension: 'critic' }), 'Verify missed')))).filter(Boolean)

const finalConfirmed = confirmed.concat(missedVerified.filter((f) => f.survives))
const finalRejected = rejected.concat(missedVerified.filter((f) => !f.survives))
return {
  confirmed: finalConfirmed.map((f) => ({
    dimension: f.dimension, file: f.file, line: f.line, title: f.title,
    severity: f.severity, description: f.description, failure_scenario: f.failure_scenario,
    suggested_fix: f.suggested_fix,
    votes: f.votes.map((v) => ({ real: v.real, severity: v.severity, reasoning: v.reasoning, evidence: v.evidence })),
  })),
  rejected: finalRejected.map((f) => ({
    dimension: f.dimension, file: f.file, line: f.line, title: f.title,
    votes: f.votes.map((v) => ({ real: v.real, reasoning: v.reasoning })),
  })),
}
