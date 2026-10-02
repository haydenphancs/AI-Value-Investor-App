"""Account › Credit History decides the account gate from APIClient's REFUSAL, never from a
pre-flight `auth.status` read.

The sibling of `test_ios_alerts_gate_from_refusal.py` (Tracking › Alerts, 2026-10-02) and
`test_ios_reports_gate_from_refusal.py` (Research › Reports, 2026-10-01).
`CreditHistoryViewModel.performLoad` opened with `guard AppActions.shared.isSignedIn`.
`isSignedIn` is `status == .authenticated`, but `AppState.performRestore` arms the stored token
while the status still reads `.restoring` — at launch (after `primeStoredCredential`) and on
every heal — so the guard refused a request that would have SUCCEEDED and latched
"Reconnecting…". The fix mirrors `NotificationInboxViewModel.performLoad`: send the request and
classify the outcome. `.listCreditHistory` is `.signInRequired`, so an UNARMED call is refused
by `APIClient.buildRequest` before any I/O and arrives typed as `AppError.signInRequired`.

What is pinned:

1. `performLoad` has no status read; its typed-refusal arm comes after the cancellation guard
   and before the failure log, picks Reconnecting vs Sign In from
   `let reconnecting = AppActions.shared.isRestoringSession` (exactly that read), writes the gate
   once, drops an in-flight "Load more", clears the rows AND the day groups the screen draws
   (`regroup()` after `items = []`) and the next-page cursor, and returns.
2. File-wide, no other member gates the load on a session read — `load()`, `loadAndWait()`,
   `loadMore()` and `reset()` included.
3. The refusal the arm classifies is real: `.listCreditHistory` is `.signInRequired`,
   `buildRequest` throws `APIError.authRequired` for such a route with no token, and
   `AppError` maps that to `.signInRequired`. Break any link and the arm is dead code — an
   unarmed load reaches the network and the 401 renders as an error.
4. `CreditHistoryView`: the first-load `.task` reads no session; the auth-status heal fires on
   `.authenticated` while `isAuthBlocked` (both gate states) and re-runs the load; an identity
   change resets the ViewModel before reloading; no other session read in the view.
5. `.reconnecting` offers no action (auth.md §5: `requestSignIn` declines to prompt during a
   restore, so a button there is inert and a false statement); `.signedOut`'s Sign In reaches
   `appState.requestSignIn`.

There is no XCTest target (testing.md §3), so this pins the Swift source: comments are stripped
before every assertion (the fix's own comment names `isSignedIn` and `auth.status`), every check
is brace-bound to the declaration it means, and presence is asserted before a block is sliced.
`test_ios_credit_history_compact.py` pins the paging half (three `invalidateLoadMore()` in
`performLoad`: refusal, success and failure); `test_ios_account_gate_state.py` carries this load
in `_GATED_STATE_LOADS`.

Mutation-tested IN MEMORY (`pathlib.Path.read_text` monkeypatched for the one target file — the
real Swift files are never touched; other sessions read them concurrently). The table runs on
every pass as `test_each_mutation_is_killed`, and each mutation must fail with the assertion
message that names it, so it cannot "pass" by tripping an unrelated earlier check.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_VM = _IOS / "ViewModels" / "CreditHistoryViewModel.swift"
_VIEW = _IOS / "Views" / "Screens" / "CreditHistoryView.swift"
_ENDPOINT = _IOS / "Core" / "Services" / "APIEndpoint.swift"
_CLIENT = _IOS / "Core" / "Services" / "APIClient.swift"
_APP_ERROR = _IOS / "Core" / "Utilities" / "AppError.swift"

# Any pre-flight read of the session status. `isRestoringSession` is deliberately NOT here: it
# is the correct read INSIDE the refusal arm (which of the two gates to draw).
_STATUS = r"\bisSignedIn\b|\bisAuthenticated\b|auth\.status"
# Every session accessor a load could be gated on.
_SESSION_READ = _STATUS + r"|\bisRestoringSession\b|\bhasUnusedStoredCredential\b"

_CLASS = "final class CreditHistoryViewModel: ObservableObject"
_PERFORM = "private func performLoad() async"
_VIEW_DECL = "struct CreditHistoryView: View"
_CONTENT = "private var content: some View"
_AUTH_POLICY = "nonisolated var authPolicy: AuthPolicy"
_BUILD = "private func buildRequest(for endpoint: APIEndpoint, baseURL: URL? = nil) throws -> URLRequest"
_MAP = "private static func mapAPIError(_ error: APIError) -> AppError"

_ARM = "if case .signInRequired = appError"
_GATE_WRITE = "state = reconnecting ? .reconnecting : .signedOut"
_RECONNECTING_READ = r"\blet\s+reconnecting\s*=\s*AppActions\.shared\.isRestoringSession\s*\n"
_CANCEL_GUARD = "guard !Task.isCancelled else { return }"
_HEALER = ".onChange(of: appState.auth.status)"
_HEAL_GUARD = "guard status == .authenticated, isAuthBlocked else { return }"
_FIRST_LOAD_TASK = r"\.task\s*\{"
_POLICY_ARM = r"\bcase\s+\.listCreditHistory\s*:\s*return\s+\.signInRequired\b"
_PRE_FLIGHT = r"if\s+endpoint\.authPolicy\s*==\s*\.signInRequired\s*,\s*authToken\s*==\s*nil\s*\{\s*throw\s+APIError\.authRequired\s*\}"
_TYPED = r"\bcase\s+\.authRequired\s*:\s*return\s+\.signInRequired\(feature:\s*nil\)"


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the fix's own comment names `isSignedIn` and `auth.status` while explaining
    why the code no longer reads them, so an un-stripped scan for their ABSENCE fails on prose
    and a scan for a PRESENCE passes on a revert whose comment survived. A tail needs leading
    whitespace, so a `https://` inside a string literal is not cut.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", raw))
    return "\n".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _block(src: str, header: str) -> str:
    """The balanced `{`…`}` body that follows the ONLY `header` (a literal prefix)."""
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    start = src.index("{", src.index(header) + len(header))
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after `{header}`")


def _without_block(src: str, header: str) -> str:
    """`src` with the `header` and its balanced body cut out."""
    body = _block(src, header)
    at = src.index(header)
    start = src.index("{", at + len(header))
    return src[:at] + src[start + len(body):]


def _blocks(src: str, pattern: str) -> list[str]:
    """The balanced `{…}` body after EVERY match of the regex `pattern`."""
    out = []
    for m in re.finditer(pattern, src):
        start = src.index("{", m.start())
        depth = 0
        for i in range(start, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append(src[start: i + 1])
                    break
        else:
            raise AssertionError(f"unbalanced braces after `{m.group(0)}`")
    return out


def _line_of(src: str, m: re.Match | None) -> str | None:
    """The stripped source line holding match `m` (for a legible failure message)."""
    if m is None:
        return None
    end = src.find("\n", m.end())
    return src[src.rfind("\n", 0, m.start()) + 1: end if end != -1 else len(src)].strip()


def _vm_class() -> str:
    return _block(_code(_VM), _CLASS)


def _load() -> str:
    load = _block(_vm_class(), _PERFORM)
    # Anti-vacuity: the live first-page load, not a same-named stub.
    assert ("repository.fetchCreditHistory(limit: Self.pageSize, before: nil)" in load
            and "items = page.items" in load), (
        "the credit-history performLoad block is not the live first-page load — re-derive "
        "this guard")
    return load


def _do_and_catch(load: str) -> tuple[str, str]:
    assert load.count("do {") == 1 and load.count("} catch {") == 1, (
        "performLoad no longer has exactly one do/catch — re-derive these scans")
    return load[load.index("do {"): load.index("} catch {")], _block(load, "} catch")


def _view() -> str:
    return _block(_code(_VIEW), _VIEW_DECL)


# ── 1. The load decides the gate from the typed refusal ─────────────────────


def test_the_credit_history_load_decides_the_gate_from_the_refusal():
    load = _load()
    _, catch = _do_and_catch(load)
    m = re.search(_STATUS, load)
    assert not m, (
        f"the credit-history load decides the gate from auth.status again (`{m and m.group(0)}`): "
        "the token is armed while the status reads .restoring, so the guard refuses a request "
        "that would have succeeded and latches Reconnecting")
    assert catch.count(_ARM) == 1, (
        f"the credit-history load has no typed-refusal arm (`{_ARM}`), so a refused pass is "
        "flattened into an error the user cannot fix by retrying")
    arm = _block(catch, _ARM)
    assert load.count("isRestoringSession") == 1 and "isRestoringSession" in arm, (
        "the credit-history load reads isRestoringSession outside its typed-refusal arm — a "
        "pre-flight session read decides the gate again instead of APIClient's refusal")
    # A substring test passes on `!AppActions.shared.isRestoringSession` or `… == false`: pin
    # the whole statement, ending at the line break (auth.md §5).
    assert re.search(_RECONNECTING_READ, arm), (
        "the credit-history refusal inverts or replaces the restoring read (`let reconnecting = "
        "AppActions.shared.isRestoringSession` exactly): a restoring user would get Sign In and "
        "a signed-out user a permanent Reconnecting")
    assert arm.count(_GATE_WRITE) == 1 and len(re.findall(r"\bstate\s*=(?!=)", arm)) == 1, (
        f"the credit-history refusal does not write the gate exactly once as `{_GATE_WRITE}` — "
        "a hardcoded or later write wins over the isRestoringSession decision")
    assert re.search(r"\breturn\s*\}\s*$", arm), (
        "the credit-history refusal falls through into the error state")
    fail_log = catch.find("log.error(")
    assert fail_log != -1 and catch.find("state = .error(") != -1, (
        "the credit-history load lost its real-failure log or error state — the ordering check "
        "is vacuous")
    assert catch.index(_ARM) < fail_log, (
        "the credit-history refusal is logged as a load failure: `log.error(` runs before the "
        "typed-refusal arm, so every refused launch reads as an outage in the logs")
    cancel_at = catch.find(_CANCEL_GUARD)
    assert cancel_at != -1 and cancel_at < catch.index(_ARM), (
        "a cancelled credit-history load can draw the account gate: the catch's "
        "`guard !Task.isCancelled` no longer runs before the typed-refusal arm (APIClient wraps "
        "a cancellation, and `reset()` cancels the previous identity's load through it)")

    cleared = re.search(r"\bitems\s*=\s*\[\]", arm)
    assert cleared, (
        "the credit-history refusal keeps rows it can no longer refresh — another account's "
        "spending survives under the gate")
    regroup = arm.find("regroup()")
    assert regroup != -1 and regroup > cleared.start(), (
        "the credit-history refusal clears `items` but not the day groups the screen draws: "
        "`regroup()` must run after `items = []`")
    assert re.search(r"\bnextCursor\s*=\s*nil\b", arm), (
        "the credit-history refusal keeps a next-page cursor, so a Load more can page an "
        "account's history after the gate")
    assert "invalidateLoadMore()" in arm, (
        "the credit-history refusal leaves an in-flight Load more alive — it lands on the "
        "cleared list and splices the previous page back under the gate")


# ── 2. No other member gates the load on a session read ─────────────────────


def test_no_other_credit_history_member_gates_the_load_on_a_session_read():
    """Section 1 looks only inside performLoad. The bug returns unseen if the pre-flight moves
    one level up — into `load()`, `loadAndWait()`, `loadMore()` or a helper. File-wide, the
    only session read left is the arm's isRestoringSession."""
    rest = _without_block(_code(_VM), _ARM)
    m = re.search(_SESSION_READ, rest)
    assert not m, (
        "a pre-flight session read gates the credit-history load from another member "
        f"(`{_line_of(rest, m)}`) — the token is armed while the status reads .restoring, so "
        "it refuses a request that would have succeeded")


