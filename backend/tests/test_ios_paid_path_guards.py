"""Source-level guards for iOS defects that cost users money or strand the app.

Four groups: **C2** (report double-charge), **C4** (stranded chat), **C13** (indicator trap),
and the **chat money path** (a 402 that was illegible, non-terminal and invisible). Keep this
count in sync when adding a group — a guard file whose header undercounts its own contents is
how you end up believing a defect is uncovered.

There is no XCTest target in this project, so — exactly like `test_ios_auth_policy_parity.py`
and `test_ios_theme_parity.py` — these invariants are pinned from Python by reading the Swift
source. A failure here is a real bug that ships, not a style nit.

All three were found in the 2026-08-07 deep check and are documented in
`~/.claude/plans/handoff-deep-check-floofy-lightning.md`:

  * **C2** — opening a report you already own could silently charge 20 credits, because Path
    A's `catch` was unqualified and fell through to the billable path on a network blip or a
    decode failure (`TickerReportViewModel.swift`).
  * **C4** — a second "Ask Cay AI" from the same host screen permanently stranded the chat.
  * **C13** — `stochastic()` admitted `count >= 14` but built the %D range as `15..<count`,
    trapping at runtime on a series of exactly 14 points
    (`TechnicalIndicatorCalculator.swift`).

(This header has already drifted twice — it said "two defects" when C13 landed, and "three"
when the chat money path did. That is the recurrence this note exists to stop.)

Note what these can and cannot prove. They pin the SHAPE of the source, not runtime behaviour:
a semantically-equivalent rewrite passes without the guard actually running, and a rename
breaks the test without breaking the app. C13's only behavioural proof was a standalone Swift
harness run in a throwaway scratchpad, and neither C2 nor C13 has been tap-verified in the app.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_REPORT_VM = _REPO / "frontend/ios/ios/ViewModels/TickerReportViewModel.swift"
_CHAT_VM = _REPO / "frontend/ios/ios/ViewModels/ChatViewModel.swift"
_INDICATORS = _REPO / "frontend/ios/ios/Core/TechnicalIndicatorCalculator.swift"


def _src(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return path.read_text()


# ── C2: opening a report you already own must not silently cost 20 credits ────
#
# `_fetchReport` has two paths. Path A reads the already-generated report by `reportId` and is
# FREE. Path B (`getTickerReport`) runs the full ~17 Gemini + ~20 FMP pipeline on a cache miss
# and charges 20 credits. Path A's `catch` was unqualified, so a network blip, a timeout, a
# transient 500, a rate-limit or a Codable `decodingError` all fell through and re-bought a
# report the user already owned — silently, with no prompt. A DTO drift is worse: the decode
# fails every time, so that report charges on EVERY open.


def test_path_a_failure_does_not_fall_through_to_the_billable_path_unconditionally():
    src = _src(_REPORT_VM)
    assert "reportIsGenuinelyUnavailable" in src, (
        "TickerReportViewModel must classify Path A failures before falling through to the "
        "BILLABLE live fetch — an unqualified `catch` re-buys a report the user already owns"
    )

    # The classifier must be consulted inside _fetchReport, not merely defined.
    body = src[src.index("private func _fetchReport"):]
    guard_at = body.index("reportIsGenuinelyUnavailable")
    fallthrough_at = body.index("getTickerReport")
    assert guard_at < fallthrough_at, (
        "the classification must happen BEFORE the billable Path B call"
    )


def test_only_genuinely_absent_reports_justify_a_paid_regeneration():
    """The backend documents exactly three "not there" outcomes for this route
    (`research.py::get_research_ticker_report`). Anything else is a transport or contract
    failure, where regenerating spends money to paper over a bug."""
    src = _src(_REPORT_VM)
    block = src[src.index("fallthroughErrorCodes"):]
    block = block[: block.index("]")]

    for code in ("REPORT_NOT_FOUND", "REPORT_NOT_READY", "DATA_INCOMPLETE"):
        assert code in block, f"{code} is a legitimate fall-through and must stay listed"

    # The expensive mistakes: these must NEVER be treated as "report is absent".
    for code in (
        "NETWORK", "TIMEOUT", "RATE_LIMITED", "INTERNAL", "GEMINI", "DECODE", "UNKNOWN",
    ):
        assert code not in block, (
            f"{code} means we could not READ the report, not that it is missing — falling "
            f"through charges 20 credits for a transport failure"
        )


def test_the_classifier_defaults_to_not_falling_through():
    """A `default:` that returns true would re-open the hole for every future APIError case,
    and for any non-APIError thrown by the decoder."""
    src = _src(_REPORT_VM)
    fn = src[src.index("static func reportIsGenuinelyUnavailable"):]
    fn = fn[: fn.index("\n    }")]

    assert "guard let apiError = error as? APIError else { return false }" in fn, (
        "a non-APIError (e.g. a raw DecodingError) must not be read as 'report absent'"
    )
    default_arm = fn[fn.index("default:"):]
    assert "return false" in default_arm, (
        "the default arm must NOT fall through — new APIError cases are unknown failures, "
        "and unknown must never mean 'spend the user's credits'"
    )


# ── C4: the second "Ask Cay AI" must not strand the chat forever ──────────────
#
# `startNewConversation` sets `isAITyping = true` and replaces `messages`, then guarded its
# post-await completion on `currentSessionId == nil`. The host screen owns the view model as a
# `@StateObject` and `AIChatScreen` never resets it, so from the SECOND open onward
# `currentSessionId` was still set from the previous conversation: the guard failed, the seed
# returned early, and `isAITyping` was never cleared. Messages wiped, spinner forever, send bar
# dead — on the app's flagship surface, with ten entry points into it.


def test_the_seed_staleness_test_is_not_the_session_id():
    src = _src(_CHAT_VM)
    seed = src[src.index("func startNewConversation"):]
    seed = seed[: seed.index("\n    /// Send a message")]

    assert "seedGeneration" in seed, (
        "startNewConversation must use a generation token to detect staleness"
    )
    assert "currentSessionId == nil else" not in seed, (
        "`currentSessionId == nil` cannot express 'is this seed still current' once ANY "
        "conversation exists — that test stranded every chat after the first"
    )


def test_every_seed_invalidating_operation_bumps_the_generation():
    """A token nobody bumps is a token that never invalidates. All three lifecycle methods
    that make an in-flight seed obsolete must increment it."""
    src = _src(_CHAT_VM)
    for fn_name, end_marker in (
        ("func startNewConversation", "\n    /// Send a message"),
        ("func loadConversation", "\n        Task {"),
        ("func resetConversation", "\n    }"),
    ):
        body = src[src.index(fn_name):]
        body = body[: body.index(end_marker)]
        assert "seedGeneration &+=" in body, (
            f"{fn_name} must invalidate any in-flight seed — cancellation is cooperative and "
            f"the seed can already be past its await"
        )


def test_both_seed_exit_paths_are_guarded():
    """The `catch` needs the same test as the success path, or a superseded seed paints an
    error onto a chat that has already moved on."""
    src = _src(_CHAT_VM)
    seed = src[src.index("func startNewConversation"):]
    seed = seed[: seed.index("\n    /// Send a message")]
    assert seed.count("seed == seedGeneration") >= 2, (
        "both the success guard and the catch guard must compare the captured generation"
    )


def test_the_generation_counter_cannot_crash_on_overflow():
    """`&+=`, not `+=`. An overflow trap here would crash the app rather than wrap; only
    equality is ever tested, so wrap-around is harmless."""
    src = _src(_CHAT_VM)
    assert "seedGeneration += 1" not in src, "use &+= — a trapping overflow would crash"
    assert re.search(r"private var seedGeneration: UInt64 = 0", src), (
        "the generation token must be declared as a non-optional UInt64 starting at 0"
    )


# ── C13: the stochastic sub-chart must not trap on a short series ─────────────


def test_stochastic_guards_the_percent_d_window_not_just_percent_k():
    """`stochastic` admitted `count >= kPeriod` (14) but built %D's range as
    `(kPeriod - 1 + dPeriod - 1)..<count` = `16..<count`. At count 14 or 15 that is a Range
    with lowerBound > upperBound, which TRAPS at runtime — a crash, not a short chart.
    Reachable from any 14- or 15-candle series: a newly listed ticker, a short intraday
    window, a holiday-shortened range."""
    src = _src(_INDICATORS)
    fn = src[src.index("static func stochastic("):]

    assert "let dStart = kPeriod - 1 + dPeriod - 1" in fn
    assert "guard dStart < count else" in fn, (
        "%D's start index must be bounds-checked before it becomes a Range"
    )

    # The guard must precede the loop that uses it.
    assert fn.index("guard dStart < count") < fn.index("for i in dStart..<count"), (
        "the guard must come before the loop, or it guards nothing"
    )


def test_stochastic_still_returns_percent_k_when_percent_d_is_impossible():
    """Degrading to an all-nil %D is the honest answer; returning all-nil %K as well would
    blank a chart that has perfectly good data."""
    src = _src(_INDICATORS)
    fn = src[src.index("static func stochastic("):]
    guard_body = fn[fn.index("guard dStart < count else"):]
    guard_body = guard_body[: guard_body.index("}")]
    assert "kValues: kValues" in guard_body, (
        "the short-series path must still return the computed %K values"
    )


# ── Credit-balance freshness and honesty (2026-08-08 adversarial review) ──────────────
#
# All four below are places where a credit number shown to the user was either stale, not
# theirs, or described as something it isn't. They cost trust and, in two cases, money.

_RESEARCH_VM = _REPO / "frontend/ios/ios/ViewModels/ResearchViewModel.swift"
_PROFILE_VM = _REPO / "frontend/ios/ios/ViewModels/ProfileViewModel.swift"
_BUY_VIEW = _REPO / "frontend/ios/ios/Views/Screens/BuyCreditsView.swift"


def _strip_swift_comments(src: str) -> str:
    """Drop `//` lines and trailing comments — mandatory for "must NOT appear" assertions,
    since this codebase explains its invariants in prose that quotes the forbidden tokens."""
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"//.*$", "", raw))
    return "\n".join(out)


def test_research_view_model_refreshes_credits_when_an_entitlement_lands():
    """Buying credits from the Research tab must re-enable Generate without a relaunch.

    `ResearchViewModel` holds its own `creditBalance` (it has no `AppState` reference), written
    only by `loadCredits()` from init / refresh / report-completion. Nothing re-read it after a
    purchase — the sheets carry no `onDismiss` — so a user paid, the sheet closed, and the
    Generate button stayed disabled with "insufficient credits" until a manual refresh.
    """
    src = _strip_swift_comments(_src(_RESEARCH_VM))
    assert "@Published var creditBalance" in src, "guard is stale — the property moved"
    assert "caydexEntitlementChanged" in src, (
        "ResearchViewModel must observe .caydexEntitlementChanged (StoreKitService's single "
        "funnel for interactive purchases AND Transaction.updates replays) or its private "
        "credit balance goes stale the moment a user buys credits from this very tab"
    )


def test_profile_view_model_does_not_load_the_guest_sentinel_balance():
    """`/users/me/credits` is `.guestAllowed`, and a signed-out caller resolves to the SHARED
    guest sentinel — seeded ~100,000 credits. Loading it writes a balance that is not the
    user's into `AppState.user.credits`, which every other surface then renders as theirs.
    `ResearchViewModel.loadCredits` already guards; Profile did not."""
    src = _strip_swift_comments(_src(_PROFILE_VM))
    body = src[src.index("func loadCredits("):]
    body = body[:body.index("\n    func ", 1)] if "\n    func " in body[1:] else body
    assert re.search(r"isAuthenticated|isSignedIn", body), (
        "ProfileViewModel.loadCredits() must refuse to load a balance for a signed-out caller "
        "— otherwise the shared guest sentinel's credits are written into AppState as the "
        "user's own"
    )


def test_buy_credits_header_does_not_fabricate_a_zero_balance():
    """Rendering `?? 0` states a balance we do not have. The codebase's stated policy for an
    unknown balance is to HIDE the number (ResearchModels' `.mock` note, GenerateAnalysisSection),
    because "0 credits available" on the Buy Credits screen reads as "you have none" to someone
    who may have plenty."""
    src = _strip_swift_comments(_src(_BUY_VIEW))
    assert "credits?.remaining ?? 0" not in src, (
        "Buy Credits renders a fabricated 0 when the balance is unknown — hide it instead"
    )


# ── The 5xx retry must never re-send a WRITE ─────────────────────────────────
#
# `APIClient.request` retried on `.serverError` with `retryCount = 2` and no method
# check. A 5xx says nothing about whether the origin committed, and an edge that drops
# the response AFTER the handler ran is indistinguishable from one that never reached
# it — so `POST /research/generate`, which precharges 20 credits and inserts a row
# BEFORE returning, could be billed up to three times for one tap. A free account is
# seeded 50 credits. The agent-run dedup collapses the duplicate pipelines into one
# Gemini run, so the compute was deduplicated and only the BILLING multiplied, which
# is why nothing upstream ever noticed.

_API_CLIENT = _REPO / "frontend/ios/ios/Core/Services/APIClient.swift"
_API_ENDPOINT = _REPO / "frontend/ios/ios/Core/Services/APIEndpoint.swift"


def _strip_comments(src: str) -> str:
    """Remove // and /* */ comments so a guard cannot be satisfied by prose.

    Without this every assertion below passes on the DOC COMMENT that explains the
    rule, which is exactly how a source-scan guard goes vacuous.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def test_server_error_retry_is_gated_on_an_idempotent_method():
    code = _strip_comments(_src(_API_CLIENT))
    retries = re.findall(r"if retryCount > 0,\s*case \.serverError = (?:error|apiError)[^{]*\{", code)
    assert retries, "the 5xx retry block moved or was renamed — re-point this guard"
    for block in retries:
        assert "isSafeToRetryAfterServerError" in block, (
            "a 5xx retry is not gated on the HTTP method. Re-sending POST "
            "/research/generate after a dropped response charges another 20 credits; "
            "gate on `endpoint.method.isSafeToRetryAfterServerError`.\n"
            f"offending block: {block!r}"
        )


def test_only_get_is_declared_safe_to_retry():
    code = _strip_comments(_src(_API_ENDPOINT))
    m = re.search(r"var isSafeToRetryAfterServerError:\s*Bool\s*\{([^}]*)\}", code)
    assert m, "HTTPMethod.isSafeToRetryAfterServerError is missing"
    body = m.group(1)
    assert "self == .GET" in body, (
        "only GET may be auto-retried after a 5xx; every other verb can carry a "
        f"side effect. body was: {body!r}"
    )
    for verb in (".POST", ".PUT", ".PATCH", ".DELETE"):
        assert verb not in body, f"{verb} must not be admitted to the 5xx retry"


# ── The report cost shown to the user must be the cost the server charges ────


def test_ios_analysis_cost_matches_the_backend_report_credit_cost():
    from app.config import settings

    src = _strip_comments(_src(_REPO / "frontend/ios/ios/Models/ResearchModels.swift"))
    m = re.search(r"static let standard = AnalysisCost\(credits:\s*(\d+)\s*\)", src)
    assert m, "AnalysisCost.standard moved — the Generate button's 'Uses N Credits' label"
    assert int(m.group(1)) == settings.REPORT_CREDIT_COST, (
        f"iOS advertises {m.group(1)} credits on the Generate button while the backend "
        f"charges {settings.REPORT_CREDIT_COST}. The button label and the debit must "
        "move together — a mismatch is a disclosure problem, not a cosmetic one."
    )


# ── The moat radar must not be handed geometry it traps on ───────────────────
#
# `polygonPath` reduces an empty `dimensions` array to `i % 0`, which is an integer
# remainder by zero — a Swift TRAP, i.e. a hard crash, not a NaN. Reachable from a
# saved report: `MoatCompetitionResponse.dimensions` has no minimum length and
# `research_reports.ticker_report_data` is user history that CACHE_SCHEMA_FLOOR never
# invalidates.


def test_moat_radar_guards_against_degenerate_geometry():
    code = _strip_comments(_src(_REPO / "frontend/ios/ios/Views/Molecules/ReportMoatRadarChart.swift"))
    assert re.search(r"dimensions\.count >= 3", code), (
        "ReportMoatRadarChart must refuse to plot fewer than 3 pillars — `i % sides` "
        "traps when `sides` is 0"
    )
    for fn, guard in (("polygonPath", r"guard sides > 0"), ("dataPolygonPath", r"guard !values\.isEmpty")):
        assert re.search(guard, code), (
            f"{fn} must be safe on its own terms so a future caller cannot crash the app"
        )


# ── The Reports list must live-poll on the screen that is actually presented ──
#
# `startReportsPolling()` was wired only in `Views/Screens/ResearchView.swift`, a
# preview-only copy that is never presented, so in the shipping app nothing armed it.


def test_reports_polling_is_armed_from_the_live_screen():
    code = _strip_comments(_src(_REPO / "frontend/ios/ios/ContentView.swift"))
    assert "startReportsPolling()" in code, (
        "ContentView (which hosts the LIVE ResearchViewWithBinding) must arm the "
        "Reports live-poll; wiring it only in the preview-only screen ships nothing"
    )
    assert "stopReportsPolling()" in code, "…and must stop it when the screen goes away"


def test_the_dead_duplicate_research_screen_is_gone():
    """A second full implementation of a screen is where fixes go to die: the polling
    wiring above lived there, correct and unreachable, for as long as it existed."""
    assert not (_REPO / "frontend/ios/ios/Views/Screens/ResearchView.swift").exists(), (
        "ResearchView.swift is back. The live Research screen is "
        "`ResearchViewWithBinding` in ContentView.swift — do not re-create a second copy."
    )


# ── The chat money path: 402 must be legible, terminal, and visible ──────────
#
# Chat charges CHAT_CREDIT_COST per turn but, unlike the report path, showed the user
# nothing and gave them nowhere to go when the wallet ran out. Three separate defects had
# to line up for that; fixing any one alone is invisible, which is exactly why all three
# are pinned here.
#
# Each guard below is brace-bounded to the declaration it is about and reads
# comment-stripped source: this file's own prose quotes every token these assert on, so an
# un-stripped whole-file scan would pass on the explanation after the code was reverted.

_API_CLIENT = _REPO / "frontend/ios/ios/Core/Services/APIClient.swift"
_CHAT_SCREEN = _REPO / "frontend/ios/ios/Views/Screens/AIChatScreen.swift"


def _decl_body(src: str, prefix: str, open_ch: str = "{") -> str:
    """The delimiter-matched body of the declaration starting at `prefix`.

    Asserting against a whole file passes when the token lives in a DIFFERENT declaration —
    which is how a fix to a preview-only duplicate screen once looked like a fix to the live
    one. Bound the scan to the declaration you actually mean.

    `prefix` need only be enough to identify the declaration; the scan starts at the first
    `open_ch` after it, so a signature that spans lines or gains a parameter still matches.
    `open_ch="["` bounds a collection literal instead of a body.
    """
    close_ch = {"{": "}", "[": "]", "(": ")"}[open_ch]
    at = src.index(prefix)
    start = src.index(open_ch, at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced {open_ch}{close_ch} after {prefix!r}")


def _code(path: Path) -> str:
    return _strip_swift_comments(_src(path))


def test_the_sse_reader_decodes_payment_and_admission_refusals():
    """A streamed 402 must become a `businessError`, not a bare `serverError`.

    The SSE reader's status switch handled 401/404/429 and swept EVERYTHING else into
    `default: throw APIError.serverError(statusCode:)`. Streaming is the path users are
    actually on, so running out of credits mid-chat produced a generic "server error" —
    `AppError.from` had no code to map, so `.insufficientCredits` (and with it the `.upgrade`
    action that opens Buy Credits) could never be reached. The non-streaming path decoded the
    same body correctly, which is why this looked fixed and was not.
    """
    body = _decl_body(_code(_API_CLIENT), "private func openStreamOnce(")
    # Anchored, not a substring: `"case 402" in body` also matches `case 4020`, which is how
    # this guard passed a mutation test it should have failed. Require the status as a whole
    # number — a real `case` arm is followed by a separator, never another digit.
    assert re.search(r"\bcase\b[^\n]*\b402\b(?!\d)", body), (
        "the SSE reader no longer decodes 402 — an out-of-credits chat turn falls back to a "
        "generic serverError and the user gets no Upgrade route"
    )
    assert re.search(r"\b409\b(?!\d)", body), (
        "the SSE reader no longer decodes 409 SYSTEM_BUSY — a transient admission refusal "
        "becomes an indistinguishable generic server error"
    )
    assert "APIError.businessError" in body, (
        "402/409 must carry the backend error_code through as a businessError; without it "
        "AppError.mapAPIError has nothing to switch on"
    )
    # The code, not the status, is the contract — the body has to actually be read.
    assert "APIErrorResponse.self" in body


def test_chat_does_not_re_post_a_turn_the_server_refused_before_generating():
    """A 402/409 must NOT fall into the stream-failure reconcile.

    Every other stream failure is recoverable by the non-streaming fallback, so the catch
    unconditionally ran a history GET and then re-POSTed. For a pre-flight refusal the server
    never started generating and persisted nothing, so that reconcile spends a round trip to
    fail identically — and on any code where it did NOT fail, it would charge a second time.
    """
    body = _decl_body(_code(_CHAT_VM), "private func streamMessageToSession(")
    assert "isTerminalPreflightRefusal" in body, (
        "the stream catch no longer short-circuits terminal refusals — a 402 will spend a "
        "history GET and then re-POST to the non-streaming endpoint"
    )
    # The short-circuit has to come BEFORE the reconcile, or it proves nothing.
    assert body.index("isTerminalPreflightRefusal") < body.index("reconcileAfterStreamFailure"), (
        "the terminal-refusal check must precede reconcileAfterStreamFailure"
    )


def test_insufficient_credits_is_classified_as_terminal():
    """The set is matched on backend ErrorCode values, so it must actually contain them."""
    body = _decl_body(_code(_CHAT_VM), "private static let terminalPreflightCodes", open_ch="[")
    for code in ("INSUFFICIENT_CREDITS", "SYSTEM_BUSY", "CHAT_DAILY_LIMIT_REACHED"):
        assert code in body, f"{code} is no longer treated as a terminal pre-flight refusal"


def test_a_failed_chat_turn_reaches_the_global_error_host():
    """Setting `errorMessage` alone is a dead end.

    The in-chat banner is a string with an ✕. The ACTION — `.insufficientCredits` carrying
    `.upgrade`, i.e. the route to Buy Credits — is attached by `AppState.handleError`, and
    `ChatViewModel` did not have an `AppState` reference at all. Without this the user is told
    they are out of credits and given no way to buy any.
    """
    src = _code(_CHAT_VM)
    body = _decl_body(src, "private func reportTurnFailure(")
    assert "appState?.handleError(error)" in body, (
        "a failed chat turn no longer reaches the global error host — the Upgrade action is "
        "unreachable and the banner is a dead end"
    )
    assert "weak var appState: AppState?" in src, "ChatViewModel lost its AppState reference"


def test_the_chat_cover_hosts_the_global_error_surfaces():
    """`.errorPresentationHost()` must be applied INSIDE the chat's fullScreenCover.

    A fullScreenCover is its own presentation: the root's error toast and its Buy Credits
    sheet render BEHIND it and are never seen. So decoding the 402 correctly and routing it
    correctly still leaves the user with nothing on screen. Its removal is silent by
    construction — nothing crashes, the sheet simply never appears — so a guard is the only
    thing that catches it.
    """
    body = _decl_body(_code(_CHAT_SCREEN), "private struct AIChatCoverModifier: ViewModifier")
    assert ".errorPresentationHost()" in body, (
        "the AI chat cover no longer hosts the error surfaces — a 402 inside chat shows an "
        "invisible toast and opens an invisible Buy Credits sheet"
    )
    # `EnvironmentValues.appState` defaults to a throwaway AppState(), so a broken chain
    # fails SILENTLY. The explicit re-injection is the defence.
    assert "environment(\\.appState" in body or ".appState, appState)" in body, (
        "the cover no longer re-injects appState explicitly; a broken inheritance chain would "
        "silently bind a throwaway AppState and nothing would ever update"
    )


def test_the_chat_screen_hands_its_view_model_the_app_state():
    """One injection point covers all 15 hosts that present the chat."""
    body = _decl_body(_code(_CHAT_SCREEN), "struct AIChatScreen: View")
    assert "viewModel.appState = appState" in body, (
        "AIChatScreen no longer injects AppState — the chat balance goes stale and 402s "
        "cannot reach the Upgrade route"
    )


def test_the_chat_balance_is_refreshed_only_when_credits_actually_moved():
    """A free follow-up moves nothing, so it must not spend a request proving that."""
    body = _decl_body(_code(_CHAT_VM), "private func refreshCreditsIfMoved(")
    assert "movedCredits" in body, (
        "the refresh no longer gates on whether the turn moved credits — a free follow-up "
        "will spend a request on the answer path for no reason"
    )
