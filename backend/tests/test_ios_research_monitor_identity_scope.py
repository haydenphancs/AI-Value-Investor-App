"""Report-generation work never outlives — or acts for — the ACCOUNT that tapped.

`ResearchViewModel.generateAnalysis()` starts one unstructured `Task` per tap that iterates
`TaskPollingManager.generateAndMonitorResearch` (a 3 s `/research/status` poll, up to
`maxPollDuration` = 300 s). `handleIdentityChange` cleared `inFlightReportIds`, `liveProgress`
and `reports` on a sign-out or an account switch, but never stopped those Tasks — and each held
the ViewModel through `guard let self`. After the session ended, every tick was refused
pre-flight as `.signInRequired`, which `isTransientPollFailure` treats as transient, so the
monitor ran to its deadline. Its `.failed(.timeout)` arm ("CLIENT-side poll timeout only") then
did `self.inFlightReportIds.insert(id)` and `startReportsPolling()`: the PREVIOUS account's report
id took one of the NEW account's four Generate slots, and the list poll restarted for an account
that never owned it.

The work is stamped with the account (`AppActions.shared.currentAccountId`, the profile id, which
is kept through the `.restoring` window), not with `identityEpoch`: a reconnect of the SAME
account also reaches `handleIdentityChange`, and the owner chose (2026-10-02) that it must keep
its monitors, slots and live progress. What is pinned (`ResearchViewModel.swift` unless named):

1. Every monitor Task is registered in `generationMonitors` with its owner and removes itself on
   exit (`defer`); `generateAnalysis` starts no other Task.
2. The owner is read ONCE, at the tap (`guard let owner = AppActions.shared.currentAccountId`),
   `monitorMayAct(_:)` compares it with the LIVE `currentAccountId`, and `AppActions` answers
   that from the profile id — never gated on `isSignedIn`, which is false while restoring.
3. The Task re-checks `monitorMayAct(owner)` before `generateAndMonitorResearch`: a Task whose
   first run lands after an account switch would otherwise POST — and charge 20 credits to the
   NEXT account — for the previous one's tap.
4. The stream loop's first statement is that re-check, so no arm runs for another account.
5. No write follows an `await` without a re-check in between: the loop's arms, the monitor's
   catch (entry uncovered) and `retryReport`'s Task (`stillOwner(...)`). The scan walks each
   write's enclosing blocks, dropping branches that cannot fall through (`continue`/`return`)
   and earlier branches of the same if/else chain, so arms added later are covered too.
6. `handleIdentityChange` cancels every monitor another account owns, BEFORE its clears and
   unconditionally (ahead of the `isActiveTab` gate, no branch, early exit or `await` first).
7. The slots (`inFlightReportIds`, `liveProgress`) are dropped unless the account is unchanged
   (`account == nil || account != inFlightOwnerId`), and every slot taken records its owner.
8. `retryReport` stamps its owner at the tap and `stillOwner` re-checks `monitorMayAct(owner)`:
   its last step calls `generateAnalysis()`, which charges whoever holds the session by then.
9. A stopped monitor raises no alert: each re-check's `else` only logs and returns, and the
   `.failed` arm's `isCancellation` branch still precedes the failure analytics and the alert.
10. `TaskPollingManager`: both stream builders cancel their producer Task from
    `continuation.onTermination` (the `APIClient.stream` idiom), so cancelling the consumer
    stops the `/status` poll instead of leaving it to run to `maxPollDuration`.

There is no XCTest target (testing.md §3): comments are stripped before every assertion, every
scan is brace-bound to its declaration, and presence is asserted before anything is sliced.

Mutation-tested IN MEMORY (``pathlib.Path.read_text`` monkeypatched for the one target file —
the Swift files are never rewritten; other sessions read them concurrently). The table runs on
every pass as ``test_each_mutation_is_killed``, and each mutation must fail with the assertion
message that names it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_POLL = _IOS / "Core" / "Services" / "TaskPollingManager.swift"
_ACTIONS = _IOS / "Core" / "State" / "AppActions.swift"

_GENERATE = "func generateAnalysis()"
_RETRY = "func retryReport(_ report: AnalysisReport)"
_IDENTITY = "func handleIdentityChange(isActiveTab: Bool) async"
_HELPER = "private func monitorMayAct(_ owner: String) -> Bool"
_TASK = "let task = Task"
_RETRY_TASK = "Task { [weak self] in"
_LOOP = "for try await progress in stream"
_SWITCH = "switch progress"
_DECL = "private var generationMonitors: [UUID: GenerationMonitor] = [:]"
_ACCOUNT = "var currentAccountId: String?"
_POLL_FUNCS = ("func generateAndMonitorResearch(", "func monitorResearch(reportId: String)")

_REGISTRATION = re.compile(
    r"\bgenerationMonitors\[monitorKey\]\s*=\s*GenerationMonitor\(\s*ownerId:\s*owner\s*,\s*task:\s*task\s*\)")
_OWNER_AT_TAP = re.compile(r"\bguard\s+let\s+owner\s*=\s*AppActions\.shared\.currentAccountId\s+else\s*\{")
_RECHECK = re.compile(r"\bguard\s+self\.monitorMayAct\(owner\)\s+else\b")
_RETRY_CHECK = re.compile(r"\bguard\s+stillOwner\(\"[^\"\n]*\"\)\s+else\b")
_AWAIT = re.compile(r"\bawait\b")
_CASE_LABEL = re.compile(r"^[ \t]*(?:case\s+\.[^\n]*|default)\s*:[ \t]*$", re.M)
# A write to the ViewModel's state. `loadReports()` / `loadCredits()` are not here: they are
# awaited (so they ARE the suspension points) and guard their own answers.
_WRITE = re.compile(
    r"\bself\.\w+(?:\[[^\]\n]*\])?\s*=(?!=)"
    r"|\bself\.\w+\.(?:insert|remove|removeAll|removeValue|formUnion|subtract|append)(?:\(|\s*\{)"
    r"|\bself\.(?:startReportsPolling|stopReportsPolling|applyLiveProgress|adoptCompletedInsteadOfRetrying"
    r"|applyPrefilledTicker|selectPersona|generateAnalysis)\(")
_KEEP_OWN = re.compile(r"\blet\s+kept\s*=\s*generationMonitors\.filter\s*\{\s*\$0\.value\.ownerId\s*==\s*account\s*\}")
_CANCEL_OTHERS = re.compile(
    r"for\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)\s+in\s+generationMonitors\s+where\s+kept\[\1\]\s*==\s*nil\s*"
    r"\{\s*\2\.task\.cancel\(\)\s*\}")
_SLOT_DROP_IF = re.compile(r"\bif\s+account\s*==\s*nil\s*\|\|\s*account\s*!=\s*inFlightOwnerId\s*\{")
_CLEARS = ("reports = []", "locallyTimedOutReportIds = []", "stopReportsPolling()")


# ── helpers (same shape as test_ios_reports_gate_from_refusal.py) ───────────────

def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails (a tail needs
    leading whitespace, so a `https://` inside a string literal is not cut)."""
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
    """The balanced `{…}` body opened by the first `{` at or after the ONLY `header`."""
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    start = src.index("{", src.index(header))
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after `{header}`")


