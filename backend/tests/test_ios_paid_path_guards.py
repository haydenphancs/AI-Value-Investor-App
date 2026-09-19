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


def _path_a_catch() -> str:
    """The comment-stripped, brace-bound body of Path A's `catch` inside `_fetchReport`."""
    body = _decl_body(_code(_REPORT_VM), "private func _fetchReport(")
    return _decl_body(body, "} catch {")


def test_path_a_failure_does_not_fall_through_to_the_billable_path_unconditionally():
    """⚠️ This guard used to be vacuous. It asserted the classifier's NAME appeared somewhere
    after `_fetchReport` and before `getTickerReport` — which the REFRESH-only gate
    (`if !allowPaidGeneration { … }`) satisfies on its own. Reverting the NORMAL-load gate to
    the original C2 shape (fall through unconditionally when paid generation is allowed) left
    every test in this file green while a drifted DTO charged 20 credits on every open.

    So: comment-stripped, bound to the catch, and the normal-load gate is located AFTER the
    refresh gate and required to be `if <classifier> { … } else { …return… }`."""
    catch = _path_a_catch()
    assert catch.count("Self.reportIsGenuinelyUnavailable(error)") >= 2, (
        "Path A's catch must classify the failure on BOTH the refresh path and the normal "
        "load — one consult is the refresh-only gate, which does nothing for a plain open"
    )

    # Skip past the refresh gate; the normal-load gate is what bills or refuses to.
    refresh = _decl_body(catch, "if !allowPaidGeneration")
    tail = catch[catch.index(refresh) + len(refresh):]
    m = re.search(r"if\s+Self\.reportIsGenuinelyUnavailable\(error\)\s*\{", tail)
    assert m, "the NORMAL-load gate is missing: an unqualified catch re-buys an owned report"
    # `_decl_body` returns from the `{`, so offset from the brace, not from the match.
    then_block = _decl_body(tail[m.start():], "if")
    after = tail[tail.index("{", m.start()) + len(then_block):]
    assert re.match(r"\s*else\s*\{", after), (
        "the normal-load gate has no else-arm — a transport/decode failure must be REFUSED, "
        "not silently allowed to reach the billable Path B"
    )
    else_block = _decl_body(after, "else")
    assert re.search(r"\breturn\b", else_block), (
        "the refusal arm must `return`: showing an error and then continuing into "
        "`getTickerReport` still bills"
    )
    # And all of that happens before the billable call.
    body = _decl_body(_code(_REPORT_VM), "private func _fetchReport(")
    assert body.index("} catch {") < body.index(".getTickerReport(")


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
    # 2026-09-16: the hard-cap 422 (INVALID_INPUT) and a typed 5xx are contract-shaped too;
    # dropping their bodies cost a history GET and a second POST before the non-streaming
    # door surfaced the same copy. Both must sit on the DECODING arm, not on `default:`.
    decoded_arm = re.search(r"\bcase\b[^\n]*\b422\b(?!\d)[^\n]*\n", body)
    assert decoded_arm, "the SSE reader no longer decodes a 422 body"
    assert re.search(r"\bcase\b[^\n]*\b500\.\.\.599\b", body), "the SSE reader no longer decodes 5xx bodies"
    tail = body[body.index("case 400"):]
    assert "APIErrorResponse.self" in tail[:tail.index("case 404")]


def test_the_sse_error_frame_is_decoded_into_a_business_error():
    """The stream door emits `error {error_code, user_message}` AFTER it has refunded the
    turn. The arm used to throw a bare `ChatStreamError.serverError`, so the catch could not
    tell an outage (terminal, already refunded) from a persist failure (reconcile), and
    re-POSTed GEMINI_QUOTA_EXCEEDED through the non-streaming door: a second precharge, a
    second hit on the open circuit, twice the wait, and the backend's copy lost."""
    body = _decl_body(_code(_CHAT_VM), "private func streamMessageToSession(")
    arm = body[body.index('case "error":'):body.index('case "meta":')]
    assert '"error_code"' in arm and "APIError.businessError(code:" in arm, arm
    assert '"user_message"' in arm
    # The literal is kept for the page-parity test, and the bare throw stays as the
    # fallback for a frame with no code.
    assert "ChatStreamError.serverError" in arm


