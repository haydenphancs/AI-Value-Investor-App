"""One credit balance on iOS, re-read on every money event (2026-10-12).

WHY THIS EXISTS. The owner saw "Monthly Credits 4/50" after four 1-credit chat turns. That figure
was right (4 USED of 50), but the audit behind it found the app kept TWO balances:
`AppState.user.credits` and `ResearchViewModel.creditBalance`. The Research tab PREFERRED its own
copy, which only its own reads updated. A chat turn, the report screen's paid door
(`GET /stocks/{t}/report`, which precharges on a cache miss) and the research precharge all moved
the real balance, and the Research badge, card and Generate gate kept the old one for up to five
minutes (indefinitely under a cover). A reset at month end, a refund push, a streaming 402 and an
abandoned chat turn re-read nothing at all.

What is pinned (iOS source; `ResearchViewModel.swift` and `AppState.swift` unless named):

 1. `AppState.refreshCredits()` joins a read in flight AND re-runs once for a call that landed
    during it (`creditsDemand`), bounded by `maxCreditsRefreshRounds`.
 2. Every write of a balance into `user.credits` is fenced by identity and by SEND order, and
    only `performCreditsRefresh` / `adoptCredits` write one, anywhere in the tree.
 3. Research's own pinned GET takes a send-order ticket before it goes out and writes its answer
    through to AppState after its pinned apply; a refused answer mirrors the balance that won.
 4. `BackendCreditsResponse` carries every `CreditInfo` field, so the write-through cannot
    change the Account card's "4/50" (granted_total / granted_used).
 5. The Research tab DISPLAYS AppState only and mirrors it into the ViewModel without a request.
 6. The generation monitor re-reads the balance at `.started` (the precharge) and at poll timeout.
 7. The Reports list notices credit movement synchronously after its assignment: refunded,
    refund confirmed, newly failed (+ one settle re-read when charged, whatever `is_refunded`
    says), a row this device did not start, an in-flight row that vanished elsewhere.
 8. The paid report door (Path B) re-reads on every outcome.
 9. A billable GET is never auto-resent (5xx retry, DEBUG failover).
10. Foreground re-reads a STALE balance without adding a launch duplicate.
11. Research pushes (complete / failed) re-read on arrival and on tap.
12. Chat re-reads after a streaming 402 and after a turn it stopped listening to.
13. A research shortfall reaches the GLOBAL error host (Buy Credits), and a retry whose refund is
    still pending is never upsold.
14. Profile reads through AppState and still surfaces a real failure.
15. The settle re-read is delayed, coalesced, RE-ARMED by a call that lands while it is pending
    (one extra full-delay round, bounded) and session-gated.
16. No balance lands for a session that had ended: a read SENT or ANSWERED while signed out is
    dropped by both writers. Sign-out used to keep the ended account's token armed through its
    unregister + logout round trips, so a reload the sign-out triggered read THAT account's
    balance under the new identity generation — the identity fence cannot see it. Since
    2026-10-13 `AuthService.beginSignOut()` disarms synchronously, before the identity moves
    (`test_signout_and_session_hygiene.py` §5); both drops stay for a refresh in flight that
    re-arms the ended token until sign-out's backstop clears it.

House style (`.claude/rules/testing.md` §3): comments are stripped before every assertion, every
scan is brace-bound to its declaration and asserts presence before slicing, and the in-memory
mutation table below (``pathlib.Path.read_text`` monkeypatched for one file — the Swift files are
never rewritten; other sessions read them concurrently) runs on every pass: each mutation must
fail WITH the assertion message that names it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_ACTIONS = _IOS / "Core" / "State" / "AppActions.swift"
_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_CONTENT = _IOS / "ContentView.swift"
_POLL = _IOS / "Core" / "Services" / "TaskPollingManager.swift"
_REPORT_VM = _IOS / "ViewModels" / "TickerReportViewModel.swift"
_ENDPOINT = _IOS / "Core" / "Services" / "APIEndpoint.swift"
_CLIENT = _IOS / "Core" / "Services" / "APIClient.swift"
_APP = _IOS / "iosApp.swift"
_DELEGATE = _IOS / "Core" / "AppDelegate.swift"
_PUSH = _IOS / "Core" / "Services" / "PushNotificationManager.swift"
_CHAT = _IOS / "ViewModels" / "ChatViewModel.swift"
_PROFILE_VM = _IOS / "ViewModels" / "ProfileViewModel.swift"
_KINDS = _REPO / "backend" / "app" / "services" / "notification_kinds.py"

_REFRESH = "func refreshCredits() async -> AppError?"
_PERFORM = "private func performCreditsRefresh() async -> AppError?"
_ADOPT = "func adoptCredits(_ credits: CreditInfo, from ticket: CreditsReadTicket) -> CreditsAdoption"
_IF_STALE = "func refreshCreditsIfStale("
_SETTLE = "func refreshCreditsAfterSettle("
_LOAD_CREDITS = "func loadCredits() async"
_LOAD_REPORTS = "func loadReports() async"
_NOTE = "private func noteCreditMovingChanges(in rows: [BackendReportListItem])"
_IDENTITY = "func handleIdentityChange(isActiveTab: Bool) async"
_GENERATE = "func generateAnalysis()"
_RETRY = "func retryReport(_ report: AnalysisReport)"


# ── helpers ──────────────────────────────────────────────────────────────────

def _strip(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails (a tail needs
    leading whitespace, so a `https://` inside a string literal is not cut). Load-bearing: the
    fixes' own comments name every token asserted below."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", raw))
    return "\n".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip(path.read_text(encoding="utf-8"))


def _balanced(src: str, start: int, open_: str = "{", close: str = "}") -> str:
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced `{open_}{close}` from offset {start}")


def _block(src: str, header: str) -> str:
    """The balanced `{…}` body opened by the first `{` after the ONLY `header`."""
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    return _balanced(src, src.index("{", src.index(header) + len(header)))


def _span(src: str, header: str) -> tuple[int, int]:
    """[start, end) of `header`'s declaration through its closing brace."""
    at = src.index(header)
    body = _block(src, header)
    start = src.index("{", at + len(header))
    return at, start + len(body)


def _else_block(src: str, guard: str) -> str:
    """The `else { … }` body of the guard that begins with `guard` (a literal prefix)."""
    at = src.index(guard)
    return _balanced(src, src.index("{", src.index("else", at)))


# ── 1. refreshCredits re-runs for a call that lands mid-read ─────────────────


def test_refresh_credits_reruns_for_a_call_that_lands_mid_read():
    body = _block(_code(_APP_STATE), _REFRESH)
    assert "performCreditsRefresh()" in body and "creditsTask = task" in body, (
        "refreshCredits is not the live single-flight read — this guard reads the wrong code")
    demand = body.find("creditsDemand &+= 1")
    join = body.find("if let running = creditsTask")
    assert demand != -1 and join != -1 and demand < join, (
        "refreshCredits no longer counts a JOINER's call: a call made right after a charge joins "
        "a read sent before it and gets the pre-charge balance")
    task = _block(body, "let task = Task")
    loop = re.search(r"for\s+round\s+in\s+1\.\.\.Self\.maxCreditsRefreshRounds\s*\{", task)
    assert loop, "the refresh task no longer loops (bounded by maxCreditsRefreshRounds) for late callers"
    covered = task.find("let covered = self.creditsDemand")
    read = task.find("await self.performCreditsRefresh()")
    rerun = task.find("if covered == self.creditsDemand { break }")
    assert -1 < covered < read < rerun, (
        "the re-run check is broken: `let covered` must be read BEFORE the GET and compared "
        "AFTER it, or a call that landed during the GET is never re-run")
    m = re.search(r"static\s+let\s+maxCreditsRefreshRounds\s*=\s*(\d+)", _code(_APP_STATE))
    assert m and 2 <= int(m.group(1)) <= 5, "maxCreditsRefreshRounds is gone or unbounded"