def _pairs(src: str) -> list[tuple[int, int]]:
    stack, out = [], []
    for i, ch in enumerate(src):
        if ch == "{":
            stack.append(i)
        elif ch == "}":
            assert stack, "unbalanced `}` in the scanned block"
            out.append((stack.pop(), i))
    assert not stack, "unbalanced `{` in the scanned block"
    return out


def _line(src: str, pos: int) -> str:
    end = src.find("\n", pos)
    return src[src.rfind("\n", 0, pos) + 1: end if end != -1 else len(src)].strip()


def _generate() -> str:
    gen = _block(_code(_VM), _GENERATE)
    assert "pollingManager.generateAndMonitorResearch(" in gen and "case .failed(let appError):" in gen, (
        "the generateAnalysis block is not the live generation path — re-derive this guard")
    return gen


def _task_body(gen: str) -> str:
    assert gen.count(_TASK) == 1, (
        "generateAnalysis's monitor Task is not registered in `generationMonitors` with its owner")
    return _block(gen, _TASK)


def _retry() -> str:
    retry = _block(_code(_VM), _RETRY)
    assert ".deleteReport(reportId: backendId, forRetry: true)" in retry and "self.generateAnalysis()" in retry, (
        "the retryReport block is not the live retry path — re-derive this guard")
    return retry


def _falls_through(seg: str) -> str:
    """`seg` as the code that can run before the segment's end, block by top-level block:

    - a block whose last statement is `continue` / `return` cannot fall through: cut;
    - an earlier branch of the if/else chain the segment ends inside is mutually exclusive with
      the branch being reached: cut;
    - the `do` body of the do/catch the segment ends inside (the code being reached is in its
      `catch`): the catch is entered from a THROWING point, so a re-check after the do body's
      `await` never runs on that path. It is replaced by its awaits alone (checks dropped).
    """
    blocks, depth, start = [], 0, 0
    for k, ch in enumerate(seg):
        if ch == "{":
            if depth == 0:
                start = k
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blocks.append((start, k))
    chains, current = [], []
    for a, b in blocks:
        current.append((a, b))
        if not re.match(r"\s*(?:else|catch)\b", seg[b + 1:]):
            chains.append((current, False))
            current = []
    if current:
        chains.append((current, True))
    replace: list[tuple[int, int, str]] = []
    for chain, segment_ends_inside in chains:
        for a, b in chain:
            body = seg[a + 1: b].strip()
            last = re.split(r"[\n;]", body)[-1].strip() if body else ""
            if segment_ends_inside and re.search(r"\bdo\s*$", seg[:a]):
                replace.append((a, b, " await " if _AWAIT.search(body) else ""))
            elif segment_ends_inside or re.fullmatch(r"(?:continue|return)\b.*", last):
                replace.append((a, b, ""))
    out, at = [], 0
    for a, b, text in replace:
        out.append(seg[at:a])
        out.append(text)
        at = b + 1
    out.append(seg[at:])
    return "".join(out)