def test_the_meta_frame_rewrites_the_optimistic_bubble_and_the_reconcile_clamps():
    """The reconcile counts LOCAL user bubbles equal to the server-normalised target. With
    the bubble raw ("hold…") and the target NFKC'd ("hold..."), that count was 0 — and a
    zero expectation adopted the PREVIOUS turn's history as if this one had persisted: the
    question vanished, unanswered, no banner, no retry."""
    vm = _code(_CHAT_VM)
    body = _decl_body(vm, "private func streamMessageToSession(")
    arm = body[body.index('case "meta":'):body.index('case "credits":')]
    assert "messages[idx].content = [.text(serverMessage)]" in arm, arm
    # By IDENTITY first: with `messages` replaced underneath the turn (a history load that
    # raced the send), "the last user row" was the PREVIOUS question and took this turn's
    # text. The role-based fallback is only for a caller that passed no id.
    assert "messages.firstIndex(where: { $0.id == userMessageId })" in arm, arm
    assert arm.index("firstIndex(where: { $0.id == userMessageId })") < \
        arm.index("lastIndex(where: { $0.role == .user })")
    # Both senders thread their optimistic bubble's id through.
    send = _decl_body(vm, "func sendMessage(_ text: String)")
    assert "userMessageId: userMessage.id" in send, "sendMessage must pass its bubble id"
    seed = _decl_body(vm, "func startNewConversation(")
    assert "userMessageId: userMessage.id" in seed, "the seed must pass its bubble id"
    rec = _decl_body(vm, "private func reconcileAfterStreamFailure(")
    # 2026-09-17: the count oracle is gone — the reconcile keys on server ids (see
    # test_the_reconcile_oracle_is_identity_based_and_never_regenerates_on_a_transport_failure).
    assert "knownAssistantServerIds()" in rec, rec


def test_the_reconcile_oracle_is_identity_based_and_never_regenerates_on_a_transport_failure():
    """Two defects shared the count oracle: the server's history page is the newest 400
    rows while the local list is the whole conversation (a saved, charged turn read as
    "absent" → re-POST), and ONE "not there yet" read after a transport drop was taken as
    "will never be there" while the origin — silent to a phone whose radio dropped — was
    still generating, persisting and charging the turn."""
    vm = _code(_CHAT_VM)
    oracle = _decl_body(vm, "static func historyContainsTurn(")
    assert "knownAssistantServerIds.contains(last.id)" in oracle
    assert "messages[messages.count - 2]" in oracle and 'question.role == "user"' in oracle
    assert "reconcileKey(" in oracle, "both sides must be folded like the backend's normalize_text"
    assert "expectedUserMatches" not in vm, "the count oracle must be gone everywhere"
    # F03-7: the fold strips every code point the server's `normalize_text` strips. The
    # server persists the stripped copy and the client compares its RAW message folded by
    # this function, so a code point stripped on one side only never matches (the bidi
    # isolates 0x2066-0x2069 were that gap).
    from app.services.chat_security import _INVISIBLE_CODEPOINTS
    fold = _decl_body(vm, "static func reconcileKey(")
    ranges = re.findall(r"0x([0-9A-Fa-f]{4})\.\.\.0x([0-9A-Fa-f]{4})|0x([0-9A-Fa-f]{4})(?![0-9A-Fa-f.])", fold)
    covered = set()
    for lo, hi, single in ranges:
        if single:
            covered.add(int(single, 16))
        else:
            covered.update(range(int(lo, 16), int(hi, 16) + 1))
    missing = sorted(hex(cp) for cp in _INVISIBLE_CODEPOINTS if cp not in covered)
    assert missing == [], f"reconcileKey does not strip {missing}, which normalize_text does"
    rec = _decl_body(vm, "private func reconcileAfterStreamFailure(")
    assert "mayRegenerate" in rec
    assert "[0, 2, 5, 10, 15, 15]" in rec, "a transport failure gets a bounded WAIT, not a verdict"
    tail = rec[rec.index("guard mayRegenerate else"):]
    assert "reportTurnFailure(ChatStreamError.unconfirmed)" in tail[:tail.index("sendMessageToSession(")]
    stream = _decl_body(vm, "private func streamMessageToSession(")
    catch = stream[stream.index("} catch {"):]
    assert "if case APIError.businessError = error { serverVerdict = true }" in catch
    assert "mayRegenerate: serverVerdict" in catch
    models = _code(_REPO / "frontend" / "ios" / "ios" / "Models" / "ChatConversationModels.swift")
    assert "var serverId: String?" in models
    assert "serverId: id.isEmpty ? nil : id" in models, "toRichChatMessage must carry the server id"