# ── 3. The refusal the arm classifies is real ───────────────────────────────


def test_an_unarmed_load_is_refused_typed_before_any_io():
    policy = _block(_code(_ENDPOINT), _AUTH_POLICY)
    assert re.search(_POLICY_ARM, policy), (
        "listCreditHistory is no longer .signInRequired, so an unarmed load reaches the network "
        "and its 401 renders as an error instead of the account gate (and auth.md §1 pairs this "
        "with the backend's get_current_user)")
    build = _block(_code(_CLIENT), _BUILD)
    assert re.search(_PRE_FLIGHT, build), (
        "buildRequest no longer refuses an unarmed .signInRequired call before any I/O — the "
        "credit-history refusal arm is dead code")
    mapping = _block(_code(_APP_ERROR), _MAP)
    assert re.search(_TYPED, mapping), (
        "APIError.authRequired no longer maps to AppError.signInRequired, so the pre-flight "
        "refusal arrives untyped and misses the credit-history refusal arm")


# ── 4. The screen heals the gate and never pre-flights it ───────────────────


def test_the_screen_heals_the_gate_and_never_pre_flights_it():
    view = _view()
    tasks = _blocks(view, _FIRST_LOAD_TASK)
    assert len(tasks) == 1 and "viewModel.loadAndWait()" in tasks[0], (
        "the Credit History first load is gone or doubled — re-derive this scan")
    m = re.search(_SESSION_READ, tasks[0])
    assert not m, (
        f"the Credit History `.task` reads the session (`{m and m.group(0)}`): gating the first "
        "load on it skips the load while the session is .restoring")

    blocked = _block(view, "private var isAuthBlocked: Bool")
    for state in ("viewModel.state == .reconnecting", "viewModel.state == .signedOut"):
        assert state in blocked, (
            f"isAuthBlocked ignores `{state}`, so the auth-status heal skips a gate still "
            "waiting on the session")

    healer = _block(view, _HEALER)
    assert _HEAL_GUARD in healer, (
        "the Credit History heal no longer fires exactly on `.authenticated` while gated")
    assert "viewModel.loadAndWait()" in healer, (
        "the Credit History heal no longer re-runs the load, so a gate latched during restore "
        "stays until the user leaves and re-opens the screen")

    identity = _block(view, ".reloadOnIdentityChange")
    reset_at = identity.find("viewModel.reset()")
    reload_at = identity.find("viewModel.loadAndWait()")
    assert reset_at != -1 and reload_at != -1 and reset_at < reload_at, (
        "an identity change reloads Credit History without resetting it first — the previous "
        "account's rows or gate stay on screen until the new load lands (auth.md §7)")

    rest = _without_block(view, _HEALER)
    m = re.search(_SESSION_READ, rest)
    assert not m, (
        f"a session read gates the Credit History screen outside its heal (`{_line_of(rest, m)}`)"
        " — the gate is the ViewModel's, decided from the refusal")