def _unchecked_writes(root: str, *, switch_open: int | None = None, entry_covered: bool = False,
                      recheck: re.Pattern = _RECHECK) -> tuple[int, list[str]]:
    """(writes scanned, writes reachable from an `await` with no `recheck` in between). Walks
    each write's enclosing blocks innermost-first; the nearest `await` or re-check on the
    fall-through path decides. An arm of the `switch` at `switch_open` is entered through the
    loop's first-statement re-check (pinned by test 4); otherwise the root's entry is covered
    only when `entry_covered` (code that runs synchronously from the tap)."""
    pairs = _pairs(root)
    writes = list(_WRITE.finditer(root))
    bad = []
    for m in writes:
        w = m.start()
        enclosing = sorted(((o, c) for o, c in pairs if o < w < c), key=lambda p: -p[0])
        end, covered = w, None
        for o, _c in enclosing:
            seg_start = o + 1
            if o == switch_open:
                labels = list(_CASE_LABEL.finditer(root, o + 1, end))
                assert labels, f"no case label above `{_line(root, w)}`"
                seg_start = labels[-1].end()
            seg = _falls_through(root[seg_start:end])
            awaits = [x.start() for x in _AWAIT.finditer(seg)]
            checks = [x.start() for x in recheck.finditer(seg)]
            if checks and (not awaits or checks[-1] > awaits[-1]):
                covered = True
                break
            if awaits:
                covered = False
                break
            if o == switch_open:
                covered = True
                break
            end = o
        if covered is None:
            covered = entry_covered
        if not covered:
            bad.append(_line(root, w))
    return len(writes), bad


# ── 1. every monitor is registered with its owner, and deregisters ──────────────

def test_each_generation_monitor_is_registered_and_deregisters():
    vm = _code(_VM)
    assert vm.count(_DECL) == 1, "`generationMonitors` declaration changed — re-derive this guard"
    record = re.sub(r"\s+", " ", _block(vm, "private struct GenerationMonitor"))
    assert "let ownerId: String" in record and "let task: Task<Void, Never>" in record, (
        "`GenerationMonitor` no longer carries its owner and its Task")
    gen = _generate()
    body = _task_body(gen)
    after = gen[gen.index(_TASK) + len(body):]
    assert _REGISTRATION.search(after), (
        "generateAnalysis's monitor Task is not registered in `generationMonitors` with its owner")
    assert re.search(r"\bdefer\s*\{\s*self\.generationMonitors\[monitorKey\]\s*=\s*nil\s*\}", body), (
        "the monitor Task never removes itself from `generationMonitors`")
    assert len(re.findall(r"\bTask\s*\{", gen)) == 1, (
        "generateAnalysis starts a Task outside `generationMonitors`")


# ── 2. owner read once at the tap, compared live, kept through restoring ────────

def test_the_monitor_is_stamped_with_the_account_that_tapped():
    vm = _code(_VM)
    gen = _generate()
    assert gen.count(_TASK) == 1, (
        "generateAnalysis's monitor Task is not registered in `generationMonitors` with its owner")
    body = _task_body(gen)
    tap = _OWNER_AT_TAP.search(gen)
    assert tap and tap.start() < gen.index(_TASK) and not re.search(r"\blet\s+owner\b", body), (
        "the monitor's owner is not stamped at the tap (read inside the Task, a late first run "
        "would adopt the NEXT account)")
    helper = re.sub(r"\s+", " ", _block(vm, _HELPER))
    assert re.search(r"\bowner == AppActions\.shared\.currentAccountId\b", helper), (
        "`monitorMayAct` no longer compares its owner with the live `currentAccountId`")
    actions = _code(_ACTIONS)
    assert actions.count(_ACCOUNT) == 1, "`AppActions.currentAccountId` is gone"
    decl = actions[actions.index(_ACCOUNT):]
    decl = decl[: decl.index("\n")]
    assert re.fullmatch(r"var currentAccountId: String\? \{ appState\?\.user\.profile\?\.id \}", decl.strip()), (
        "`AppActions.currentAccountId` is not the profile id kept through `.restoring` "
        f"(gating it on the session status stops a reconnecting account's own work): {decl.strip()}")


# ── 3. no POST for a tap whose account has gone ─────────────────────────────────