# ── 2. every balance write is send-ordered and identity-fenced ────────────────


def _assert_fenced_write(body: str, identity_guard: str, order_guard: str, applied: str, who: str):
    write = body.find("user.credits =")
    assert write != -1, f"{who} no longer writes user.credits — re-derive this scan"
    for guard, what in ((identity_guard, "identity"), (order_guard, "send-order")):
        at = body.find(guard)
        assert at != -1 and at < write, (
            f"{who} writes user.credits without its {what} guard before the write (`{guard}`)")
    assert body.find(applied) > write, f"{who} does not record the applied send order after its write"


def test_every_balance_write_is_send_ordered_and_identity_fenced():
    app = _code(_APP_STATE)
    perform = _block(app, _PERFORM)
    request = perform.find("apiClient.request(")
    assert request != -1, "performCreditsRefresh no longer sends the read — re-derive this scan"
    for capture in ("let identity = identityGeneration", "creditsReadSeq &+= 1", "let seq = creditsReadSeq"):
        at = perform.find(capture)
        assert at != -1 and at < request, (
            f"performCreditsRefresh takes `{capture}` after the request was sent (or not at all) — "
            "a read must be stamped when it is SENT")
    _assert_fenced_write(perform, "guard identity == identityGeneration", "guard seq > creditsAppliedSeq",
                         "creditsAppliedSeq = seq", "performCreditsRefresh")
    adopt = _block(app, _ADOPT)
    _assert_fenced_write(adopt, "guard ticket.identity == identityGeneration",
                         "guard ticket.seq > creditsAppliedSeq", "creditsAppliedSeq = ticket.seq",
                         "adoptCredits")
    begin = _block(app, "func beginCreditsRead() -> CreditsReadTicket")
    assert "creditsReadSeq &+= 1" in begin and "identity: identityGeneration" in begin, (
        "beginCreditsRead no longer stamps the ticket with the send order and identity")

    # Tree-wide: a balance (any non-nil right-hand side) is written into user.credits ONLY by the
    # two fenced methods. A clear (`= nil`) is a sign-out path and stays allowed.
    allowed = [_span(app, _PERFORM), _span(app, _ADOPT)]
    offenders = []
    for path in sorted(_IOS.rglob("*.swift")):
        src = _strip(path.read_text(encoding="utf-8"))
        for m in re.finditer(r"\buser\.credits\s*=(?!=)\s*([^\n]*)", src):
            if m.group(1).strip().startswith("nil"):
                continue
            if path == _APP_STATE and any(a <= m.start() < b for a, b in allowed):
                continue
            offenders.append(f"{path.relative_to(_IOS)}: {m.group(0).strip()}")
    assert not offenders, (
        "a balance is written into user.credits outside performCreditsRefresh/adoptCredits — "
        f"unordered and unfenced: {offenders}")


# ── 3. Research's pinned GET writes through after its pinned apply ───────────


def test_research_load_credits_writes_through_after_its_pinned_apply():
    credits = _block(_code(_VM), _LOAD_CREDITS)
    request = credits.find("apiClient.request(")
    assert request != -1 and ".getUserCredits" in credits, (
        "loadCredits is not the live credits load — re-derive this scan")
    ticket = credits.find("let ticket = AppActions.shared.beginCreditsRead()")
    epoch = credits.find("let epoch = identityEpoch")
    assert ticket != -1 and ticket < epoch and ticket < request, (
        "loadCredits takes its send-order ticket after the request went out (or not at all) — "
        "AppState cannot order an answer it was never told the send time of")
    apply_ = credits.find("self.creditBalance = CreditBalance.from(backendCredits)")
    guard = credits.find("guard epoch == identityEpoch else")
    through = credits.find("AppActions.shared.adoptCredits(CreditInfo(backendCredits), from: ticket)")
    assert through != -1 and through > apply_ > guard > request, (
        "loadCredits no longer writes its answer through to AppState after its pinned, "
        "epoch-guarded apply — the displayed balance never learns what this tab read")
    arm = re.search(r"if\s+case\s+\.superseded\(let\s+current\)\s*=\s*AppActions\.shared\.adoptCredits\(", credits)
    assert arm, "loadCredits ignores a refused write-through and keeps the value AppState rejected"
    mirror = _balanced(credits, credits.index("{", arm.end()))
    assert re.search(r"self\.creditBalance\s*=\s*current\.map\b", mirror), (
        "loadCredits ignores a refused write-through and keeps the value AppState rejected")


# ── 4. the DTO carries every CreditInfo field ────────────────────────────────


def _struct_body(src: str, name: str) -> str:
    m = re.search(rf"^[ \t]*struct[ \t]+{name}\b[^{{\n]*\{{", src, re.M)
    assert m, f"struct {name} not found — re-derive this scan"
    return _balanced(src, m.end() - 1)


def _coding_keys(struct: str) -> set[str]:
    enum = _block(struct, "enum CodingKeys: String, CodingKey")
    keys: set[str] = set()
    for line in enum.splitlines():
        line = line.strip()
        if not line.startswith("case "):
            continue
        for item in line[5:].split(","):
            item = item.strip()
            if not item:
                continue
            m = re.fullmatch(r'(\w+)\s*=\s*"([^"]+)"', item)
            keys.add(m.group(2) if m else item)
    return keys


def test_the_backend_credits_dto_carries_every_credit_info_field():
    dto_src = _code(_POLL)
    info = _coding_keys(_struct_body(_code(_APP_STATE), "CreditInfo"))
    dto = _coding_keys(_struct_body(dto_src, "BackendCreditsResponse"))
    assert {"granted_total", "granted_used"} <= info, "CreditInfo lost its granted-pool keys — re-derive"
    missing = info - dto
    assert not missing, (
        f"BackendCreditsResponse does not decode {sorted(missing)}: the Research write-through "
        "would drop them and the Account card's used/total ('4/50') would change")
    decoder = _block(dto_src, "extension BackendCreditsResponse: Decodable")
    for prop in ("grantedTotal", "grantedUsed"):
        assert re.search(rf"self\.{prop}\s*=\s*try\s+container\.decodeIfPresent\(", decoder), (
            f"BackendCreditsResponse does not decode {sorted({prop})}: the Research write-through "
            "would drop them and the Account card's used/total ('4/50') would change")
    conv = _block(dto_src, "init(_ response: BackendCreditsResponse)")
    props = re.findall(r"^\s*let\s+(\w+)\s*:", _struct_body(_code(_APP_STATE), "CreditInfo"), re.M)
    assert len(props) >= 8, "CreditInfo's stored properties could not be read — re-derive"
    dropped = [p for p in props if not re.search(rf"\b{p}:\s*response\.{p}\b", conv)]
    assert not dropped, (
        f"CreditInfo.init(_: BackendCreditsResponse) does not pass {dropped} through — the "
        "write-through would change the Account card's used/total")


# ── 5. the Research tab displays AppState and mirrors it without a request ───


def test_the_research_tab_mirrors_app_state_without_a_request():
    content = _code(_CONTENT)
    view = _block(content, "struct ResearchViewWithBinding: View")
    body = _block(view, "var body: some View")
    m = re.search(r"\.onChange\(of:\s*appState\.user\.credits,\s*initial:\s*true\)", body)
    assert m, "the Research tab no longer mirrors AppState's balance into its ViewModel"
    closure = _balanced(body, body.index("{", m.end()))
    assert "viewModel.adoptSharedCredits(" in closure, (
        "the Research tab no longer mirrors AppState's balance into its ViewModel")
    shown = _block(view, "private var effectiveCreditBalance: CreditBalance?")
    assert "appState.user.credits" in shown and "viewModel.creditBalance" not in shown, (
        "the Research card/badge displays the ViewModel's own copy again — a chat turn or the "
        "paid report door moves the real balance and this tab keeps the old one")
    adopt = _block(_code(_VM), "func adoptSharedCredits(_ info: CreditInfo?)")
    assert "creditBalance =" in adopt, "adoptSharedCredits no longer mirrors — re-derive"
    for token in ("apiClient", "AppActions", "await", "Task", "loadCredits", "refreshCredits"):
        assert token not in adopt, (
            f"adoptSharedCredits does more than assign (`{token}`): a mirror that calls back into "
            "AppState loops, and one that sends a request doubles every balance write")
    info = re.search(r"struct\s+CreditInfo\s*:\s*([^{]*)\{", _code(_APP_STATE))
    assert info and "Equatable" in info.group(1), "CreditInfo is not Equatable — `.onChange` cannot observe it"