# ── 5. Reconnecting offers no action; Sign In reaches the prompt ────────────


def test_reconnecting_offers_no_button_and_sign_in_reaches_the_prompt():
    content = _block(_view(), _CONTENT)
    for label in ("case .reconnecting:", "case .signedOut:", "case .error("):
        assert content.count(label) == 1, f"`{label}` is gone or doubled — re-derive this scan"
    recon_at = content.index("case .reconnecting:")
    signed_at = content.index("case .signedOut:")
    error_at = content.index("case .error(")
    assert recon_at < signed_at < error_at, (
        "the Credit History switch arms moved — re-derive the slices below")
    reconnecting = content[recon_at:signed_at]
    signed_out = content[signed_at:error_at]
    assert "InlineRetryNotice(" in reconnecting, "the reconnecting arm no longer renders a notice"
    assert "onRetry" not in reconnecting and "retryTitle" not in reconnecting, (
        "the Credit History reconnecting state offers an action — requestSignIn declines to "
        "prompt during a restore, so it is a dead control on top of a false statement")
    assert "appState.requestSignIn(for:" in signed_out and "onRetry:" in signed_out, (
        "the Credit History signed-out state's Sign In does not reach appState.requestSignIn")


# ── Anti-vacuity ────────────────────────────────────────────────────────────