def test_no_post_for_a_tap_whose_account_is_gone():
    body = _task_body(_generate())
    post = body.find("generateAndMonitorResearch(")
    check = _RECHECK.search(body)
    assert post >= 0, "the monitor no longer calls generateAndMonitorResearch — re-derive this guard"
    assert check and check.start() < post and not _AWAIT.search(body[:check.start()]), (
        "the monitor reaches `generateAndMonitorResearch` without re-checking `monitorMayAct(owner)`")


# ── 4. the loop's first statement is the re-check ───────────────────────────────

def test_every_arm_is_gated_at_the_top_of_the_loop():
    loop = _block(_task_body(_generate()), _LOOP)
    first = _RECHECK.match(loop[1:].lstrip())
    assert first and loop.index(_SWITCH) > loop.index("monitorMayAct(owner)"), (
        "the stream loop's first statement is not the `monitorMayAct(owner)` re-check")


# ── 5. no write after an await without a re-check ──────────────────────────────

def test_no_write_after_an_await_without_a_re_check():
    body = _task_body(_generate())
    loop = _block(body, _LOOP)
    assert loop.count(_SWITCH) == 1, "the stream loop no longer has one `switch progress`"
    scanned, bad = _unchecked_writes(loop, switch_open=loop.index("{", loop.index(_SWITCH)))
    # Anti-vacuity: today's arms hold 16 writes, three of them `startReportsPolling()`.
    assert scanned >= 10 and len(re.findall(r"self\.startReportsPolling\(", loop)) >= 3, (
        f"only {scanned} writes found in the stream loop — this scan has drifted")
    assert not bad, f"writes this ViewModel after an `await` with no account re-check: {bad}"
    scanned, bad = _unchecked_writes(_block(body, "} catch"))
    assert scanned >= 2, f"only {scanned} writes found in the monitor's catch — this scan has drifted"
    assert not bad, f"writes this ViewModel after an `await` with no account re-check: {bad}"
    retry_body = _block(_retry(), _RETRY_TASK)
    scanned, bad = _unchecked_writes(retry_body, entry_covered=True, recheck=_RETRY_CHECK)
    assert scanned >= 10 and "self.generateAnalysis()" in retry_body, (
        f"only {scanned} writes found in retryReport's Task — this scan has drifted")
    assert not bad, f"writes this ViewModel after an `await` with no account re-check: {bad}"


def test_the_write_scan_sees_through_branches_correctly():
    """The fall-through model on hand-written samples, so a vacuous `_falls_through` cannot hide
    behind the real source."""
    sample = (
        "{ switch p {\n"
        "case .a:\n"
        "    if x { await f(); continue }\n"          # does not fall through
        "    if y { await f() } else if z {\n"         # earlier branch of the chain W is in
        "        self.error = 1\n"
        "    }\n"
        "    self.startReportsPolling()\n"            # after the chain: `await f()` precedes it
        "case .b:\n"
        "    await f()\n"
        "    guard self.monitorMayAct(owner) else { return }\n"
        "    self.error = 2\n"
        "} }")
    scanned, bad = _unchecked_writes(sample, switch_open=sample.index("{", sample.index("switch p")))
    assert scanned == 3
    assert bad == ["self.startReportsPolling()"], bad
    # do/catch: the catch is entered from the do body's THROWING await, so a re-check placed
    # after that await inside the do body does not cover the catch; one inside the catch does.
    sample = '{ do { try await g()\n guard stillOwner("g") else { return }\n self.x = 1 } catch { self.error = 3 } }'
    scanned, bad = _unchecked_writes(sample, entry_covered=True, recheck=_RETRY_CHECK)
    assert scanned == 2 and len(bad) == 1 and "self.error = 3" in bad[0], bad
    fixed = sample.replace("catch {", 'catch { guard stillOwner("g") else { return }\n')
    assert _unchecked_writes(fixed, entry_covered=True, recheck=_RETRY_CHECK) == (2, [])


# ── 6. handleIdentityChange cancels other accounts' monitors first ─────────────

def test_an_identity_change_cancels_every_other_accounts_monitor_first():
    ident = _block(_code(_VM), _IDENTITY)
    read = re.search(r"\blet\s+account\s*=\s*AppActions\.shared\.currentAccountId\b", ident)
    keep = _KEEP_OWN.search(ident)
    cancel = _CANCEL_OTHERS.search(ident)
    assert read and keep and cancel and re.search(r"\bgenerationMonitors\s*=\s*kept\b", ident), (
        "handleIdentityChange does not cancel the monitors other accounts own")
    before = ident[1: cancel.start()]
    gate = ident.find("guard isActiveTab else { return }")
    clears = [ident.find(c) for c in _CLEARS]
    assert (min(clears) >= 0 and all(cancel.start() < c for c in clears) and 0 <= gate
            and read.start() < keep.start() < cancel.start() < gate
            and before.count("{") == before.count("}")
            and not re.search(r"\b(?:return|throw|guard|defer|await)\b", before)), (
        "handleIdentityChange must cancel the monitors BEFORE the clears, unconditionally")