# ── 6. the monitor reads the balance at start and at poll timeout ────────────


def test_the_monitor_reads_the_balance_at_start_and_at_poll_timeout():
    gen = _block(_code(_VM), _GENERATE)
    start = gen.find("case .started(let taskId):")
    end = gen.find("case .progress(", start)
    assert -1 < start < end, "the monitor's .started arm moved — re-derive this scan"
    started = gen[start:end]
    reports = started.find("await self.loadReports()")
    credits = started.find("await self.loadCredits()")
    assert -1 < reports < credits, (
        "the monitor's .started arm no longer re-reads the balance: the POST just precharged "
        "the report and the badge shows the pre-charge number until the run ends")
    arm = _block(gen, "if case .timeout = appError, startedId != nil")
    poll = arm.find("self.startReportsPolling()")
    assert -1 < poll < arm.find("await self.loadCredits()"), (
        "the poll-timeout arm no longer re-reads the balance after re-arming the list poll")


# ── 7. the Reports list notices credit movement after assigning ───────────────


def test_the_reports_list_notices_credit_movement_after_assigning():
    vm = _code(_VM)
    load = _block(vm, _LOAD_REPORTS)
    do_part = load[load.index("do {"): load.index("} catch {")]
    assign = do_part.find("self.reports = backendReports")
    note = do_part.find("noteCreditMovingChanges(in: backendReports)")
    assert assign != -1 and note > assign, (
        "loadReports no longer notices credit movement AFTER assigning the list")
    assert re.search(re.escape(_NOTE) + r"\s*\{", vm), (
        "noteCreditMovingChanges is no longer synchronous — it runs where nothing may suspend")
    body = _block(vm, _NOTE)
    for token in ("await", "Task {"):
        assert token not in body, (
            f"noteCreditMovingChanges suspends or spawns (`{token}`) inside loadReports' do-block")
    guard = body.find("guard let before = reportCreditMarks else { return }")
    first_cmp = body.find("before[")
    assert -1 < guard < first_cmp, (
        "the first list after a launch or an identity change triggers a refresh — it must only record")
    for cmp_, what in (("mark.refunded && !old.refunded", "a newly refunded row"),
                       ("mark.confirmed == true && old.confirmed != true", "a newly confirmed refund"),
                       ("mark.failed && !old.failed", "a newly failed row")):
        assert cmp_ in body, f"noteCreditMovingChanges no longer re-reads on {what}"
    failed = _block(body, "if mark.failed && !old.failed")
    assert re.search(r"if\s+mark\.charged\s*\{\s*settle\s*=\s*true\s*\}", failed), (
        "a newly failed CHARGED row no longer schedules the settle re-read, or it is gated on "
        "is_refunded — the failure is visible a round trip before its refund commits")
    assert "inFlightReportIds.contains" in body and "dismissedReportIds.contains" in body, (
        "noteCreditMovingChanges no longer re-reads for another device's report or a row deleted elsewhere")
    assert "AppActions.shared.refreshCredits()" in body and (
        "AppActions.shared.refreshCreditsAfterSettle()" in body), (
        "noteCreditMovingChanges no longer re-reads the shared balance")
    mark = _block(vm, "private struct ReportCreditMark: Equatable")
    assert "let charged: Bool" in mark and "creditsCharged" in body, (
        "a newly failed CHARGED row no longer schedules the settle re-read, or it is gated on "
        "is_refunded — the failure is visible a round trip before its refund commits")
    # Degraded input: an older backend (or a legacy row) sends no credits_charged. Unknown must
    # count as charged — one extra re-read — never as "nothing to settle".
    assert re.search(r"charged:\s*\(row\.creditsCharged\s*\?\?\s*[1-9]\d*\)\s*>\s*0", body), (
        "a row with no credits_charged is treated as uncharged — its refund is never re-read")
    ident = _block(vm, _IDENTITY)
    reset = ident.find("reportCreditMarks = nil")
    assert -1 < reset < ident.find("guard isActiveTab else { return }"), (
        "handleIdentityChange keeps the previous account's credit marks — the next account's "
        "first list is diffed against the last one's rows")


# ── 8. the paid report door re-reads on every outcome ────────────────────────


def test_path_b_rereads_the_wallet_on_every_outcome():
    fetch = _block(_code(_REPORT_VM), "private func _fetchReport(")
    call = ".getTickerReport("
    assert fetch.count(call) == 1, "Path B moved — re-derive this scan"
    do_at = fetch.rfind("do {", 0, fetch.index(call))
    do_body = _balanced(fetch, fetch.index("{", do_at))
    after_do = fetch.index("{", do_at) + len(do_body)
    m = re.match(r"\s*catch\s*\{", fetch[after_do:])
    assert m, "Path B's do/catch moved — re-derive this scan"
    catch_open = after_do + m.end() - 1
    catch_end = catch_open + len(_balanced(fetch, catch_open))
    refresh = "AppActions.shared.refreshCredits()"
    assert fetch.count(refresh) == 1 and fetch.index(refresh) >= catch_end, (
        "Path B does not re-read the balance after its do/catch — on a 402, a 5xx or a "
        "transport failure the badge keeps a balance the paid door may have charged")


# ── 9. a billable GET is never auto-resent ───────────────────────────────────


def test_a_paid_read_is_never_auto_resent():
    endpoint = _code(_ENDPOINT)
    billable = _block(endpoint, "nonisolated var isBillableRead: Bool")
    m = re.search(r"if\s+case\s+(\.\w+)\s*=\s*self\s*\{\s*return\s+true\s*\}", billable)
    assert m and m.group(1) == ".getTickerReport", (
        "APIEndpoint.isBillableRead no longer marks .getTickerReport — the paid report GET is "
        "auto-resent after a 5xx and double-bills one tap")
    client = _code(_CLIENT)
    retries = re.findall(r"if retryCount > 0,\s*case \.serverError = (?:error|apiError)[^{]*\{", client)
    assert len(retries) >= 2, "the 5xx retry blocks moved — re-derive this scan"
    for block in retries:
        assert "!endpoint.isBillableRead" in block, (
            f"a 5xx retry re-sends a billable GET: {block!r}")
    for fn in ("private func attemptFailover<T: Decodable>(", "private func attemptFailoverVoid(",
               "private func attemptFailoverReturningBody<T: Decodable>("):
        body = _block(client, fn)
        guard = re.search(r"guard[^\n{]*!endpoint\.isBillableRead[^\n{]*else\s*\{\s*throw originalError\s*\}", body)
        assert guard and guard.start() < body.index("buildRequest(for: endpoint, baseURL:"), (
            f"`{fn}` re-sends a billable GET through the DEBUG failover")


# ── 10. foreground re-reads a stale balance without a launch duplicate ───────