def test_comment_stripping_is_real_and_load_bearing():
    stripped = _strip_swift_comments(
        "// AppActions.shared.isSignedIn\n"
        "/* auth.status\n   isAuthenticated */\n"
        "    let x = 1  // state = .signedOut\n")
    assert not re.search(_STATUS, stripped) and ".signedOut" not in stripped
    assert "let x = 1" in stripped
    # The fix's own comment in performLoad names the removed status read. If it ever stops
    # doing so this stays harmless; while it does, it proves the stripping is what keeps the
    # absence checks honest.
    raw = _VM.read_text(encoding="utf-8")
    head = raw[raw.index(_PERFORM): raw.index("let page = try await repository.fetchCreditHistory")]
    assert re.search(_STATUS, head), (
        "the performLoad comment no longer names the removed status read — fine, but then this "
        "anti-vacuity probe needs a new raw sample")
    assert not re.search(_STATUS, _strip_swift_comments(head))


@pytest.mark.parametrize("path,outer,header,minimum", [
    (_VM, _CLASS, _PERFORM, 400),
    (_VIEW, _VIEW_DECL, _CONTENT, 400),
    (_VIEW, _VIEW_DECL, _HEALER, 60),
    (_ENDPOINT, None, _AUTH_POLICY, 2000),
    (_CLIENT, None, _BUILD, 400),
    (_APP_ERROR, None, _MAP, 400),
])
def test_each_scan_is_bounded_to_its_declaration(path, outer, header, minimum):
    src = _code(path)
    scope = _block(src, outer) if outer else src
    block = _block(scope, header)
    assert len(block) > minimum, f"`{header}` block is only {len(block)} chars — the scan drifted"
    assert len(block) < len(src) // 2, (
        f"`{header}` block is {len(block)} of {len(src)} chars — `_block` stopped bounding")