# ── 7. slots survive only an unchanged account, and always record their owner ──

def test_slots_are_kept_only_for_the_same_account():
    ident = _block(_code(_VM), _IDENTITY)
    gate = ident.find("guard isActiveTab else { return }")
    drop = _SLOT_DROP_IF.search(ident)
    assert drop and 0 <= gate and drop.start() < gate, (
        "handleIdentityChange keeps the slots across an account change, or drops them on a reconnect")
    block = _block(ident[drop.start():], "if account")
    outside = ident.replace(block, "", 1)
    assert ("inFlightReportIds = []" in block and "liveProgress = [:]" in block
            and re.search(r"\binFlightOwnerId\s*=\s*nil\b", block)
            and not re.search(r"\b(?:inFlightReportIds|liveProgress|inFlightOwnerId)\s*=(?!=)", outside)), (
        "handleIdentityChange keeps the slots across an account change, or drops them on a reconnect")
    body = _task_body(_generate())
    inserts = list(re.finditer(r"self\.inFlightReportIds\.insert\((\w+)\)", body))
    assert len(inserts) >= 2, "the monitor no longer takes its slots — re-derive this guard"
    for m in inserts:
        nxt = body[m.end():].lstrip().split("\n", 1)[0].strip()
        assert nxt == "self.inFlightOwnerId = owner", (
            f"a slot is taken without recording its owner: `{m.group(0)}` is followed by `{nxt}`")
    writers = re.findall(r"\binFlightOwnerId\s*=(?!=)\s*([^\n]*)", _code(_VM))
    assert sorted(w.strip() for w in writers) == ["nil", "owner", "owner"], (
        f"a slot is taken without recording its owner (inFlightOwnerId writers: {writers})")


# ── 8. retryReport acts only for the account that tapped ───────────────────────

def test_a_retry_acts_only_for_the_account_that_tapped():
    retry = _retry()
    tap = _OWNER_AT_TAP.search(retry)
    assert tap and tap.start() < retry.index(_RETRY_TASK), "retryReport's owner is not stamped at the tap"
    body = _block(retry, _RETRY_TASK)
    helper = re.sub(r"\s+", " ", _block(body, "func stillOwner("))
    assert re.search(r"guard self\.monitorMayAct\(owner\) else \{ print\(", helper) and "return false" in helper, (
        "retry's `stillOwner` no longer checks `monitorMayAct(owner)` (or stops without a log)")
    charge = body.rindex("self.generateAnalysis()")
    checks = list(_RETRY_CHECK.finditer(body[:charge]))
    assert checks and not _AWAIT.search(body[checks[-1].end(): charge]), (
        "retryReport calls `generateAnalysis()` after an `await` with no `stillOwner` re-check")


# ── 9. a stopped monitor raises no alert ────────────────────────────────────────

def test_a_stopped_monitor_raises_no_alert():
    body = _task_body(_generate())
    checks = list(_RECHECK.finditer(body))
    assert len(checks) >= 5, f"only {len(checks)} `monitorMayAct(owner)` re-checks — re-derive this guard"
    for m in checks:
        start = body.index("{", m.end())
        depth = 0
        for i in range(start, len(body)):
            depth += {"{": 1, "}": -1}.get(body[i], 0)
            if depth == 0:
                break
        branch = body[start: i + 1]
        assert not (_WRITE.search(branch) or re.search(r"\b(?:error\s*=|Analytics|AppActions)", branch)), (
            "a monitor stopped by an account change raises an alert or writes state: "
            f"{' '.join(branch.split())}")
    failed = body[body.index("case .failed(let appError):"):] if "case .failed(let appError):" in body else ""
    cancelled = failed.find("if appError.isCancellation")
    analytics = failed.find("Analytics.shared.track(.reportFailed")
    alert = failed.find("self.error = appError.message")
    assert 0 <= cancelled < analytics and cancelled < alert, (
        "a cancelled monitor's `.failed(.cancelled)` reaches the failure analytics or the alert")


# ── 10. the /status poller ends with its consumer ───────────────────────────────

@pytest.mark.parametrize("fn", _POLL_FUNCS)
def test_the_status_poll_ends_with_its_consumer(fn):
    body = _block(_code(_POLL), fn)
    assert "AsyncThrowingStream" in body and ".getResearchStatus(reportId: reportId)" in body, (
        f"`{fn}` is not the live status poller — re-derive this guard")
    assert (len(re.findall(r"\bTask\s*\{", body)) == 1 and "let producer = Task {" in body
            and re.search(r"continuation\.onTermination\s*=\s*\{\s*_\s+in\s+producer\.cancel\(\)\s*\}", body)), (
        f"`{fn}`'s poller outlives its consumer (no `onTermination` cancel)")