def test_foreground_rereads_a_stale_balance_without_a_launch_duplicate():
    app = _code(_APP)
    m = re.search(r"\.onReceive\(NotificationCenter\.default\.publisher\(for:\s*UIApplication\."
                  r"didBecomeActiveNotification\)\)", app)
    assert m, "the didBecomeActive handler moved — re-derive this scan"
    foreground = _balanced(app, app.index("{", m.end()))
    assert "appState.refreshCreditsIfStale()" in foreground, (
        "returning to the app no longer re-reads a stale balance — a month rollover or a "
        "charge on another device stays invisible until something else re-reads it")
    body = _block(_code(_APP_STATE), _IF_STALE)
    assert re.search(r"guard\s+auth\.isAuthenticated\s+else\s*\{\s*return\s*\}", body), (
        "refreshCreditsIfStale reads for a signed-out or reconnecting session")
    assert re.search(r"guard\s+creditsReadSeq\s*>\s*0\s+else\s*\{\s*return\s*\}", body), (
        "refreshCreditsIfStale duplicates the launch read — the first read belongs to onAuthenticated")
    assert "fresh.identity == identityGeneration" in body, (
        "refreshCreditsIfStale trusts another identity's freshness stamp")
    join = _block(body, "if let running = creditsTask")
    assert "await running.value" in join and "refreshCredits()" not in join, (
        "refreshCreditsIfStale adds demand to a read in flight instead of joining it")


# ── 11. research pushes re-read the balance ──────────────────────────────────


def test_research_pushes_reread_the_balance():
    delegate = _code(_DELEGATE)
    will = _block(delegate, "willPresent notification: UNNotification")
    assert "PushNotificationManager.shared.noteCreditMovingPush(info)" in will, (
        "a research push arriving in the foreground no longer re-reads the balance")
    did = _block(delegate, "didReceive response: UNNotificationResponse")
    tap = did.find("PushNotificationManager.shared.handleTap(pushed)")
    note = did.find("PushNotificationManager.shared.noteCreditMovingPush(info)")
    assert tap != -1 and note != -1, "a tapped research push no longer re-reads the balance"
    mark_read = _block(did, "if response.actionIdentifier == Action.markRead")
    assert "noteCreditMovingPush" not in mark_read, "a tapped research push no longer re-reads the balance"
    push = _code(_PUSH)
    kinds = re.search(r"creditMovingKinds:\s*Set<String>\s*=\s*\[([^\]]*)\]", push)
    assert kinds, "PushNotificationManager.creditMovingKinds is gone — re-derive"
    ios_kinds = set(re.findall(r'"([^"]+)"', kinds.group(1)))
    backend = _KINDS.read_text(encoding="utf-8")
    expected = {re.search(rf'^{name}\s*=\s*"([^"]+)"', backend, re.M).group(1)
                for name in ("KIND_RESEARCH_COMPLETE", "KIND_RESEARCH_FAILED")}
    assert ios_kinds == expected, (
        f"creditMovingKinds {sorted(ios_kinds)} drifted from the backend's research kinds "
        f"{sorted(expected)}")
    note_body = _block(push, "func noteCreditMovingPush(_ info: [AnyHashable: Any])")
    assert "appState.auth.isAuthenticated" in note_body and "refreshCredits()" in note_body, (
        "noteCreditMovingPush re-reads without a signed-in session (or not at all)")


# ── 12. chat re-reads after a streaming 402 and an abandoned turn ────────────


def test_chat_rereads_after_a_streaming_402_and_an_abandoned_turn():
    chat = _code(_CHAT)
    helper = _block(chat, "private func refreshCreditsAfterAbandonedTurn()")
    assert "refreshCreditsAfterSettle()" in helper, (
        "refreshCreditsAfterAbandonedTurn no longer schedules the delayed settle re-read")
    stream = _block(chat, "private func streamMessageToSession(")
    terminal = _block(stream, "if Self.isTerminalPreflightRefusal(error)")
    code = terminal.find('code == "INSUFFICIENT_CREDITS"')
    refresh = terminal.find("refreshCredits()", code)
    assert -1 < code < refresh < terminal.rindex("return"), (
        "a streaming 402 no longer re-reads the balance the server just corrected")
    loop_at = stream.index("for try await event in events")
    loop = stream[stream.index("{", loop_at):]
    first = re.match(r"\{\s*guard\s+sessionId\s*==\s*currentSessionId\s+else\s*\{", loop)
    assert first, "the stream loop's first statement is no longer its session guard — re-derive"
    assert "refreshCreditsAfterAbandonedTurn()" in _balanced(loop, first.end() - 1), (
        "a stream the user navigated away from no longer schedules a settle re-read")
    done = stream[stream.index('case "done":'): stream.index('case "error":')]
    guards = [_else_block(done[m.start():], "guard")
              for m in re.finditer(r"guard\s+sessionId\s*==\s*currentSessionId\s+else", done)]
    assert any("refreshCreditsIfMoved(turnCost)" in g for g in guards), (
        "a `done` turn dropped during the reveal drain no longer re-reads the wallet it charged")
    catch = stream[stream.rindex("} catch {"):]
    assert "refreshCreditsAfterAbandonedTurn()" in _else_block(catch, "guard sessionId == currentSessionId"), (
        "a stream the user navigated away from no longer schedules a settle re-read")
    send = _block(chat, "private func sendMessageToSession(")
    success = send[: send.index("} catch {")]
    assert "refreshCreditsIfMoved(response.credit)" in _else_block(success, "guard sessionId == currentSessionId"), (
        "a non-streaming answer dropped as stale no longer re-reads the wallet it charged")
    send_catch = send[send.index("} catch {"):]
    assert "refreshCreditsAfterAbandonedTurn()" in _else_block(send_catch, "guard sessionId == currentSessionId"), (
        "a non-streaming send the user navigated away from no longer schedules a settle re-read")
    rec = _block(chat, "private func reconcileAfterStreamFailure(")
    rec_guards = [_balanced(rec, rec.index("{", rec.index("else", m.start())))
                  for m in re.finditer(r"guard\s+sessionId\s*==\s*currentSessionId", rec)]
    assert len(rec_guards) >= 3 and all("refreshCreditsAfterAbandonedTurn()" in g for g in rec_guards), (
        "an abandoned reconcile no longer schedules a settle re-read")


# ── 13. a research shortfall reaches the global error host ───────────────────


def test_a_research_shortfall_goes_to_the_global_error_host():
    vm = _code(_VM)
    assert '"Insufficient credits"' not in vm, (
        'ResearchViewModel shows the bare "Insufficient credits" alert again — a dead end with no Buy Credits')
    gen = _block(vm, _GENERATE)
    arms = gen[gen.index("case .failed(let appError):"): gen.rindex("} catch {")]
    generic = arms[arms.index('print("❌ ResearchVM: Research failed'):]
    upgrade = generic.find("if appError.suggestedAction == .upgrade {")
    shown = generic.find("self.error = appError.message")
    assert -1 < upgrade < shown and "AppActions.shared.handleError(appError)" in (
        _block(generic, "if appError.suggestedAction == .upgrade")), (
        "the monitor's generic .failed arm no longer routes a 402 to the global error host")
    outer = gen[gen.rindex("} catch {"):]
    assert "AppActions.shared.handleError(failure)" in _block(outer, "if failure.suggestedAction == .upgrade"), (
        "the monitor's catch no longer routes a 402 to the global error host")
    gate = _block(gen, "if let balance = creditBalance, balance.credits < analysisCost.credits")
    assert re.search(r"AppActions\.shared\.handleError\(\s*AppError\.insufficientCredits\(", gate), (
        "generateAnalysis' balance gate no longer routes to the global error host")
    actions = _block(_code(_ACTIONS), "func handleError(_ error: Error)")
    assert "appState?.handleError(error)" in actions, "AppActions.handleError no longer reaches AppState"


def test_a_retry_owed_a_refund_is_never_upsold():
    retry = _block(_code(_VM), _RETRY)
    gate = _block(retry, "if backendId != nil, report.isRefunded")
    short = _block(gate, "if let balance = self.creditBalance, balance.credits < self.analysisCost.credits")
    pending = _block(short, "if report.refundIsPending")
    for token in ("handleError", "insufficientCredits", "showCreditsSheet"):
        assert token not in pending, (
            "a retry whose refund is still pending is sent to Buy Credits — the user is OWED the "
            f"credits they lack (`{token}`)")
    assert "self.error =" in pending, "a retry whose refund is still pending is refused silently"
    rest = short[short.index(pending) + len(pending):]
    assert re.search(r"AppActions\.shared\.handleError\(\s*AppError\.insufficientCredits\(", rest), (
        "retryReport's refunded-row gate no longer routes a real shortfall to Buy Credits")
    assert re.search(r"\breturn\s*\}\s*$", short), (
        "a short balance no longer stops the retry before the card is deleted")