# ── The mutations above, re-run in memory on every pass ─────────────────────

_V_DO = ("        do {\n"
         "            let page = try await repository.fetchCreditHistory(limit: Self.pageSize, "
         "before: nil)\n")
_V_CATCH_HEAD = ("        } catch {\n            guard !Task.isCancelled else { return }\n"
                 "            // ⚠️ `catch is CancellationError`")
_V_ARM_HEAD = "            let appError = AppError.from(error)\n            if case .signInRequired = appError {"
_V_ARM_RETURN = ('                return\n            }\n'
                 '            log.error("load credit history failed')
_V_ARM_INVALIDATE = "                invalidateLoadMore()\n                items = []\n"
_V_ARM_ROWS = "                items = []\n                regroup()\n                nextCursor = nil\n"
_V_LOAD = "    func load() {\n        invalidateLoadMore()\n        loadTask?.cancel()\n"
_V_LOAD_AND_WAIT = "    func loadAndWait() async {\n        load()\n"
_V_LOAD_MORE = "    func loadMore() {\n        guard let cursor = nextCursor, !isLoadingMore else { return }\n"

_E_POLICY = "        case .listCreditHistory:\n            return .signInRequired\n"
_C_PRE_FLIGHT = ("        if endpoint.authPolicy == .signInRequired, authToken == nil {\n"
                 "            throw APIError.authRequired\n        }\n")
_M_TYPED = "        case .authRequired:\n            return .signInRequired(feature: nil)\n"

_W_TASK = "        .task { await viewModel.loadAndWait() }\n"
_W_BLOCKED = "        viewModel.state == .reconnecting || viewModel.state == .signedOut\n"
_W_HEAL = ("            guard status == .authenticated, isAuthBlocked else { return }\n"
           "            Task { await viewModel.loadAndWait() }\n")
_W_IDENTITY = "            viewModel.reset()\n            await viewModel.loadAndWait()\n"
_W_RECONNECTING = ('                message: "Reconnecting your account…",\n'
                   '                systemImage: "arrow.clockwise",\n'
                   "                iconColor: AppColors.textMuted\n")
_W_SIGN_IN = '                onRetry: { appState.requestSignIn(for: "see your credit history") }\n'

_T1 = test_the_credit_history_load_decides_the_gate_from_the_refusal
_T2 = test_no_other_credit_history_member_gates_the_load_on_a_session_read
_T3 = test_an_unarmed_load_is_refused_typed_before_any_io
_T4 = test_the_screen_heals_the_gate_and_never_pre_flights_it
_T5 = test_reconnecting_offers_no_button_and_sign_in_reaches_the_prompt

_GUARD = "guard AppActions.shared.isSignedIn else { return }\n"