def test_a_failed_history_read_never_regenerates_the_turn():
    """The stream persists and charges BEFORE `credits`/`done` (a suggestions call sits in
    between). The reconcile's history GET is the only oracle that the turn is absent, and
    it used to regenerate on ANY read failure — a phone locked in that window plus one
    transport blip stored and billed the same Q+A twice with no refund path."""
    vm = _code(_CHAT_VM)
    body = _decl_body(vm, "private func reconcileAfterStreamFailure(")
    assert "fetchHistoryForReconcile(sessionId: sessionId)" in body
    unavailable = body[body.index("case .unavailable"):]
    regen = unavailable.index("sendMessageToSession(")
    arm = unavailable[:regen]
    assert "return" in arm and "reportTurnFailure(ChatStreamError.unconfirmed)" in arm, arm
    # The retry helper is bounded and does not retry a 404 (nothing to adopt).
    helper = _decl_body(vm, "private func fetchHistoryForReconcile(")
    assert "for (attempt, delay) in delays.enumerated()" in helper
    assert "if case APIError.notFound = error { return .unavailable(error) }" in helper


def test_the_non_stream_door_adopts_a_persisted_answer_instead_of_reporting_a_timeout():
    """`.sendChatMessage` has a 60 s inter-byte timeout and the server persists + charges
    the moment generation ends; a timeout or a dropped connection therefore says nothing
    about whether the answer exists. The catch used to report a red "timed out" banner
    and leave the answer invisible until the conversation was reloaded — and the user's
    next tap sent (and paid for) the same question again."""
    vm = _code(_CHAT_VM)
    body = _decl_body(vm, "private func sendMessageToSession(")
    catch = body[body.index("} catch {"):]
    assert "isTransportFailure(error)" in catch
    assert "fetchHistoryForReconcile(sessionId: sessionId)" in catch
    assert "historyContainsTurn(" in catch
    assert "sendMessageToSession(" not in catch, "the non-stream catch must never re-POST"
    assert "ChatStreamError.unconfirmed" in catch
    tf = _decl_body(vm, "static func isTransportFailure(")
    assert "case .timeout, .noConnection: return true" in tf


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
    for code in ("INSUFFICIENT_CREDITS", "SYSTEM_BUSY", "CHAT_DAILY_LIMIT_REACHED",
                 # 2026-09-16: the message itself is the problem (blank / over the hard cap)
                 "INVALID_INPUT",
                 # and the two outage codes the `error` frame carries after `refund_once` —
                 # re-POSTing them precharges again into the same open circuit.
                 "GEMINI_QUOTA_EXCEEDED", "GEMINI_UNAVAILABLE"):
        assert code in body, f"{code} is no longer treated as a terminal pre-flight refusal"
    # INTERNAL_ERROR must stay recoverable: the "couldn't be saved" frame fires after a turn
    # the server may have delivered, and reconcile is exactly what adopts it.
    assert "INTERNAL_ERROR" not in body