# ── 14. Profile reads through AppState and still surfaces its error ──────────


def test_profile_reads_through_app_state_and_still_surfaces_its_error():
    profile = _code(_PROFILE_VM)
    load = _block(profile, "func loadCredits()")
    assert "if isRestoring { return }" in load and "isAuthenticated" in load, (
        "Profile's loadCredits lost its restoring skip or its signed-out clear")
    for token in ("apiClient.request(", "user.credits = credits"):
        assert token not in load, (
            f"Profile reads or writes the balance directly again (`{token}`) — unordered and "
            "unfenced against a sign-out")
    task = _block(load, 'performTask("loadCredits", showLoading: false)')
    read = task.find("await appState.refreshCredits()")
    assert read != -1 and re.search(r"throw\s+failure", task[read:]), (
        "Profile's credit read no longer surfaces a real failure (refreshCredits' error is not thrown)")
    perform = _block(_code(_APP_STATE), _PERFORM)
    catch = perform[perform.index("} catch {"):]
    tracked = catch.find("Analytics.shared.track(.backgroundSyncFailed")
    refusal = catch.find("if case .signInRequired = appError")
    cancel = catch.find("if appError.isCancellation { return nil }")
    assert -1 < refusal < tracked and -1 < cancel < tracked, (
        "a refused or cancelled credits read is tracked as a sync failure again")
    assert "return nil" in _block(catch, "if case .signInRequired = appError"), (
        "a refused or cancelled credits read is tracked as a sync failure again")


# ── 15. the settle re-read is delayed, coalesced and session-gated ───────────


def test_the_settle_reread_is_coalesced_and_fenced():
    app = _code(_APP_STATE)
    body = _block(app, _SETTLE)
    head = re.match(r"\{\s*guard\s+settleCreditsTask\s*==\s*nil\s+else\s*\{", body)
    assert head, "refreshCreditsAfterSettle no longer coalesces — every trigger schedules its own re-read"
    # A call while the timer is pending must not just inherit what is left of it: the refund it
    # waits for commits a round trip after the row flipped (IOS-CREDITS-2).
    rearm = _balanced(body, head.end() - 1)
    assert re.fullmatch(r"\{\s*settleCreditsRearmed\s*=\s*true\s+return\s*\}", rearm), (
        "a call made while the settle re-read is pending no longer re-arms it — a later money "
        "event inherits what is left of the first event's timer and can read before its refund lands")
    task = _block(body, "settleCreditsTask = Task")
    assert re.search(r"for\s+round\s+in\s+1\.\.\.AppState\.maxSettleRounds\s*\{", task), (
        "the settle re-read no longer runs its re-armed round (a loop bounded by maxSettleRounds)")
    cap = re.search(r"static\s+let\s+maxSettleRounds\s*=\s*(\d+)", app)
    assert cap and int(cap.group(1)) == 2, (
        "maxSettleRounds is gone, disables the re-armed round (1) or lets re-arms chain (> 2)")
    sleep = task.find("Task.sleep(for: delay)")
    cleared = task.find("self.settleCreditsTask = nil")
    signed_in = task.find("self.auth.isAuthenticated")
    read = task.find("self.refreshCredits()")
    assert -1 < sleep < read and -1 < cleared < read and -1 < signed_in < read, (
        "the settle re-read no longer waits, clears its slot and checks the session before reading")
    take = task.find("let rearmed = self.settleCreditsRearmed")
    consume = task.find("self.settleCreditsRearmed = false")
    assert -1 < take < consume < read, (
        "the settle re-read never consumes its re-arm flag — a later timer would run a needless "
        "extra round, and the cap would warn on every settle")
    again = re.search(r"let\s+again\s*=\s*rearmed\s*&&\s*round\s*<\s*AppState\.maxSettleRounds\b", task)
    stop = task.find("if !again { return }")
    assert again and again.start() < read < stop, (
        "the settle re-read's extra round no longer depends on a re-arm — every settle reads twice "
        "or a re-armed one never reads again")
    assert re.search(r"if\s+!again\s*\{\s*self\.settleCreditsTask\s*=\s*nil\s*\}", task), (
        "the settle re-read releases its slot while a re-armed round is still owed — a call during "
        "that round starts a second, overlapping timer")
    assert "self.settleCreditsTask = nil" in _else_block(task, "guard self.auth.isAuthenticated"), (
        "a settle re-read that finds the session ended keeps its slot held — no settle re-read "
        "would ever run again in this process")


# ── 16. no balance lands for a session that had ended ─────────────────────────


def test_no_balance_lands_for_a_session_that_had_ended():
    app = _code(_APP_STATE)
    perform = _block(app, _PERFORM)
    request = perform.find("apiClient.request(")
    assert request != -1, "performCreditsRefresh no longer sends the read — re-derive this scan"
    stamp = re.search(r"let\s+sentSignedOut\s*=\s*auth\.status\s*==\s*\.unauthenticated\b", perform)
    assert stamp and stamp.start() < request, (
        "performCreditsRefresh does not stamp whether the session had ended when the read was SENT — "
        "a read sent after sign-out carries the ended account's still-armed token")
    write = perform.find("user.credits =")
    guard = re.search(r"guard\s+!sentSignedOut\s*,\s*auth\.status\s*!=\s*\.unauthenticated\s+else\s*"
                      r"\{\s*return\s+nil\s*\}", perform)
    assert guard and request < guard.start() < write, (
        "performCreditsRefresh writes a balance for a session that had ended (sent or answered "
        "while signed out)")

    adopt = _block(app, _ADOPT)
    a_guard = re.search(r"guard\s+!ticket\.sentSignedOut\s*,\s*auth\.status\s*!=\s*\.unauthenticated"
                        r"\s+else\s*\{", adopt)
    a_write = adopt.find("user.credits =")
    assert a_guard and -1 < a_guard.start() < a_write, (
        "adoptCredits writes a balance for a session that had ended (sent or answered while signed out)")
    assert "return .superseded(current: user.credits)" in _balanced(adopt, a_guard.end() - 1), (
        "adoptCredits refuses an ended session's answer without reporting the balance that stands — "
        "Research would keep mirroring the ended account's figure")

    begin = _block(app, "func beginCreditsRead() -> CreditsReadTicket")
    assert re.search(r"sentSignedOut:\s*auth\.status\s*==\s*\.unauthenticated\b", begin), (
        "beginCreditsRead does not stamp whether the session had ended when the read was SENT")
    assert re.search(r"\blet\s+sentSignedOut\s*:\s*Bool\b", _struct_body(app, "CreditsReadTicket")), (
        "beginCreditsRead does not stamp whether the session had ended when the read was SENT")


# ── mutation table ───────────────────────────────────────────────────────────

_T1 = test_refresh_credits_reruns_for_a_call_that_lands_mid_read
_T2 = test_every_balance_write_is_send_ordered_and_identity_fenced
_T3 = test_research_load_credits_writes_through_after_its_pinned_apply
_T4 = test_the_backend_credits_dto_carries_every_credit_info_field
_T5 = test_the_research_tab_mirrors_app_state_without_a_request
_T6 = test_the_monitor_reads_the_balance_at_start_and_at_poll_timeout
_T7 = test_the_reports_list_notices_credit_movement_after_assigning
_T8 = test_path_b_rereads_the_wallet_on_every_outcome
_T9 = test_a_paid_read_is_never_auto_resent
_T10 = test_foreground_rereads_a_stale_balance_without_a_launch_duplicate
_T11 = test_research_pushes_reread_the_balance
_T12 = test_chat_rereads_after_a_streaming_402_and_an_abandoned_turn
_T13 = test_a_research_shortfall_goes_to_the_global_error_host
_T13b = test_a_retry_owed_a_refund_is_never_upsold
_T14 = test_profile_reads_through_app_state_and_still_surfaces_its_error
_T15 = test_the_settle_reread_is_coalesced_and_fenced
_T16 = test_no_balance_lands_for_a_session_that_had_ended