# ── comment stripping and bounding are real ────────────────────────────────────

def test_comment_stripping_is_real_and_load_bearing():
    raw = _VM.read_text(encoding="utf-8")
    gen_raw = raw[raw.index(_GENERATE): raw.index(_HELPER)]
    # The fix's own comments name the re-check; stripping must remove them, or a presence scan
    # would pass on prose after a revert.
    assert gen_raw.count("monitorMayAct(owner)") > _strip_swift_comments(gen_raw).count("monitorMayAct(owner)"), (
        "generateAnalysis' comments no longer name `monitorMayAct(owner)` — fine, but then this "
        "anti-vacuity probe needs a new raw sample")
    ident_raw = raw[raw.index(_IDENTITY): raw.index("guard isActiveTab else { return }")]
    assert "generationMonitors`" in ident_raw and "generationMonitors`" not in _strip_swift_comments(ident_raw), (
        "handleIdentityChange's comment no longer names `generationMonitors` — refresh this probe")
    assert "no longer signed in" in _strip_swift_comments(gen_raw), "string literals were stripped"


@pytest.mark.parametrize("path,header", [
    (_VM, _GENERATE),
    (_VM, _RETRY),
    (_VM, _IDENTITY),
    (_POLL, _POLL_FUNCS[0]),
    (_POLL, _POLL_FUNCS[1]),
])
def test_each_scan_is_bounded_to_its_declaration(path, header):
    src = _code(path)
    block = _block(src, header)
    assert len(block) > 200, f"`{header}` block is only {len(block)} chars — the scan has drifted"
    assert len(block) < len(src) // 2, (
        f"`{header}` block is {len(block)} of {len(src)} chars — `_block` stopped bounding")


# ── mutations, re-run in memory on every pass ───────────────────────────────────

_T1 = test_each_generation_monitor_is_registered_and_deregisters
_T2 = test_the_monitor_is_stamped_with_the_account_that_tapped
_T3 = test_no_post_for_a_tap_whose_account_is_gone
_T4 = test_every_arm_is_gated_at_the_top_of_the_loop
_T5 = test_no_write_after_an_await_without_a_re_check
_T6 = test_an_identity_change_cancels_every_other_accounts_monitor_first
_T7 = test_slots_are_kept_only_for_the_same_account
_T8 = test_a_retry_acts_only_for_the_account_that_tapped
_T9 = test_a_stopped_monitor_raises_no_alert


def _t10_generate():
    test_the_status_poll_ends_with_its_consumer(_POLL_FUNCS[0])


def _t10_monitor():
    test_the_status_poll_ends_with_its_consumer(_POLL_FUNCS[1])


_I28 = " " * 28
_LOOP_TOP = (
    "                    guard self.monitorMayAct(owner) else {\n"
    "                        print(\"🛑 ResearchVM: monitor for \\(ticker) outlived its identity — stopped\")\n"
    "                        return\n"
    "                    }\n")
_PRE_POST = (
    "            guard self.monitorMayAct(owner) else {\n"
    "                print(\"🛑 ResearchVM: generation for \\(ticker) dropped — the account that tapped is no longer signed in\")\n"
    "                return\n"
    "            }\n")
_CANCEL_LINES = (
    "        let account = AppActions.shared.currentAccountId\n"
    "        let kept = generationMonitors.filter { $0.value.ownerId == account }\n"
    "        for (key, monitor) in generationMonitors where kept[key] == nil { monitor.task.cancel() }\n"
    "        generationMonitors = kept\n")
_GATE_LINE = "        guard isActiveTab else { return }\n"
_SLOT_IF = "        if account == nil || account != inFlightOwnerId {\n"
_RETRY_TAP = (
    "        guard let owner = AppActions.shared.currentAccountId else {\n"
    "            print(\"❌ ResearchVM: retryReport — signed in with no profile id; refusing the tap\")\n")
_ON_TERM = "            continuation.onTermination = { _ in producer.cancel() }\n"
_GEN_TERM_NOTE = "            // polling `/status` with whatever token was armed next until `maxPollDuration`.\n"
_MON_TERM_NOTE = "            // Same as `generateAndMonitorResearch`: the poll ends with its consumer.\n"
_W = "writes this ViewModel after an `await` with no account re-check"

