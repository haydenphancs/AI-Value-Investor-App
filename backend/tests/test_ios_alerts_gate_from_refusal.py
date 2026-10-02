"""Tracking › Alerts' two account-scoped loads decide the account gate from APIClient's REFUSAL,
never from a pre-flight `auth.status` read — and every session read left in those files, and in
`WhaleService`, is a deliberate one.

The sibling of `test_ios_reports_gate_from_refusal.py` (Research › Reports, 2026-10-01).
`NotificationInboxViewModel.performLoad` and `PriceAlertStore.performLoad` both opened with
`guard AppActions.shared.isSignedIn`. `isSignedIn` is `status == .authenticated`, but
`AppState.performRestore` arms the stored token while the status still reads `.restoring` — at
launch (after `primeStoredCredential`) and on every heal — so the guard refused requests that
would have SUCCEEDED and drew "Reconnecting…". Alerts is where a cold-launch push tap lands. The
fix mirrors `HomeDashboardViewModel.performLoad`: send the request and classify the outcome.
`.listNotifications` and `.listPriceAlerts` are `.signInRequired`, so an UNARMED call is refused
by `APIClient.buildRequest` before any I/O and arrives typed as `AppError.signInRequired`.

What is pinned:

1. `NotificationInboxViewModel.performLoad` has no status read; its typed-refusal arm comes after
   the cancellation guard and before the failure log, picks Reconnecting vs Sign In from
   `let reconnecting = AppActions.shared.isRestoringSession` (exactly that read), writes the gate
   once, drops the rows and the next-page cursor, publishes NO unread count, and returns.
2. `PriceAlertStore.performLoad` the same, behind the catch's identity-epoch guard (a refusal
   asked for by the previous identity must not gate the next account), and its arm nils
   `lastLoadedAt`.
3. Only a pass that saw the account is stamped fresh: `lastLoadedAt = Date()` is written once,
   in the success path, after its epoch guard; every other write is `nil`.
4. File-wide, no other member gates either load on a session read. The ONE left in the inbox is
   `refreshUnreadCount`'s skip, cut out exactly before scanning.
5. That skip is safe by construction, and this pins why: `refreshUnreadCount` latches nothing
   (writes no `state` / `items`), and `establishAuthenticatedSession` publishes `.authenticated`
   BEFORE `onAuthenticated`, whose transition fan-out calls it. A launch-time `didBecomeActive`
   skipped in the `.restoring` window is therefore re-run once the identity settles.
   (`test_ios_alerts_badge_and_filters.py` pins the skip itself.)
6. `WhaleService.toggleFollow` is a user TAP and keeps `guard AppActions.shared.isSignedIn`,
   ahead of any optimistic state — a follow must not fire on an unvalidated session. It is the
   only session read in the file (WhaleService has no load; `TrackingViewModel.loadWhaleList`
   owns the roster and is pinned in `test_ios_account_gate_state.py`), and `requestSignIn` turns
   the tap into the "Reconnecting your account…" toast while a credential is restoring.
7. `AlertsTabContent`: the activation `.task(id: isActiveTab)` reads no session; the auth-status
   heal fires on `.authenticated` while `isAuthBlocked`, which covers all four gate states; an
   identity change resets both stores before its `isActive` early return.

There is no XCTest target (testing.md §3), so this pins the Swift source: comments are stripped
before every assertion (the fix's own comments name `isSignedIn` and `auth.status`), every check
is brace-bound to the declaration it means, and presence is asserted before a block is sliced.

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
_INBOX = _IOS / "ViewModels" / "NotificationInboxViewModel.swift"
_STORE = _IOS / "Core" / "Services" / "PriceAlertStore.swift"
_WHALE = _IOS / "Views" / "Screens" / "WhaleService.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_ALERTS_TAB = _IOS / "Views" / "Organisms" / "AlertsTabContent.swift"

# Any pre-flight read of the session status. `isRestoringSession` is deliberately NOT here: it
# is the correct read INSIDE the refusal arm (which of the two gates to draw).
_STATUS = r"\bisSignedIn\b|\bisAuthenticated\b|auth\.status"
# Every session accessor a load could be gated on.
_SESSION_READ = _STATUS + r"|\bisRestoringSession\b|\bhasUnusedStoredCredential\b"

_PERFORM = "private func performLoad() async"
_REFRESH = "func refreshUnreadCount() async"
_TOGGLE = "func toggleFollow(_ whaleId: String) -> Bool"
_ESTABLISH = "private func establishAuthenticatedSession(userId: String) async"
_ON_AUTH = "private func onAuthenticated(userId: String? = nil, identity: Int) async"
_SAME_USER = "if let userId, userId == lastAuthenticatedUserId"
_REQUEST_SIGN_IN = "func requestSignIn(for feature: String?)"
_ALERTS_VIEW = "struct AlertsTabContent: View"

_ARM = "if case .signInRequired = appError"
_GATE_WRITE = "state = reconnecting ? .reconnecting : .signedOut"
_RECONNECTING_READ = r"\blet\s+reconnecting\s*=\s*AppActions\.shared\.isRestoringSession\s*\n"
_CANCEL_GUARD = "guard !Task.isCancelled else { return }"
_EPOCH_GUARD = "guard epoch == identityEpoch else { return }"
_REFRESH_SKIP = "guard AppActions.shared.isSignedIn else { return }"
_FOLLOW_GATE = (r"guard\s+AppActions\.shared\.isSignedIn\s+else\s*\{\s*"
                r'AppActions\.shared\.requestSignIn\(for:\s*"follow investors"\)\s*'
                r"return\s+false\s*\}")
_ACTIVATION_TASK = r"\.task\s*\(\s*id:\s*isActiveTab\s*\)"
_BLOCKED_STATES = ("notifications.state == .reconnecting", "notifications.state == .signedOut",
                   "priceAlerts.state == .reconnecting", "priceAlerts.state == .signedOut")


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the fix's own comments name `isSignedIn` and `auth.status` while explaining why
    the code no longer reads them, so an un-stripped scan for their ABSENCE fails on prose and a
    scan for a PRESENCE passes on a revert whose comment survived. A tail needs leading
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
        start = src.index("{", m.end())
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


def _writes(src: str, name: str) -> list[str]:
    """The right-hand side of every plain assignment to `name` (`==` excluded)."""
    return [w.strip() for w in re.findall(rf"\b{name}\s*=(?!=)\s*([^\n]*)", src)]


def _line_of(src: str, m: re.Match | None) -> str | None:
    """The stripped source line holding match `m` (for a legible failure message)."""
    if m is None:
        return None
    end = src.find("\n", m.end())
    return src[src.rfind("\n", 0, m.start()) + 1: end if end != -1 else len(src)].strip()


def _do_and_catch(load: str, name: str) -> tuple[str, str]:
    assert load.count("do {") == 1 and load.count("} catch {") == 1, (
        f"{name} no longer has exactly one do/catch — re-derive these scans")
    return load[load.index("do {"): load.index("} catch {")], _block(load, "} catch")


def _inbox_load() -> str:
    load = _block(_code(_INBOX), _PERFORM)
    # Anti-vacuity: the live first-page load, not a same-named stub.
    assert ("repository.fetchNotifications(limit: 30, before: nil)" in load
            and "items = page.items" in load), (
        "the inbox performLoad block is not the live first-page load — re-derive this guard")
    return load


def _store_load() -> str:
    load = _block(_code(_STORE), _PERFORM)
    assert "repository.fetchPriceAlerts(ticker: nil)" in load and "alerts = page.items" in load, (
        "the PriceAlertStore performLoad block is not the live load — re-derive this guard")
    return load


def _common_arm_checks(load: str, catch: str, who: str) -> str:
    """The checks both loads share; returns the arm. `who` prefixes every message."""
    m = re.search(_STATUS, load)
    assert not m, (
        f"the {who} load decides the gate from auth.status again (`{m and m.group(0)}`): the "
        "token is armed while the status reads .restoring, so the guard refuses a request that "
        "would have succeeded and latches Reconnecting")
    assert catch.count(_ARM) == 1, (
        f"the {who} load has no typed-refusal arm (`{_ARM}`), so a refused pass is flattened "
        "into an error the user cannot fix by retrying")
    arm = _block(catch, _ARM)
    assert load.count("isRestoringSession") == 1 and "isRestoringSession" in arm, (
        f"the {who} load reads isRestoringSession outside its typed-refusal arm — a pre-flight "
        "session read decides the gate again instead of APIClient's refusal")
    # A substring test passes on `!AppActions.shared.isRestoringSession` or `… == false`: pin
    # the whole statement, ending at the line break (auth.md §5).
    assert re.search(_RECONNECTING_READ, arm), (
        f"the {who} refusal inverts or replaces the restoring read (`let reconnecting = "
        "AppActions.shared.isRestoringSession` exactly): a restoring user would get Sign In and "
        "a signed-out user a permanent Reconnecting")
    assert arm.count(_GATE_WRITE) == 1 and len(re.findall(r"\bstate\s*=(?!=)", arm)) == 1, (
        f"the {who} refusal does not write the gate exactly once as `{_GATE_WRITE}` — a "
        "hardcoded or later write wins over the isRestoringSession decision")
    assert re.search(r"\breturn\s*\}\s*$", arm), (
        f"the {who} refusal falls through into the error state")
    fail_log = catch.find("log.error(")
    assert fail_log != -1 and catch.find("state = .error(") != -1, (
        f"the {who} load lost its real-failure log or error state — the ordering check is vacuous")
    assert catch.index(_ARM) < fail_log, (
        f"the {who} refusal is logged as a load failure: `log.error(` runs before the "
        "typed-refusal arm, so every refused launch reads as an outage in the logs")
    return arm


# ── 1. The inbox decides the gate from the typed refusal ────────────────────


def test_the_inbox_load_decides_the_gate_from_the_refusal():
    load = _inbox_load()
    _, catch = _do_and_catch(load, "the inbox performLoad")
    arm = _common_arm_checks(load, catch, "inbox")
    assert re.search(r"\bitems\s*=\s*\[\]", arm) and re.search(r"\bnextCursor\s*=\s*nil\b", arm), (
        "the inbox refusal keeps rows (or a next-page cursor) it can no longer refresh")
    assert "unreadCount" not in arm and "notificationUnreadDidChange" not in arm, (
        "the inbox refusal publishes an unread count — a read that never happened zeroes the "
        "badge, the second-writer bug AlertsTabContent documents")
    cancel_at = catch.find(_CANCEL_GUARD)
    assert cancel_at != -1 and cancel_at < catch.index(_ARM), (
        "a cancelled inbox load can draw the account gate: the catch's `guard !Task.isCancelled` "
        "no longer runs before the typed-refusal arm (APIClient wraps a cancellation, so a "
        "superseded load must bail first)")


# ── 2. The price-alert store decides the gate from the typed refusal ────────


def test_the_price_alert_load_decides_the_gate_from_the_refusal():
    load = _store_load()
    _, catch = _do_and_catch(load, "PriceAlertStore.performLoad")
    arm = _common_arm_checks(load, catch, "price-alert")
    assert re.search(r"\balerts\s*=\s*\[\]", arm), (
        "the price-alert refusal keeps rows it can no longer refresh (and the bell keeps badging)")
    assert _writes(arm, "lastLoadedAt") == ["nil"], (
        "the price-alert refusal leaves lastLoadedAt fresh (it must write exactly "
        "`lastLoadedAt = nil`), so loadIfStale suppresses the reload that heals the gate")
    epoch_at = catch.find(_EPOCH_GUARD)
    assert epoch_at != -1 and epoch_at < catch.index(_ARM), (
        "a refusal asked for under the previous identity gates the next account's price alerts: "
        "the catch's identity-epoch guard no longer runs before the typed-refusal arm")


# ── 3. Only a pass that saw the account is stamped fresh ────────────────────


def test_only_a_pass_that_saw_the_account_is_stamped_fresh():
    store = _code(_STORE)
    assert re.search(r"\bprivate\s+var\s+lastLoadedAt\s*:\s*Date\?", store), (
        "lastLoadedAt is no longer a private stored property — a stamp could come from outside "
        "this file, unseen by this scan")
    writes = _writes(store, "lastLoadedAt")
    assert writes.count("Date()") == 1 and all(w in ("Date()", "nil") for w in writes), (
        f"lastLoadedAt is stamped outside performLoad's success path (writes: {writes}) — a "
        "refused or failed pass marked fresh suppresses the reload that heals it")
    do_part, _ = _do_and_catch(_store_load(), "PriceAlertStore.performLoad")
    stamp_at = do_part.find("lastLoadedAt = Date()")
    epoch_at = do_part.find(_EPOCH_GUARD)
    assert stamp_at != -1 and epoch_at != -1 and epoch_at < stamp_at, (
        "the success path stamps lastLoadedAt before its identity-epoch guard, so an answer for "
        "the previous account marks the next one fresh")


# ── 4. No other member gates either load on a session read ──────────────────


def test_no_other_inbox_member_gates_the_load_on_a_session_read():
    """Section 1 looks only inside performLoad. The bug returns unseen if the pre-flight moves
    one level up — into `load()`, `loadAndWait()`, a helper or computed property. File-wide, the
    only session reads left are refreshUnreadCount's skip and the arm's isRestoringSession."""
    rest = _code(_INBOX)
    refresh = _block(rest, _REFRESH)
    cut, n = re.subn(re.escape(_REFRESH_SKIP), "", refresh)
    assert n == 1, (
        f"refreshUnreadCount no longer holds exactly one status skip (found {n}) — section 5 "
        "names why it exists; re-derive this scan")
    start = rest.index("{", rest.index(_REFRESH) + len(_REFRESH))
    rest = rest[:start] + cut + rest[start + len(refresh):]
    rest = _without_block(rest, _ARM)
    m = re.search(_SESSION_READ, rest)
    assert not m, (
        f"a pre-flight session read gates the inbox from another member (`{_line_of(rest, m)}`) "
        "— the token is armed while the status reads .restoring, so it refuses a request that "
        "would have succeeded")


def test_no_other_price_alert_member_gates_the_load_on_a_session_read():
    """Same scan for the store: `load()`, `loadIfStale()` and the mutations carry no session
    read today. A future TAP gate on a mutation is a deliberate choice — cut it out here by
    name, as section 6 does for WhaleService, rather than loosening this scan."""
    rest = _without_block(_code(_STORE), _ARM)
    m = re.search(_SESSION_READ, rest)
    assert not m, (
        "a pre-flight session read gates the price-alert load from another member "
        f"(`{_line_of(rest, m)}`) — loadIfStale and five detail-screen bells call through it")


# ── 5. The badge refresh's skip heals by construction ───────────────────────


def test_the_badge_refresh_skip_heals_by_construction():
    refresh = _block(_code(_INBOX), _REFRESH)
    assert "repository.fetchNotifications(limit: 1, before: nil)" in refresh, (
        "the refreshUnreadCount block is not the live badge refresh — re-derive this guard")
    assert not re.search(r"\b(state|items|nextCursor)\s*=(?!=)", refresh), (
        "refreshUnreadCount writes the inbox state — its status skip would now LATCH, so it "
        "needs a typed-refusal arm like performLoad instead of a pre-flight read")

    app = _code(_APP_STATE)
    establish = _block(app, _ESTABLISH)
    published = establish.find("auth.status = .authenticated")
    fan_out = establish.find("await onAuthenticated(")
    assert published != -1 and fan_out != -1 and published < fan_out, (
        "onAuthenticated runs before .authenticated is published, so its badge refresh is "
        "skipped by refreshUnreadCount's status check and the launch badge never refreshes")

    on_auth = _block(app, _ON_AUTH)
    transition = _without_block(on_auth, _SAME_USER)
    assert transition.count("NotificationInboxViewModel.shared.refreshUnreadCount()") == 1, (
        "the transition fan-out no longer refreshes the badge — refreshUnreadCount's launch "
        "skip is never healed")


# ── 6. The follow tap keeps its gate, and it is the file's only session read ──


def test_the_follow_tap_keeps_its_gate_ahead_of_any_optimistic_state():
    whale = _code(_WHALE)
    toggle = _block(whale, _TOGGLE)
    gate = re.search(_FOLLOW_GATE, toggle)
    assert gate, (
        "toggleFollow lost its user-tap sign-in gate — a follow fires optimistically on an "
        "unvalidated session and snaps back, instead of the sign-in prompt or the Reconnecting "
        "toast")
    first_mutation = min(i for i in (toggle.find("followedWhaleIds.insert("),
                                     toggle.find("followedWhaleIds.remove("),
                                     toggle.find("followTasks[whaleId] = Task")) if i != -1)
    assert gate.start() < first_mutation, (
        "toggleFollow's tap gate runs after its optimistic mutation — the button fills in and "
        "then snaps back, the reported bug's exact shape")

    start = whale.index("{", whale.index(_TOGGLE) + len(_TOGGLE))
    rest = whale[:start] + toggle.replace(gate.group(0), "", 1) + whale[start + len(toggle):]
    m = re.search(_SESSION_READ, rest)
    assert not m, (
        f"a session read gates WhaleService outside toggleFollow's tap gate (`{_line_of(rest, m)}`)"
        " — the service has no load to gate; TrackingViewModel.loadWhaleList owns the roster")

    request = _block(_code(_APP_STATE), _REQUEST_SIGN_IN)
    assert request.count("if hasUnusedStoredCredential {") == 1, (
        "requestSignIn prompts a restoring session to sign in — the follow tap gate would tell "
        "a signed-in user to sign in")
    restoring = _block(request, "if hasUnusedStoredCredential")
    assert ('showToast("Reconnecting your account…"' in restoring
            and re.search(r"\breturn\s*\}\s*$", restoring)
            and request.index("if hasUnusedStoredCredential") < request.index("signInPrompt =")), (
        "requestSignIn prompts a restoring session to sign in — the restoring branch must toast "
        "Reconnecting and return before `signInPrompt =`")


# ── 7. The Alerts tab heals the gate and never pre-flights it ───────────────


def test_the_alerts_tab_heals_the_gate_and_never_pre_flights_it():
    view = _block(_code(_ALERTS_TAB), _ALERTS_VIEW)
    tasks = _blocks(view, _ACTIVATION_TASK)
    assert len(tasks) == 1 and "loadAll()" in tasks[0], (
        "the Alerts activation load is gone or doubled — re-derive this scan")
    m = re.search(_SESSION_READ, tasks[0])
    assert not m, (
        f"the Alerts `.task(id: isActiveTab)` reads the session (`{m and m.group(0)}`): gating "
        "the activation load on it skips the load while the session is .restoring")

    blocked = _block(view, "private var isAuthBlocked: Bool")
    for state in _BLOCKED_STATES:
        assert state in blocked, (
            f"isAuthBlocked ignores `{state}`, so the auth-status heal skips a section still "
            "waiting on the session")
    assert re.search(r"\breturn\s+blockedNotifications\s*\|\|\s*blockedRules\b", blocked), (
        "isAuthBlocked drops a section from its answer, so the heal skips it")

    healer = _block(view, ".onChange(of: appState.auth.status)")
    assert "guard status == .authenticated, isAuthBlocked else { return }" in healer, (
        "the Alerts heal no longer fires exactly on `.authenticated` while a section is gated")

    identity = _block(view, ".reloadOnIdentityChange")
    gate_at = identity.find("guard isActive else { return }")
    assert gate_at != -1 and all(
        identity.find(r) != -1 and identity.find(r) < gate_at
        for r in ("notifications.reset()", "priceAlerts.reset()")), (
        "an identity change leaves the previous account's gate or rows in a hidden Alerts tab: "
        "both stores must reset before the `isActive` early return")


# ── Anti-vacuity ────────────────────────────────────────────────────────────


def test_comment_stripping_is_real_and_load_bearing():
    stripped = _strip_swift_comments(
        "// AppActions.shared.isSignedIn\n"
        "/* auth.status\n   isAuthenticated */\n"
        "    let x = 1  // state = .signedOut\n")
    assert not re.search(_STATUS, stripped) and ".signedOut" not in stripped
    assert "let x = 1" in stripped
    # The fix's own comment in the inbox performLoad names the removed status read. If it ever
    # stops doing so this stays harmless; while it does, it proves the stripping is what keeps
    # the absence checks honest.
    raw = _INBOX.read_text(encoding="utf-8")
    head = raw[raw.index(_PERFORM): raw.index("let page = try await repository.fetchNotifications")]
    assert re.search(_STATUS, head), (
        "the inbox performLoad comment no longer names the removed status read — fine, but then "
        "this anti-vacuity probe needs a new raw sample")
    assert not re.search(_STATUS, _strip_swift_comments(head))


@pytest.mark.parametrize("path,header,minimum", [
    (_INBOX, _PERFORM, 400),
    (_INBOX, _REFRESH, 300),
    (_STORE, _PERFORM, 400),
    (_WHALE, _TOGGLE, 400),
    (_APP_STATE, _ESTABLISH, 120),
    (_APP_STATE, _ON_AUTH, 400),
    (_APP_STATE, _REQUEST_SIGN_IN, 150),
    (_ALERTS_TAB, _ALERTS_VIEW, 2000),
])
def test_each_scan_is_bounded_to_its_declaration(path, header, minimum):
    src = _code(path)
    block = _block(src, header)
    assert len(block) > minimum, f"`{header}` block is only {len(block)} chars — the scan drifted"
    assert len(block) < len(src) // 2 or header == _ALERTS_VIEW, (
        f"`{header}` block is {len(block)} of {len(src)} chars — `_block` stopped bounding")


# ── The mutations above, re-run in memory on every pass ─────────────────────

_I_DO = ("        do {\n"
         "            let page = try await repository.fetchNotifications(limit: 30, before: nil)\n")
_I_ARM_RETURN = ("                // bug documented in `AlertsTabContent` wearing different clothes.\n"
                 "                return\n")
_I_ROWS = "                items = []\n                nextCursor = nil\n"
_I_CATCH_HEAD = ("        } catch {\n            guard !Task.isCancelled else { return }\n"
                 "            // ⚠️ `catch is CancellationError`")
_I_ARM_HEAD = "            let appError = AppError.from(error)\n            if case .signInRequired = appError {"
_I_LOAD = "    func load() {\n        loadTask?.cancel()\n"
_I_LOAD_AND_WAIT = "    func loadAndWait() async {\n        load()\n"
_I_REFRESH_SKIP = ("        guard AppActions.shared.isSignedIn else { return }\n"
                   "        // A full page load is authoritative")

_S_DO = ("        let epoch = identityEpoch\n        do {\n"
         "            let page = try await repository.fetchPriceAlerts(ticker: nil)\n")
_S_CATCH_EPOCH = ("            // not gate the next account's list.\n"
                  "            guard epoch == identityEpoch else { return }\n")
_S_ARM_RETURN = "                return\n            }\n            // A failure that is never logged"
_S_ROWS = "                alerts = []\n                let reconnecting"
_S_ARM_STAMP = "                lastLoadedAt = nil\n                // The designed path"
_S_ARM_HEAD = "            let appError = AppError.from(error)\n            // Signed out and broken"
_S_STALE = ("        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }\n"
            "        await load()\n")
_S_DO_GUARD = "            guard epoch == identityEpoch else { return }\n            alerts = page.items\n"
_S_DO_STAMP = "            state = .loaded\n            lastLoadedAt = Date()\n"
_S_LOAD_IF_STALE = "    func loadIfStale(maxAge: TimeInterval = PriceAlertStore.stalenessWindow) async {\n"
_S_LOAD = "    func load() async {\n        if let running = loadTask"

_A_ESTABLISH = ("        auth.status = .authenticated\n        cancelRestoreBackoff()\n"
                "        await onAuthenticated(userId: userId, identity: identity)\n")
_A_FAN_OUT = "        Task { await NotificationInboxViewModel.shared.refreshUnreadCount() }\n"
_A_RESTORING_TOAST = ("        if hasUnusedStoredCredential {\n"
                      '            Task { await restoreSessionIfNeeded(trigger: "sign-in-requested") }\n')

_W_GATE = ("        guard AppActions.shared.isSignedIn else {\n"
           '            AppActions.shared.requestSignIn(for: "follow investors")\n'
           "            return false\n        }\n")
_W_OPTIMISTIC = ("        if newFollowing {\n            followedWhaleIds.insert(whaleId)\n"
                 "        } else {\n            followedWhaleIds.remove(whaleId)\n        }\n")
_W_SYNC = "    func syncFromAPIResponse(_ whales: [TrendingWhale], asOf epoch: Int) {\n"

_T_TASK = ("        .task(id: isActiveTab) {\n            guard isActiveTab else { return }\n"
           "            await loadAll()\n        }\n")
_T_RULES = ("        let blockedRules = priceAlerts.state == .reconnecting"
            " || priceAlerts.state == .signedOut\n")
_T_NOTIFS = ("        let blockedNotifications = notifications.state == .reconnecting\n"
             "            || notifications.state == .signedOut\n")
_T_IDENTITY = ("            notifications.reset()\n            priceAlerts.reset()\n"
               "            guard isActive else { return }\n")

_T1 = test_the_inbox_load_decides_the_gate_from_the_refusal
_T2 = test_the_price_alert_load_decides_the_gate_from_the_refusal
_T3 = test_only_a_pass_that_saw_the_account_is_stamped_fresh
_T4a = test_no_other_inbox_member_gates_the_load_on_a_session_read
_T4b = test_no_other_price_alert_member_gates_the_load_on_a_session_read
_T5 = test_the_badge_refresh_skip_heals_by_construction
_T6 = test_the_follow_tap_keeps_its_gate_ahead_of_any_optimistic_state
_T7 = test_the_alerts_tab_heals_the_gate_and_never_pre_flights_it

_GUARD = "guard AppActions.shared.isSignedIn else { return }\n"

# (name, file, ((anchor, replacement), ...), guard, the assertion message it must fail WITH).
# Every anchor must occur EXACTLY once at the moment it is applied; edits apply in order.
_MUTATIONS = [
    # ── 1. the inbox refusal arm ──
    ("I1-status-guard-back", _INBOX, ((_I_DO, "        " + _GUARD + _I_DO),),
     _T1, "the inbox load decides the gate from auth.status again"),
    ("I1b-softened-status-guard", _INBOX,
     ((_I_DO, "        guard AppActions.shared.isSignedIn || AppActions.shared.isRestoringSession "
              "else { return }\n" + _I_DO),),
     _T1, "the inbox load decides the gate from auth.status again"),
    ("I2-restoring-pre-flight", _INBOX,
     ((_I_DO, "        guard !AppActions.shared.isRestoringSession else { return }\n" + _I_DO),),
     _T1, "the inbox load reads isRestoringSession outside its typed-refusal arm"),
    ("I3-arm-disabled", _INBOX,
     (("if case .signInRequired = appError {", "if false, case .signInRequired = appError {"),),
     _T1, "the inbox load has no typed-refusal arm"),
    ("I4-arm-falls-through", _INBOX,
     ((_I_ARM_RETURN, "                // bug documented in `AlertsTabContent` wearing different "
                      "clothes.\n"),),
     _T1, "the inbox refusal falls through into the error state"),
    ("I5-inverted-restoring-read", _INBOX,
     (("let reconnecting = AppActions.shared.isRestoringSession",
       "let reconnecting = !AppActions.shared.isRestoringSession"),),
     _T1, "the inbox refusal inverts or replaces the restoring read"),
    ("I6-hardcoded-signed-out", _INBOX, ((_GATE_WRITE, "state = .signedOut"),),
     _T1, "the inbox refusal does not write the gate exactly once"),
    ("I7-gate-overwritten", _INBOX,
     (("                " + _GATE_WRITE + "\n",
       "                " + _GATE_WRITE + "\n                state = .signedOut\n"),),
     _T1, "the inbox refusal does not write the gate exactly once"),
    ("I8-keeps-rows", _INBOX, ((_I_ROWS, "                nextCursor = nil\n"),),
     _T1, "the inbox refusal keeps rows"),
    ("I8b-keeps-cursor", _INBOX, ((_I_ROWS, "                items = []\n"),),
     _T1, "the inbox refusal keeps rows (or a next-page cursor)"),
    ("I9-refusal-publishes-badge", _INBOX,
     ((_I_ARM_RETURN, "                AppState.notificationUnreadDidChange(0)\n" + _I_ARM_RETURN),),
     _T1, "the inbox refusal publishes an unread count"),
    ("I10-cancelled-load-gates", _INBOX,
     ((_I_CATCH_HEAD, "        } catch {\n            // ⚠️ `catch is CancellationError`"),),
     _T1, "a cancelled inbox load can draw the account gate"),
    ("I11-refusal-logged-as-failure", _INBOX,
     ((_I_ARM_HEAD, "            let appError = AppError.from(error)\n"
                    '            log.error("load notifications failed")\n'
                    "            if case .signInRequired = appError {"),),
     _T1, "the inbox refusal is logged as a load failure"),
    # ── 2. the price-alert refusal arm ──
    ("S1-status-guard-back", _STORE, ((_S_DO, "        " + _GUARD + _S_DO),),
     _T2, "the price-alert load decides the gate from auth.status again"),
    ("S2-restoring-pre-flight", _STORE,
     ((_S_DO, "        guard !AppActions.shared.isRestoringSession else { return }\n" + _S_DO),),
     _T2, "the price-alert load reads isRestoringSession outside its typed-refusal arm"),
    ("S3-arm-disabled", _STORE,
     (("if case .signInRequired = appError {", "if false, case .signInRequired = appError {"),),
     _T2, "the price-alert load has no typed-refusal arm"),
    ("S4-refusal-before-epoch-guard", _STORE,
     ((_S_CATCH_EPOCH, "            // not gate the next account's list.\n"),),
     _T2, "a refusal asked for under the previous identity gates the next account"),
    ("S5-arm-falls-through", _STORE,
     ((_S_ARM_RETURN, "            }\n            // A failure that is never logged"),),
     _T2, "the price-alert refusal falls through into the error state"),
    ("S6-inverted-restoring-read", _STORE,
     (("let reconnecting = AppActions.shared.isRestoringSession",
       "let reconnecting = !AppActions.shared.isRestoringSession"),),
     _T2, "the price-alert refusal inverts or replaces the restoring read"),
    # The old backstop: a refusal that always says "signed out".
    ("S7-hardcoded-signed-out", _STORE, ((_GATE_WRITE, "state = .signedOut"),),
     _T2, "the price-alert refusal does not write the gate exactly once"),
    ("S8-keeps-rows", _STORE, ((_S_ROWS, "                let reconnecting"),),
     _T2, "the price-alert refusal keeps rows"),
    ("S9-refusal-stamped-fresh", _STORE,
     ((_S_ARM_STAMP, "                lastLoadedAt = Date()\n                // The designed path"),),
     _T2, "the price-alert refusal leaves lastLoadedAt fresh"),
    ("S9b-refusal-keeps-old-stamp", _STORE,
     ((_S_ARM_STAMP, "                // The designed path"),),
     _T2, "the price-alert refusal leaves lastLoadedAt fresh"),
    ("S10-refusal-logged-as-failure", _STORE,
     ((_S_ARM_HEAD, "            let appError = AppError.from(error)\n"
                    '            log.error("load price alerts failed")\n'
                    "            // Signed out and broken"),),
     _T2, "the price-alert refusal is logged as a load failure"),
    # ── 3. the freshness stamp ──
    ("S11-stamp-in-load-if-stale", _STORE,
     ((_S_STALE, _S_STALE + "        lastLoadedAt = Date()\n"),),
     _T3, "lastLoadedAt is stamped outside performLoad's success path"),
    ("S12-stamp-before-epoch-guard", _STORE,
     ((_S_DO_GUARD, "            lastLoadedAt = Date()\n" + _S_DO_GUARD),
      (_S_DO_STAMP, "            state = .loaded\n")),
     _T3, "the success path stamps lastLoadedAt before its identity-epoch guard"),
    ("S12b-stamp-made-visible", _STORE,
     (("    private var lastLoadedAt: Date?", "    var lastLoadedAt: Date?"),),
     _T3, "lastLoadedAt is no longer a private stored property"),
    # ── 4. a pre-flight moved one level up ──
    ("I12-pre-flight-in-load", _INBOX,
     ((_I_LOAD, "    func load() {\n        " + _GUARD + "        loadTask?.cancel()\n"),),
     _T4a, "a pre-flight session read gates the inbox from another member"),
    ("I13-restoring-pre-flight-in-load-and-wait", _INBOX,
     ((_I_LOAD_AND_WAIT, "    func loadAndWait() async {\n"
                         "        guard !AppActions.shared.isRestoringSession else { return }\n"
                         "        load()\n"),),
     _T4a, "a pre-flight session read gates the inbox from another member"),
    ("I14-second-refresh-skip", _INBOX,
     ((_I_REFRESH_SKIP, "        " + _GUARD + _I_REFRESH_SKIP),),
     _T4a, "refreshUnreadCount no longer holds exactly one status skip"),
    ("S13-pre-flight-in-load-if-stale", _STORE,
     ((_S_LOAD_IF_STALE, _S_LOAD_IF_STALE + "        " + _GUARD),),
     _T4b, "a pre-flight session read gates the price-alert load from another member"),
    ("S14-pre-flight-in-load", _STORE,
     ((_S_LOAD, "    func load() async {\n        " + _GUARD + "        if let running = loadTask"),),
     _T4b, "a pre-flight session read gates the price-alert load from another member"),
    # ── 5. the badge refresh's skip stays safe ──
    ("R1-refresh-latches-state", _INBOX,
     ((_I_REFRESH_SKIP, "        guard AppActions.shared.isSignedIn else { state = .reconnecting; "
                        "return }\n        // A full page load is authoritative"),),
     _T5, "refreshUnreadCount writes the inbox state"),
    ("R2-fan-out-before-authenticated", _APP_STATE,
     ((_A_ESTABLISH, "        await onAuthenticated(userId: userId, identity: identity)\n"
                     "        auth.status = .authenticated\n        cancelRestoreBackoff()\n"),),
     _T5, "onAuthenticated runs before .authenticated is published"),
    ("R3-fan-out-drops-badge", _APP_STATE, ((_A_FAN_OUT, ""),),
     _T5, "the transition fan-out no longer refreshes the badge"),
    # ── 6. the follow tap gate ──
    ("W1-tap-gate-removed", _WHALE, ((_W_GATE, ""),),
     _T6, "toggleFollow lost its user-tap sign-in gate"),
    ("W1b-tap-gate-silent", _WHALE,
     (('            AppActions.shared.requestSignIn(for: "follow investors")\n', ""),),
     _T6, "toggleFollow lost its user-tap sign-in gate"),
    ("W2-tap-gate-after-optimistic-state", _WHALE,
     ((_W_GATE, ""), (_W_OPTIMISTIC, _W_OPTIMISTIC + _W_GATE)),
     _T6, "toggleFollow's tap gate runs after its optimistic mutation"),
    ("W3-session-read-elsewhere", _WHALE,
     ((_W_SYNC, _W_SYNC + "        " + _GUARD),),
     _T6, "a session read gates WhaleService outside toggleFollow's tap gate"),
    ("W4-sign-in-prompt-during-restore", _APP_STATE,
     ((_A_RESTORING_TOAST, _A_RESTORING_TOAST.replace("if hasUnused", "if false, hasUnused")),),
     _T6, "requestSignIn prompts a restoring session to sign in"),
    # ── 7. the Alerts tab ──
    ("T1-activation-reads-session", _ALERTS_TAB,
     ((_T_TASK, _T_TASK.replace("guard isActiveTab else",
                                "guard isActiveTab, appState.auth.isAuthenticated else")),),
     _T7, "the Alerts `.task(id: isActiveTab)` reads the session"),
    ("T2-activation-load-gone", _ALERTS_TAB, ((_T_TASK, ""),),
     _T7, "the Alerts activation load is gone or doubled"),
    ("T3-blocked-ignores-signed-out-rules", _ALERTS_TAB,
     ((_T_RULES, "        let blockedRules = priceAlerts.state == .reconnecting\n"),),
     _T7, "isAuthBlocked ignores `priceAlerts.state == .signedOut`"),
    ("T3b-blocked-ignores-reconnecting-inbox", _ALERTS_TAB,
     ((_T_NOTIFS, "        let blockedNotifications = notifications.state == .signedOut\n"),),
     _T7, "isAuthBlocked ignores `notifications.state == .reconnecting`"),
    ("T3c-blocked-drops-a-section", _ALERTS_TAB,
     (("        return blockedNotifications || blockedRules\n",
       "        return blockedNotifications\n"),),
     _T7, "isAuthBlocked drops a section from its answer"),
    ("T4-heal-not-on-authenticated", _ALERTS_TAB,
     (("guard status == .authenticated, isAuthBlocked else { return }",
       "guard status != .unauthenticated, isAuthBlocked else { return }"),),
     _T7, "the Alerts heal no longer fires exactly on `.authenticated`"),
    ("T5-identity-reset-after-active-gate", _ALERTS_TAB,
     ((_T_IDENTITY, "            guard isActive else { return }\n"
                    "            notifications.reset()\n            priceAlerts.reset()\n"),),
     _T7, "an identity change leaves the previous account's gate or rows in a hidden Alerts tab"),
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
    for guard in (_T1, _T2, _T3, _T4a, _T4b, _T5, _T6, _T7):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"
    names = [m[0] for m in _MUTATIONS]
    assert len(names) == len(set(names)), "duplicate mutation ids"