_ORDER_GUARD = (
    "        guard ticket.seq > creditsAppliedSeq else {\n"
    "            #if DEBUG\n"
    '            print("ℹ️ [AppState] credits answer #\\(ticket.seq) dropped — #\\(creditsAppliedSeq) is newer")\n'
    "            #endif\n"
    "            return .superseded(current: user.credits)\n"
    "        }\n")
_PROFILE_TASK = (
    "            guard let appState = self?.appState else { return }\n"
    "            if let failure = await appState.refreshCredits() {\n"
    "                throw failure\n"
    "            }\n")
_THROUGH = (
    "            if case .superseded(let current) = AppActions.shared.adoptCredits(CreditInfo(backendCredits), from: ticket) {\n"
    "                self.creditBalance = current.map { CreditBalance.from($0) }\n"
    "            }\n")
_TICKET = "        let ticket = AppActions.shared.beginCreditsRead()\n"
_CREDITS_REQUEST = "            let backendCredits: BackendCreditsResponse = try await apiClient.request(\n"
_ONCHANGE = (
    "        .onChange(of: appState.user.credits, initial: true) { _, info in\n"
    "            viewModel.adoptSharedCredits(info)\n"
    "        }\n")
_STARTED_CREDITS = (
    "                        await self.loadReports()\n"
    "                        // The POST that answered `.started` just precharged the report's\n"
    "                        // credits: show the debit now, not after the run ends.\n"
    "                        await self.loadCredits()\n")
_I28 = " " * 28
_POLL_TAIL = (
    f"{_I28}// for another identity.\n"
    f"{_I28}await self.loadCredits()\n")
_NOTE_CALL = "            noteCreditMovingChanges(in: backendReports)\n"
_LIVE = "            applyLiveProgress()   // keep the in-flight row at the live stream %\n"
_RELEASE = "            releaseFinishedSlots(against: backendReports)\n"
_PATH_B_REFRESH = "        AppActions.shared.refreshCredits()\n    }\n\n    // MARK: - Detailed-Analysis PDF"
_PATH_B_SUCCESS = "            self.reportData = reportData\n            self.error = nil\n"
_RETRY_COND = (
    "               endpoint.method.isSafeToRetryAfterServerError,\n"
    "               !endpoint.isBillableRead {\n"
    "                try await Task.sleep(nanoseconds: 1_000_000_000) // 1 second\n"
    "                return try await self.request(")
_BODY_FAILOVER = "        guard !endpoint.isBillableRead else { throw originalError }\n"
_STALE_AUTH = "        guard auth.isAuthenticated else { return }\n        guard creditsReadSeq > 0 else { return }\n"
_STALE_JOIN = "            _ = await running.value\n            return\n"
_TAP_NOTE = (
    "            // The tap may be the first this process hears of a report's charge settling.\n"
    "            PushNotificationManager.shared.noteCreditMovingPush(info)\n")
_STREAM_402 = (
    "                if case APIError.businessError(let code, _) = error, code == \"INSUFFICIENT_CREDITS\" {\n"
    "                    Task { [weak appState] in await appState?.refreshCredits() }\n"
    "                }\n"
    "                return\n"
    "            }\n\n"
    "            print(\"⚠️ [ChatVM] Stream failed")
_DONE_GUARD = (
    "                    guard sessionId == currentSessionId else {\n"
    "                        refreshCreditsIfMoved(turnCost)\n"
    "                        return\n"
    "                    }\n")
_GENERIC_ARM = (
    "                            if appError.suggestedAction == .upgrade {\n"
    "                                AppActions.shared.handleError(appError)\n"
    "                            } else {\n"
    "                                self.error = appError.message\n"
    "                            }\n")
_GEN_GATE = (
    "            AppActions.shared.handleError(\n"
    "                AppError.insufficientCredits(required: analysisCost.credits, available: balance.credits)\n"
    "            )\n")
_PENDING_MSG = (
    "                        self.error = \"This analysis's refund hasn't landed yet, so there aren't "
    "enough credits to retry. Try again once it has.\"\n")
_REFUSAL_ARM = (
    "            if case .signInRequired = appError {\n"
    "                #if DEBUG\n"
    "                print(\"ℹ️ [AppState] credits refresh refused — no token armed\")\n"
    "                #endif\n"
    "                return nil\n"
    "            }\n")
_SETTLE_GUARD = (
    "        guard settleCreditsTask == nil else {\n"
    "            settleCreditsRearmed = true\n"
    "            return\n"
    "        }\n")
_I16 = " " * 16
_SETTLE_SLEEP = f"{_I16}try? await Task.sleep(for: delay)\n"
_SETTLE_READ_TAIL = f"{_I16}await self.refreshCredits()\n{_I16}if !again {{ return }}\n"
_SETTLE_AUTH = (
    f"{_I16}guard self.auth.isAuthenticated else {{\n"
    f"{_I16}    self.settleCreditsTask = nil\n"
    f"{_I16}    return\n"
    f"{_I16}}}\n")
_PERFORM_SESSION = "            guard !sentSignedOut, auth.status != .unauthenticated else { return nil }\n"
_PERFORM_STAMP = "        let sentSignedOut = auth.status == .unauthenticated\n"
_ADOPT_SESSION_HEAD = "        guard !ticket.sentSignedOut, auth.status != .unauthenticated else {\n"
_ADOPT_SESSION_TAIL = (
    "            print(\"ℹ️ [AppState] credits answer for an ended session dropped\")\n"
    "            #endif\n"
    "            return .superseded(current: user.credits)\n")
_ADOPT_SESSION = (
    "        // See the SESSION fence above: sent with an ended session's still-armed token.\n"
    + _ADOPT_SESSION_HEAD
    + "            #if DEBUG\n"
    + _ADOPT_SESSION_TAIL
    + "        }\n")