def test_status_line_refusals_on_the_stream_open_are_terminal_not_reconciled():
    """F10-10: a 404 (session gone), any 401 (token refused at the door) and AUTH_UNAVAILABLE
    arrive BEFORE the handler runs, so nothing was charged and nothing is in history. The
    reconcile spent a history GET that failed the same way and then laundered the real
    reason into "couldn't confirm" — an expired session became "refresh the conversation"."""
    vm = _code(_CHAT_VM)
    body = _decl_body(vm, "static func isTerminalPreflightRefusal(")
    for case in ("APIError.notFound", "APIError.authRequired", "APIError.unauthorized",
                 "APIError.authError"):
        assert case in body, f"{case} is reconciled instead of reported"
    assert '"AUTH_UNAVAILABLE"' in body
    # The stream door gives a 404 chat copy and drops the ghost row — row FIRST: its
    # `resetConversation()` nils `errorMessage`, so a banner set before it never rendered
    # (W2 E-4).
    stream = _decl_body(vm, "private func streamMessageToSession(")
    terminal = stream[stream.index("if Self.isTerminalPreflightRefusal(error)"):]
    terminal = terminal[:terminal.index("reconcileAfterStreamFailure")]
    assert "ChatStreamError.sessionGone" in terminal and "removeSessionLocally(sessionId)" in terminal
    assert terminal.index("removeSessionLocally(sessionId)") < terminal.index("ChatStreamError.sessionGone")
    # ...and so does the non-stream door, in the same order.
    non_stream = _decl_body(vm, "private func sendMessageToSession(")
    catch = non_stream[non_stream.index("} catch {"):]
    assert "ChatStreamError.sessionGone" in catch and "removeSessionLocally(sessionId)" in catch
    assert catch.index("removeSessionLocally(sessionId)") < catch.index("ChatStreamError.sessionGone")
    reset = _decl_body(vm, "func resetConversation(")
    assert "errorMessage = nil" in reset, "the ordering above only matters while reset clears the banner"
    # The credits re-read stays tied to the 402, not to every terminal code (a 401 would
    # fail the same way).
    assert 'code == "INSUFFICIENT_CREDITS"' in catch
    enum = _decl_body(vm, "private enum ChatStreamError")
    assert "case sessionGone" in enum and "no longer exists" in enum


def test_a_cancelled_reconcile_read_does_not_paint_unconfirmed(monkeypatch=None):
    """F10-6: a same-session reload cancels the reconcile Task; its history read then
    returns `.unavailable(CancellationError)`, and both `.unavailable` arms reported
    "couldn't confirm" over a conversation that had just reloaded fine."""
    vm = _code(_CHAT_VM)
    for decl in ("private func reconcileAfterStreamFailure(", "private func sendMessageToSession("):
        body = _decl_body(vm, decl)
        arm = body[body.index("case .unavailable"):]
        arm = arm[:arm.index("reportTurnFailure(ChatStreamError.unconfirmed)")]
        assert "!Task.isCancelled" in arm, f"{decl}: the .unavailable arm reports on a cancelled read"


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


def test_a_send_cannot_race_a_history_load():
    """Slow network: tap a history row, type while the spinner shows, send. The load then
    replaced `messages` (optimistic bubble gone) and this turn's `meta` frame rewrote the
    PREVIOUS question. Belt: the ViewModel refuses the send while a load is in flight;
    braces: the screen greys the bar and hides the chips for the same window."""
    vm = _code(_CHAT_VM)
    send = _decl_body(vm, "func sendMessage(_ text: String)")
    assert re.search(r"guard\s+!isAITyping\s*,\s*!isLoadingSession\s+else\s*\{\s*return\s*\}", send), (
        "sendMessage must guard on isLoadingSession as well as isAITyping"
    )
    # The load's catch releases the gate, or a failed load would strand the send bar.
    load = _decl_body(vm, "func loadConversation(sessionId: String)")
    catch = load[load.index("} catch {"):]
    assert "isLoadingSession = false" in catch
    screen = _code(_CHAT_SCREEN)
    assert re.search(r"isBusy:\s*viewModel\.isAITyping\s*\|\|\s*viewModel\.isLoadingSession", screen), (
        "the chat bar must read busy while a history row is loading"
    )
    chips = screen[screen.index("suggestions: (viewModel.messages.isEmpty"):]
    chips = chips[:chips.index("suggestions.map")]
    assert "!viewModel.isLoadingSession" in chips, "chips must hide while a history row loads"


def test_an_adopted_turn_refreshes_the_wallet_like_the_non_stream_door_does():
    """The `credits` frame precedes `done`, so a stream that died after the answer never
    delivered it; the reconcile adopted the persisted (charged) turn and left the balance
    one credit stale until the next turn. `sendMessageToSession`'s adopt path already
    re-read it; the stream's did not."""
    vm = _code(_CHAT_VM)
    rec = _decl_body(vm, "private func reconcileAfterStreamFailure(")
    adopt = rec[rec.index("Self.historyContainsTurn(history.messages, userMessage: target"):]
    adopt = adopt[:adopt.index("return")]
    assert "refreshCreditsIfMoved(nil)" in adopt, adopt