# (name, file, ((anchor, replacement), ...), guard, the assertion message it must fail WITH).
# Every anchor must occur EXACTLY once at the moment it is applied; edits apply in order.
_MUTATIONS = [
    # ── 1. the refusal arm ──
    ("V1-status-guard-back", _VM, ((_V_DO, "        " + _GUARD + _V_DO),),
     _T1, "the credit-history load decides the gate from auth.status again"),
    ("V1b-softened-status-guard", _VM,
     ((_V_DO, "        guard AppActions.shared.isSignedIn || AppActions.shared.isRestoringSession "
              "else { return }\n" + _V_DO),),
     _T1, "the credit-history load decides the gate from auth.status again"),
    ("V2-restoring-pre-flight", _VM,
     ((_V_DO, "        guard !AppActions.shared.isRestoringSession else { return }\n" + _V_DO),),
     _T1, "the credit-history load reads isRestoringSession outside its typed-refusal arm"),
    ("V3-arm-disabled", _VM,
     (("if case .signInRequired = appError {", "if false, case .signInRequired = appError {"),),
     _T1, "the credit-history load has no typed-refusal arm"),
    ("V4-arm-falls-through", _VM,
     ((_V_ARM_RETURN, '            }\n            log.error("load credit history failed'),),
     _T1, "the credit-history refusal falls through into the error state"),
    ("V5-inverted-restoring-read", _VM,
     (("let reconnecting = AppActions.shared.isRestoringSession",
       "let reconnecting = !AppActions.shared.isRestoringSession"),),
     _T1, "the credit-history refusal inverts or replaces the restoring read"),
    # The old backstop: a refusal that always says "signed out".
    ("V6-hardcoded-signed-out", _VM, ((_GATE_WRITE, "state = .signedOut"),),
     _T1, "the credit-history refusal does not write the gate exactly once"),
    ("V7-gate-overwritten", _VM,
     (("                " + _GATE_WRITE + "\n",
       "                " + _GATE_WRITE + "\n                state = .signedOut\n"),),
     _T1, "the credit-history refusal does not write the gate exactly once"),
    ("V8-refusal-logged-as-failure", _VM,
     ((_V_ARM_HEAD, "            let appError = AppError.from(error)\n"
                    '            log.error("load credit history failed")\n'
                    "            if case .signInRequired = appError {"),),
     _T1, "the credit-history refusal is logged as a load failure"),
    ("V9-cancelled-load-gates", _VM,
     ((_V_CATCH_HEAD, "        } catch {\n            // ⚠️ `catch is CancellationError`"),),
     _T1, "a cancelled credit-history load can draw the account gate"),
    ("V10-keeps-rows", _VM,
     ((_V_ARM_ROWS, "                regroup()\n                nextCursor = nil\n"),),
     _T1, "the credit-history refusal keeps rows it can no longer refresh"),
    ("V11-keeps-day-groups", _VM,
     ((_V_ARM_ROWS, "                items = []\n                nextCursor = nil\n"),),
     _T1, "the credit-history refusal clears `items` but not the day groups"),
    ("V11b-regroups-before-clearing", _VM,
     ((_V_ARM_ROWS, "                regroup()\n                items = []\n"
                    "                nextCursor = nil\n"),),
     _T1, "the credit-history refusal clears `items` but not the day groups"),
    ("V12-keeps-cursor", _VM,
     ((_V_ARM_ROWS, "                items = []\n                regroup()\n"),),
     _T1, "the credit-history refusal keeps a next-page cursor"),
    ("V13-load-more-survives", _VM,
     ((_V_ARM_INVALIDATE, "                items = []\n"),),
     _T1, "the credit-history refusal leaves an in-flight Load more alive"),
    # ── 2. a pre-flight moved one level up ──
    ("V14-pre-flight-in-load", _VM,
     ((_V_LOAD, "    func load() {\n        " + _GUARD
                + "        invalidateLoadMore()\n        loadTask?.cancel()\n"),),
     _T2, "a pre-flight session read gates the credit-history load from another member"),
    ("V15-restoring-pre-flight-in-load-and-wait", _VM,
     ((_V_LOAD_AND_WAIT, "    func loadAndWait() async {\n"
                         "        guard !AppActions.shared.isRestoringSession else { return }\n"
                         "        load()\n"),),
     _T2, "a pre-flight session read gates the credit-history load from another member"),
    ("V16-pre-flight-in-load-more", _VM,
     ((_V_LOAD_MORE, _V_LOAD_MORE + "        " + _GUARD),),
     _T2, "a pre-flight session read gates the credit-history load from another member"),
    # ── 3. the refusal chain ──
    ("E1-route-guest-allowed", _ENDPOINT,
     ((_E_POLICY, "        case .listCreditHistory:\n            return .guestAllowed\n"),),
     _T3, "listCreditHistory is no longer .signInRequired"),
    ("C1-no-pre-flight-refusal", _CLIENT, ((_C_PRE_FLIGHT, ""),),
     _T3, "buildRequest no longer refuses an unarmed .signInRequired call before any I/O"),
    ("M1-refusal-untyped", _APP_ERROR,
     ((_M_TYPED, "        case .authRequired:\n            return .unauthorized\n"),),
     _T3, "APIError.authRequired no longer maps to AppError.signInRequired"),
    # ── 4. the screen ──
    ("W1-first-load-reads-session", _VIEW,
     ((_W_TASK, "        .task {\n            guard appState.auth.isAuthenticated else { return }\n"
                "            await viewModel.loadAndWait()\n        }\n"),),
     _T4, "the Credit History `.task` reads the session"),
    ("W2-first-load-gone", _VIEW, ((_W_TASK, ""),),
     _T4, "the Credit History first load is gone or doubled"),
    ("W3-blocked-ignores-signed-out", _VIEW,
     ((_W_BLOCKED, "        viewModel.state == .reconnecting\n"),),
     _T4, "isAuthBlocked ignores `viewModel.state == .signedOut`"),
    ("W3b-blocked-ignores-reconnecting", _VIEW,
     ((_W_BLOCKED, "        viewModel.state == .signedOut\n"),),
     _T4, "isAuthBlocked ignores `viewModel.state == .reconnecting`"),
    ("W4-heal-not-on-authenticated", _VIEW,
     ((_HEAL_GUARD, "guard status != .unauthenticated, isAuthBlocked else { return }"),),
     _T4, "the Credit History heal no longer fires exactly on `.authenticated` while gated"),
    ("W5-heal-does-not-reload", _VIEW,
     ((_W_HEAL, "            guard status == .authenticated, isAuthBlocked else { return }\n"
                "            Task { viewModel.reset() }\n"),),
     _T4, "the Credit History heal no longer re-runs the load"),
    ("W6-identity-reload-without-reset", _VIEW,
     ((_W_IDENTITY, "            await viewModel.loadAndWait()\n"),),
     _T4, "an identity change reloads Credit History without resetting it first"),
    ("W7-session-read-in-view", _VIEW,
     ((_W_TASK, "        .onAppear { if appState.auth.isAuthenticated { viewModel.load() } }\n"
                + _W_TASK),),
     _T4, "a session read gates the Credit History screen outside its heal"),
    # ── 5. the two gate arms ──
    ("W8-reconnecting-offers-sign-in", _VIEW,
     ((_W_RECONNECTING, _W_RECONNECTING.rstrip("\n") + ",\n"
                        '                retryTitle: "Sign In",\n'
                        "                onRetry: { appState.requestSignIn(for: nil) }\n"),),
     _T5, "the Credit History reconnecting state offers an action"),
    ("W9-sign-in-is-dead", _VIEW,
     ((_W_SIGN_IN, "                onRetry: { viewModel.load() }\n"),),
     _T5, "the Credit History signed-out state's Sign In does not reach appState.requestSignIn"),
]


@pytest.mark.parametrize(
    "path,edits,test,message",
    [m[1:] for m in _MUTATIONS],
    ids=[m[0] for m in _MUTATIONS],
)
def test_each_mutation_is_killed(monkeypatch, path, edits, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — other sessions' tests read these Swift files concurrently,
    so they are never rewritten."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    mutated = original
    for old, new in edits:
        assert mutated.count(old) == 1, (
            f"mutation anchor `{old[:60]!r}` occurs {mutated.count(old)}x in {path.name} — "
            "re-derive this mutation against the new source rather than deleting it")
        mutated = mutated.replace(old, new, 1)
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    # The unmutated source passes (the plain tests above prove it); mutated, it must fail.
    with pytest.raises(AssertionError, match=re.escape(message)):
        test()


def test_the_mutation_table_covers_every_guard():
    """A guard with no mutation behind it is unproven; keep the table honest as tests are added."""
    covered = {m[3] for m in _MUTATIONS}
    for guard in (_T1, _T2, _T3, _T4, _T5):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"
    names = [m[0] for m in _MUTATIONS]
    assert len(names) == len(set(names)), "duplicate mutation ids"