_MUTATIONS = [
    # ── 1. registry ──
    ("G1-unregistered-monitor", _VM,
     (("        generationMonitors[monitorKey] = GenerationMonitor(ownerId: owner, task: task)\n", ""),),
     _T1, "generateAnalysis's monitor Task is not registered in `generationMonitors` with its owner"),
    ("G2-never-deregisters", _VM,
     (("            defer { self.generationMonitors[monitorKey] = nil }\n", ""),),
     _T1, "the monitor Task never removes itself from `generationMonitors`"),
    ("G3-second-unregistered-task", _VM,
     (("        let monitorKey = UUID()\n",
       "        Task { await self.loadCredits() }\n        let monitorKey = UUID()\n"),),
     _T1, "generateAnalysis starts a Task outside `generationMonitors`"),
    ("G4-record-loses-its-owner", _VM,
     (("        let ownerId: String\n", "        var note: String\n"),),
     _T1, "`GenerationMonitor` no longer carries its owner and its Task"),
    # ── 2. the stamp ──
    ("O1-owner-read-inside-the-task", _VM,
     (("            defer { self.generationMonitors[monitorKey] = nil }\n",
       "            defer { self.generationMonitors[monitorKey] = nil }\n"
       "            let owner = AppActions.shared.currentAccountId ?? \"\"\n"),),
     _T2, "the monitor's owner is not stamped at the tap"),
    ("O2-helper-ignores-the-owner", _VM,
     (("        owner == AppActions.shared.currentAccountId && !Task.isCancelled\n", "        !Task.isCancelled\n"),),
     _T2, "`monitorMayAct` no longer compares its owner with the live `currentAccountId`"),
    ("O3-account-gated-on-sign-in", _ACTIONS,
     (("    var currentAccountId: String? { appState?.user.profile?.id }\n",
       "    var currentAccountId: String? { isSignedIn ? appState?.user.profile?.id : nil }\n"),),
     _T2, "`AppActions.currentAccountId` is not the profile id kept through `.restoring`"),
    # ── 3. no POST for a gone account ──
    ("P1-pre-post-recheck-removed", _VM,
     ((_PRE_POST, ""),),
     _T3, "the monitor reaches `generateAndMonitorResearch` without re-checking"),
    # ── 4. loop top ──
    ("L1-loop-top-recheck-removed", _VM,
     ((_LOOP_TOP, ""),),
     _T4, "the stream loop's first statement is not the `monitorMayAct(owner)` re-check"),
    ("L2-loop-top-recheck-after-a-write", _VM,
     ((_LOOP_TOP, "                    self.applyLiveProgress()\n" + _LOOP_TOP),),
     _T4, "the stream loop's first statement is not the `monitorMayAct(owner)` re-check"),
    # ── 5. writes after an await ──
    ("W1-poll-timeout-recheck-removed", _VM,
     ((f"{_I28}guard self.monitorMayAct(owner) else {{ return }}\n{_I28}self.startReportsPolling()\n",
       f"{_I28}self.startReportsPolling()\n"),),
     _T5, _W),
    ("W2-post-timeout-recheck-removed", _VM,
     ((f"{_I28}// Never adopt from, or alert over, the NEXT account's list.\n"
       f"{_I28}guard self.monitorMayAct(owner) else {{ return }}\n", ""),),
     _T5, _W),
    ("W3-recheck-before-the-await", _VM,
     ((f"{_I28}await self.loadReports()\n"
       f"{_I28}// The identity can end during that await; its poll was reset then.\n"
       f"{_I28}guard self.monitorMayAct(owner) else {{ return }}\n",
       f"{_I28}guard self.monitorMayAct(owner) else {{ return }}\n"
       f"{_I28}await self.loadReports()\n"),),
     _T5, _W),
    ("W4-new-write-after-an-await", _VM,
     (("                        await self.loadCredits()\n\n                    case .failed(let appError):",
       "                        await self.loadCredits()\n                        self.error = nil\n\n"
       "                    case .failed(let appError):"),),
     _T5, _W),
    ("W5-catch-recheck-removed", _VM,
     (("                guard self.monitorMayAct(owner) else { return }\n                if let id = startedId {",
       "                if let id = startedId {"),),
     _T5, _W),
    ("W6-retry-post-delete-recheck-removed", _VM,
     (("                    guard stillOwner(\"the delete\") else { return }\n"
       "                    print(\"🔄 ResearchVM: released prior report",
       "                    print(\"🔄 ResearchVM: released prior report"),),
     _T5, _W),
    ("W7-retry-catch-recheck-removed", _VM,
     (("                    guard stillOwner(\"the delete\") else { return }\n"
       "                    let appError = AppError.from(error)\n",
       "                    let appError = AppError.from(error)\n"),),
     _T5, _W),
    ("W8-retry-credits-recheck-removed", _VM,
     (("                guard stillOwner(\"the credits read\") else { return }\n", ""),),
     _T5, _W),
    # ── 6. cancel on identity change ──
    ("C1-identity-change-keeps-every-monitor", _VM,
     (("        let kept = generationMonitors.filter { $0.value.ownerId == account }\n",
       "        let kept = generationMonitors.filter { _ in true }\n"),),
     _T6, "handleIdentityChange does not cancel the monitors other accounts own"),
    ("C2-cancel-after-the-clears", _VM,
     ((_CANCEL_LINES, ""),
      ("        dismissedReportIds = []\n", "        dismissedReportIds = []\n" + _CANCEL_LINES)),
     _T6, "handleIdentityChange must cancel the monitors BEFORE the clears, unconditionally"),
    ("C3-cancel-only-on-the-active-tab", _VM,
     ((_CANCEL_LINES, "        let account = AppActions.shared.currentAccountId\n"),
      (_GATE_LINE, _GATE_LINE + _CANCEL_LINES.split("\n", 1)[1])),
     _T6, "handleIdentityChange must cancel the monitors BEFORE the clears, unconditionally"),
    ("C4-cancel-behind-an-await", _VM,
     ((_CANCEL_LINES, "        await Task.yield()\n" + _CANCEL_LINES),),
     _T6, "handleIdentityChange must cancel the monitors BEFORE the clears, unconditionally"),
    # ── 7. slots ──
    ("S1-slots-kept-across-accounts", _VM,
     ((_SLOT_IF, "        if account == nil {\n"),),
     _T7, "handleIdentityChange keeps the slots across an account change, or drops them on a reconnect"),
    ("S2-slots-dropped-on-reconnect", _VM,
     ((_SLOT_IF, "        if true {\n"),),
     _T7, "handleIdentityChange keeps the slots across an account change, or drops them on a reconnect"),
    ("S3-slot-owner-not-recorded", _VM,
     (("                        self.inFlightReportIds.insert(taskId)\n"
       "                        self.inFlightOwnerId = owner\n",
       "                        self.inFlightReportIds.insert(taskId)\n"),),
     _T7, "a slot is taken without recording its owner"),
    ("S4-slots-cleared-outside-the-branch", _VM,
     ((_SLOT_IF, "        liveProgress = [:]\n" + _SLOT_IF),),
     _T7, "handleIdentityChange keeps the slots across an account change, or drops them on a reconnect"),
    # ── 8. retry ──
    ("R1-retry-owner-not-stamped", _VM,
     ((_RETRY_TAP, "        let owner: String? = AppActions.shared.currentAccountId\n        guard let owner else {\n"
       "            print(\"❌ ResearchVM: retryReport — signed in with no profile id; refusing the tap\")\n"),),
     _T8, "retryReport's owner is not stamped at the tap"),
    ("R2-stillOwner-checks-nothing", _VM,
     (("                guard self.monitorMayAct(owner) else {\n"
       "                    print(\"🛑 ResearchVM: retry for",
       "                guard true else {\n"
       "                    print(\"🛑 ResearchVM: retry for"),),
     _T8, "retry's `stillOwner` no longer checks `monitorMayAct(owner)`"),
    ("R3-retry-charges-unchecked", _VM,
     (("            // The last step charges 20 credits to whoever holds the session NOW.\n"
       "            guard stillOwner(\"the delete\") else { return }\n", ""),),
     _T8, "retryReport calls `generateAnalysis()` after an `await` with no `stillOwner` re-check"),
    # ── 9. no alert ──
    ("A1-stopped-monitor-alerts", _VM,
     (("                        print(\"🛑 ResearchVM: monitor for \\(ticker) outlived its identity — stopped\")\n",
       "                        print(\"🛑 ResearchVM: monitor for \\(ticker) outlived its identity — stopped\")\n"
       "                        self.error = \"This analysis stopped.\"\n"),),
     _T9, "a monitor stopped by an account change raises an alert or writes state"),
    ("A2-cancelled-stream-alerts", _VM,
     (("                        if appError.isCancellation {\n"
       "                            print(\"🛑 ResearchVM: monitor for \\(ticker) cancelled — no alert\")\n"
       "                            continue\n"
       "                        }\n", ""),),
     _T9, "a cancelled monitor's `.failed(.cancelled)` reaches the failure analytics or the alert"),
    # ── 10. the poller ends with its consumer ──
    ("T1-generate-poller-outlives-consumer", _POLL,
     ((_GEN_TERM_NOTE + _ON_TERM, _GEN_TERM_NOTE),),
     _t10_generate, "`func generateAndMonitorResearch(`'s poller outlives its consumer"),
    ("T2-termination-cancels-nothing", _POLL,
     ((_GEN_TERM_NOTE + _ON_TERM, _GEN_TERM_NOTE + "            continuation.onTermination = { _ in }\n"),),
     _t10_generate, "`func generateAndMonitorResearch(`'s poller outlives its consumer"),
    ("T3-monitor-poller-outlives-consumer", _POLL,
     ((_MON_TERM_NOTE + _ON_TERM, _MON_TERM_NOTE),),
     _t10_monitor, "`func monitorResearch(reportId: String)`'s poller outlives its consumer"),
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
    for guard in (_T1, _T2, _T3, _T4, _T5, _T6, _T7, _T8, _T9, _t10_generate, _t10_monitor):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"
    names = [m[0] for m in _MUTATIONS]
    assert len(names) == len(set(names)), "duplicate mutation ids"