_MUTATIONS = [
    # ── 1 ──
    ("R1-joiner-adds-no-demand", _APP_STATE,
     (("        creditsDemand &+= 1\n", ""),),
     _T1, "refreshCredits no longer counts a JOINER's call"),
    ("R2-covered-read-after-the-get", _APP_STATE,
     (("                let covered = self.creditsDemand\n                failure = await self.performCreditsRefresh()\n",
       "                failure = await self.performCreditsRefresh()\n                let covered = self.creditsDemand\n"),),
     _T1, "the re-run check is broken"),
    ("R3-single-read-no-loop", _APP_STATE,
     (("            for round in 1...Self.maxCreditsRefreshRounds {\n",
       "            for round in [1] where round > 0 {\n"),),
     _T1, "the refresh task no longer loops"),
    # ── 2 ──
    ("O1-adopt-order-guard-gone", _APP_STATE, ((_ORDER_GUARD, ""),),
     _T2, "adoptCredits writes user.credits without its send-order guard"),
    ("O2-perform-seq-taken-late", _APP_STATE,
     (("        creditsReadSeq &+= 1\n        let seq = creditsReadSeq\n        do {\n",
       "        do {\n"),
      ("                responseType: CreditInfo.self\n            )\n            guard identity",
       "                responseType: CreditInfo.self\n            )\n            creditsReadSeq &+= 1\n"
       "            let seq = creditsReadSeq\n            guard identity")),
     _T2, "performCreditsRefresh takes `creditsReadSeq &+= 1` after the request was sent"),
    ("O3-profile-writes-directly-again", _PROFILE_VM,
     ((_PROFILE_TASK, "            self?.appState?.user.credits = credits\n"),),
     _T2, "a balance is written into user.credits outside performCreditsRefresh/adoptCredits"),
    # ── 3 ──
    ("W1-no-write-through", _VM, ((_THROUGH, ""),),
     _T3, "loadCredits no longer writes its answer through to AppState"),
    ("W2-ticket-after-the-request", _VM,
     ((_TICKET, ""), (_CREDITS_REQUEST, _TICKET + _CREDITS_REQUEST)),
     _T3, "loadCredits takes its send-order ticket after the request went out"),
    ("W3-superseded-not-mirrored", _VM,
     (("                self.creditBalance = current.map { CreditBalance.from($0) }\n", ""),),
     _T3, "loadCredits ignores a refused write-through"),
    # ── 4 ──
    ("D1-dto-drops-granted-total", _POLL,
     (('        case grantedTotal = "granted_total"\n        case grantedUsed = "granted_used"\n    }\n}\n\nextension BackendCreditsResponse',
       '        case grantedUsed = "granted_used"\n    }\n}\n\nextension BackendCreditsResponse'),),
     _T4, "BackendCreditsResponse does not decode ['granted_total']"),
    ("D2-conversion-drops-granted-total", _POLL,
     (("            grantedTotal: response.grantedTotal,\n", "            grantedTotal: nil,\n"),),
     _T4, "does not pass ['grantedTotal'] through"),
    # ── 5 ──
    ("M1-mirror-calls-back", _VM,
     (("        creditBalance = info.map { CreditBalance.from($0) }\n",
       "        creditBalance = info.map { CreditBalance.from($0) }\n        AppActions.shared.refreshCredits()\n"),),
     _T5, "adoptSharedCredits does more than assign (`AppActions`)"),
    ("M2-display-prefers-the-vm-copy", _CONTENT,
     (("        appState.user.credits.map { CreditBalance.from($0) }\n    }",
       "        viewModel.creditBalance ?? appState.user.credits.map { CreditBalance.from($0) }\n    }"),),
     _T5, "the Research card/badge displays the ViewModel's own copy again"),
    ("M3-mirror-gone", _CONTENT, ((_ONCHANGE, ""),),
     _T5, "the Research tab no longer mirrors AppState's balance into its ViewModel"),
    # ── 6 ──
    ("S1-started-arm-no-credits", _VM,
     ((_STARTED_CREDITS, "                        await self.loadReports()\n"),),
     _T6, "the monitor's .started arm no longer re-reads the balance"),
    ("S2-poll-timeout-no-credits", _VM,
     ((_POLL_TAIL, f"{_I28}// for another identity.\n"),),
     _T6, "the poll-timeout arm no longer re-reads the balance"),
    # ── 7 ──
    ("L1-noticed-before-the-assignment", _VM,
     ((_NOTE_CALL, ""), (_RELEASE, _RELEASE + _NOTE_CALL)),
     _T7, "loadReports no longer notices credit movement AFTER assigning the list"),
    ("L2-refunded-flip-ignored", _VM,
     (("            if mark.refunded && !old.refunded { moved = true }\n", ""),),
     _T7, "noteCreditMovingChanges no longer re-reads on a newly refunded row"),
    ("L3-confirmed-flip-ignored", _VM,
     (("            if mark.confirmed == true && old.confirmed != true { moved = true }\n", ""),),
     _T7, "noteCreditMovingChanges no longer re-reads on a newly confirmed refund"),
    ("L4-first-pass-diffs", _VM,
     (("        guard let before = reportCreditMarks else { return }\n",
       "        let before = reportCreditMarks ?? [:]\n"),),
     _T7, "the first list after a launch or an identity change triggers a refresh"),
    ("L5-settle-gated-on-refunded", _VM,
     (("                if mark.charged { settle = true }\n",
       "                if mark.charged && !mark.refunded { settle = true }\n"),),
     _T7, "a newly failed CHARGED row no longer schedules the settle re-read"),
    ("L7-unknown-charge-is-free", _VM,
     (("                charged: (row.creditsCharged ?? 1) > 0\n",
       "                charged: (row.creditsCharged ?? 0) > 0\n"),),
     _T7, "a row with no credits_charged is treated as uncharged"),
    ("L6-identity-keeps-marks", _VM,
     (("        reportCreditMarks = nil\n", ""),),
     _T7, "handleIdentityChange keeps the previous account's credit marks"),
    # ── 8 ──
    ("B1-path-b-refresh-on-success-only", _REPORT_VM,
     (("        AppActions.shared.refreshCredits()\n    }\n\n    // MARK: - Detailed-Analysis PDF",
       "    }\n\n    // MARK: - Detailed-Analysis PDF"),
      (_PATH_B_SUCCESS, _PATH_B_SUCCESS + "            AppActions.shared.refreshCredits()\n")),
     _T8, "Path B does not re-read the balance after its do/catch"),
    # ── 9 ──
    ("A1-request-retry-resends-paid-get", _CLIENT,
     ((_RETRY_COND, _RETRY_COND.replace(",\n               !endpoint.isBillableRead {", " {")),),
     _T9, "a 5xx retry re-sends a billable GET"),
    ("A2-ticker-report-not-billable", _ENDPOINT,
     (("        if case .getTickerReport = self { return true }\n",
       "        if case .getResearchStatus = self { return true }\n"),),
     _T9, "APIEndpoint.isBillableRead no longer marks .getTickerReport"),
    ("A3-body-failover-resends-paid-get", _CLIENT, ((_BODY_FAILOVER, ""),),
     _T9, "`private func attemptFailoverReturningBody<T: Decodable>(` re-sends a billable GET"),
    # ── 10 ──
    ("F1-foreground-reads-signed-out", _APP_STATE,
     ((_STALE_AUTH, "        guard creditsReadSeq > 0 else { return }\n"),),
     _T10, "refreshCreditsIfStale reads for a signed-out or reconnecting session"),
    ("F2-foreground-duplicates-launch-read", _APP_STATE,
     ((_STALE_AUTH, "        guard auth.isAuthenticated else { return }\n"),),
     _T10, "refreshCreditsIfStale duplicates the launch read"),
    ("F3-foreground-adds-demand", _APP_STATE,
     ((_STALE_JOIN, "            _ = await running.value\n            await refreshCredits()\n            return\n"),),
     _T10, "refreshCreditsIfStale adds demand to a read in flight"),
    ("F4-foreground-trigger-gone", _APP,
     (("                    Task { await appState.refreshCreditsIfStale() }\n", ""),),
     _T10, "returning to the app no longer re-reads a stale balance"),
    # ── 11 ──
    ("P1-tap-not-noted", _DELEGATE, ((_TAP_NOTE, ""),),
     _T11, "a tapped research push no longer re-reads the balance"),
    ("P2-failed-kind-misspelled", _PUSH,
     (('["research_complete", "research_failed"]', '["research_complete", "research_fail"]'),),
     _T11, "creditMovingKinds"),
    ("P3-push-reads-signed-out", _PUSH,
     (("              let appState,\n              appState.auth.isAuthenticated else { return }\n",
       "              let appState else { return }\n"),),
     _T11, "noteCreditMovingPush re-reads without a signed-in session"),
    # ── 12 ──
    ("C1-stream-402-no-refresh", _CHAT,
     ((_STREAM_402, "                return\n            }\n\n            print(\"⚠️ [ChatVM] Stream failed"),),
     _T12, "a streaming 402 no longer re-reads the balance"),
    ("C2-done-guard-no-refresh", _CHAT,
     ((_DONE_GUARD, "                    guard sessionId == currentSessionId else { return }\n"),),
     _T12, "a `done` turn dropped during the reveal drain no longer re-reads"),
    ("C3-loop-top-no-settle", _CHAT,
     (("                guard sessionId == currentSessionId else {\n"
       "                    refreshCreditsAfterAbandonedTurn()\n"
       "                    return\n"
       "                }\n\n"
       "                switch event.event {",
       "                guard sessionId == currentSessionId else { return }\n\n"
       "                switch event.event {"),),
     _T12, "a stream the user navigated away from no longer schedules a settle re-read"),
    ("C4-stale-success-no-refresh", _CHAT,
     (("                // Dropped from the screen, but persisted and charged all the same.\n"
       "                refreshCreditsIfMoved(response.credit)\n", ""),),
     _T12, "a non-streaming answer dropped as stale no longer re-reads"),
    ("C5-helper-reads-at-once", _CHAT,
     (("        appState?.refreshCreditsAfterSettle()\n    }\n",
       "        Task { [weak appState] in await appState?.refreshCredits() }\n    }\n"),),
     _T12, "refreshCreditsAfterAbandonedTurn no longer schedules the delayed settle re-read"),
    # ── 13 ──
    ("E1-generic-arm-local-alert", _VM,
     ((_GENERIC_ARM, "                            self.error = appError.message\n"),),
     _T13, "the monitor's generic .failed arm no longer routes a 402 to the global error host"),
    ("E2-bare-insufficient-alert", _VM,
     ((_GEN_GATE, '            error = "Insufficient credits"\n'),),
     _T13, 'ResearchViewModel shows the bare "Insufficient credits" alert again'),
    ("E3-pending-retry-upsold", _VM,
     ((_PENDING_MSG,
       "                        AppActions.shared.handleError(AppError.insufficientCredits(required: 20, available: 0))\n"
       + _PENDING_MSG),),
     _T13b, "a retry whose refund is still pending is sent to Buy Credits"),
    # ── 14 ──
    ("Q1-profile-error-swallowed", _PROFILE_VM,
     (("            if let failure = await appState.refreshCredits() {\n                throw failure\n            }\n",
       "            _ = await appState.refreshCredits()\n"),),
     _T14, "Profile's credit read no longer surfaces a real failure"),
    ("Q2-profile-direct-get", _PROFILE_VM,
     ((_PROFILE_TASK,
       "            let credits = try await self?.apiClient.request(\n"
       "                endpoint: .getUserCredits,\n                responseType: CreditInfo.self\n            )\n"),),
     _T14, "Profile reads or writes the balance directly again (`apiClient.request(`)"),
    ("Q3-refusal-tracked-as-failure", _APP_STATE,
     ((_REFUSAL_ARM, ""),
      ("            #if DEBUG\n            print(\"⚠️ [AppState] refreshCredits failed: \\(appError.message)\")\n",
       _REFUSAL_ARM + "            #if DEBUG\n            print(\"⚠️ [AppState] refreshCredits failed: \\(appError.message)\")\n")),
     _T14, "a refused or cancelled credits read is tracked as a sync failure again"),
    # ── 15 ──
    ("T1-settle-not-coalesced", _APP_STATE, ((_SETTLE_GUARD, ""),),
     _T15, "refreshCreditsAfterSettle no longer coalesces"),
    ("T2-settle-reads-before-sleeping", _APP_STATE,
     ((_SETTLE_SLEEP, ""),
      (_SETTLE_READ_TAIL, f"{_I16}await self.refreshCredits()\n" + _SETTLE_SLEEP + f"{_I16}if !again {{ return }}\n")),
     _T15, "the settle re-read no longer waits"),
    ("T3-settle-call-not-rearmed", _APP_STATE,
     (("            settleCreditsRearmed = true\n            return\n", "            return\n"),),
     _T15, "a call made while the settle re-read is pending no longer re-arms it"),
    ("T4-settle-no-extra-round", _APP_STATE,
     (("    private static let maxSettleRounds = 2\n", "    private static let maxSettleRounds = 1\n"),),
     _T15, "maxSettleRounds is gone, disables the re-armed round"),
    ("T5-settle-flag-never-consumed", _APP_STATE,
     ((f"{_I16}self.settleCreditsRearmed = false\n", ""),),
     _T15, "the settle re-read never consumes its re-arm flag"),
    ("T6-settle-extra-round-unconditional", _APP_STATE,
     ((f"{_I16}let again = rearmed && round < AppState.maxSettleRounds\n",
       f"{_I16}let again = round < AppState.maxSettleRounds\n"),),
     _T15, "the settle re-read's extra round no longer depends on a re-arm"),
    ("T7-settle-always-reads-twice", _APP_STATE,
     ((_SETTLE_READ_TAIL, f"{_I16}await self.refreshCredits()\n"),),
     _T15, "the settle re-read's extra round no longer depends on a re-arm"),
    ("T8-settle-slot-released-while-owed", _APP_STATE,
     ((f"{_I16}if !again {{ self.settleCreditsTask = nil }}\n", f"{_I16}self.settleCreditsTask = nil\n"),),
     _T15, "the settle re-read releases its slot while a re-armed round is still owed"),
    ("T9-settle-slot-stuck-after-sign-out", _APP_STATE,
     ((_SETTLE_AUTH, f"{_I16}guard self.auth.isAuthenticated else {{ return }}\n"),),
     _T15, "a settle re-read that finds the session ended keeps its slot held"),
    # ── 16 ──
    ("X1-perform-lands-after-sign-out", _APP_STATE, ((_PERFORM_SESSION, ""),),
     _T16, "performCreditsRefresh writes a balance for a session that had ended"),
    ("X2-perform-checks-only-the-send-stamp", _APP_STATE,
     ((_PERFORM_SESSION, "            guard !sentSignedOut else { return nil }\n"),),
     _T16, "performCreditsRefresh writes a balance for a session that had ended"),
    ("X3-perform-stamped-when-answered", _APP_STATE,
     ((_PERFORM_STAMP, ""),
      ("                responseType: CreditInfo.self\n            )\n            guard identity",
       "                responseType: CreditInfo.self\n            )\n"
       "            let sentSignedOut = auth.status == .unauthenticated\n            guard identity")),
     _T16, "performCreditsRefresh does not stamp whether the session had ended when the read was SENT"),
    ("X4-adopt-lands-after-sign-out", _APP_STATE, ((_ADOPT_SESSION, ""),),
     _T16, "adoptCredits writes a balance for a session that had ended"),
    ("X5-adopt-ignores-the-send-stamp", _APP_STATE,
     ((_ADOPT_SESSION_HEAD, "        guard auth.status != .unauthenticated else {\n"),),
     _T16, "adoptCredits writes a balance for a session that had ended"),
    ("X6-adopt-refusal-claims-applied", _APP_STATE,
     ((_ADOPT_SESSION_TAIL, _ADOPT_SESSION_TAIL.replace("return .superseded(current: user.credits)", "return .applied")),),
     _T16, "adoptCredits refuses an ended session's answer without reporting the balance that stands"),
    ("X7-ticket-not-stamped", _APP_STATE,
     (("            sentSignedOut: auth.status == .unauthenticated\n", "            sentSignedOut: false\n"),),
     _T16, "beginCreditsRead does not stamp whether the session had ended when the read was SENT"),
]


@pytest.mark.parametrize(
    "path,edits,test,message",
    [m[1:] for m in _MUTATIONS],
    ids=[m[0] for m in _MUTATIONS],
)
def test_each_mutation_is_killed(monkeypatch, path, edits, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — the Swift files are never rewritten."""
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
    with pytest.raises(AssertionError, match=re.escape(message)):
        test()


def test_the_mutation_table_covers_every_guard():
    covered = {m[3] for m in _MUTATIONS}
    for guard in (_T1, _T2, _T3, _T4, _T5, _T6, _T7, _T8, _T9, _T10, _T11, _T12, _T13, _T13b,
                  _T14, _T15, _T16):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"
    names = [m[0] for m in _MUTATIONS]
    assert len(names) == len(set(names)), "duplicate mutation ids"


def test_comment_stripping_is_load_bearing():
    """The fixes' comments quote the tokens these guards look for; stripping must remove them."""
    raw = _APP_STATE.read_text(encoding="utf-8")
    assert "`ResearchViewModel.loadCredits()` does NOT go" in raw
    assert "`ResearchViewModel.loadCredits()` does NOT go" not in _code(_APP_STATE)
