"""Research › Reports and the credit badge decide the account gate from APIClient's REFUSAL,
never from a pre-flight `auth.status` read.

TestFlight 1.0 (9) follow-up. `ResearchViewModel.loadReports()` opened with
`guard AppActions.shared.isSignedIn`, and `loadCredits()` had the same guard. At launch
`AppState.primeStoredCredential` ARMS the token while the status still reads `.restoring`, so
that guard refused a request that would have succeeded, drew "Reconnecting…", and nothing re-ran
the load once the restore finished: `resolveIdentity` treats the first resolution as a discovery
(no `identityGeneration` bump, so `.reloadOnIdentityChange` stays quiet), `.task(id: isActiveTab)`
had already run, and the 5 s poll exits on an empty list. The tab stayed stuck until a
pull-to-refresh.

The fix mirrors `HomeDashboardViewModel.performLoad` / `TrackingViewModel.loadTrackingFeed`:
send the request, and decide from the OUTCOME. `.getMyReports` and `.getUserCredits` are
`.signInRequired`, so an UNARMED call is refused by `APIClient.buildRequest` before any I/O
(never sent as a guest) and arrives typed as `AppError.signInRequired`. What is pinned here:

1. `loadReports` has no status read; its typed-refusal arm drops the rows, picks
   "Reconnecting…" vs Sign In from `let reconnecting = AppActions.shared.isRestoringSession`
   (exactly that read, never inverted), writes each flag once, returns before the sync-failed
   analytics and the alert, and never stamps `lastLoadedAt`.
2. The gate flags are written only from an outcome, and only from a NEWEST one: a success clears
   them after its superseded-answer guard, a real failure clears them after the arm, nothing
   writes them before or inside either stale guard, each path writes each flag once, and no
   member but `loadReports` writes them at all.
3. Review D1/D4: the refusal ADVANCES `reportsAppliedSeq` (an older armed reply landing later
   must not lift the newer gate) and a real failure does NOT; a superseded outcome is logged,
   never swallowed.
4. `loadCredits` the same way (refusal arm sets `creditBalance = nil`).
5. Only an ungated pass is stamped fresh, and (review D2) never a pass that straddled an identity
   change: `performBackendLoad` captures `identityEpoch` before its loads and re-checks it before
   the byte-identical `if !requiresSignInForReports && !isReconnectingReports {`;
   `handleIdentityChange` bumps it before the `isActiveTab` early return. That stamp is the ONLY
   `lastLoadedAt = Date()` in the file (no other member re-stamps a refused pass, no inout or
   key-path detour, the property stays private), and the identity change clears it before the
   `isActiveTab` return.
6. B4: `handleIdentityChange` WAITS OUT an in-flight load (whose list answer it just marked
   superseded) instead of joining it, then reloads UNCONDITIONALLY (never only inside the wait),
   then re-arms the poll it stopped.
7. The user-TAP gates (`generateAnalysis`, `retryReport`) deliberately keep
   `guard AppActions.shared.isSignedIn` — a 20-credit write must not fire on an unvalidated
   session, and `requestSignIn` turns the tap into a "Reconnecting your account…" toast.
8. `APIClient.request<T>` builds the request BEFORE its `do {`, so a pre-flight refusal goes
   straight back to the caller and never reaches `handleUnrecoverableAuthFailure` — otherwise
   every refused 5 s poll tick would kick a restore and defeat its backoff.
9. Those two tap gates and the refusal arm's `isRestoringSession` are the ONLY session reads in
   the file: a pre-flight gate moved one level up (`performBackendLoad`, `loadIfStale`, the poll
   loop, a helper or computed property the fan-out calls) brings the bug back unseen by 1 and 4.
10. `loadCredits` drops an answer asked for by the previous identity: `let epoch =
    identityEpoch` before the request, `guard epoch == identityEpoch else { print…; return }`
    after it, and every `creditBalance` write in the success path after that guard. Its refusal
    arm carries the same epoch guard, ahead of its `creditBalance = nil`, so a refusal asked
    for under the previous identity cannot hide the new account's balance.
11. The gate flags are READ in ResearchViewModel only by `performBackendLoad`'s freshness stamp
    (`loadReports` only writes them): a pre-flight `guard !requiresSignInForReports …` returns
    before `loadReports`, the only writer, so the gate would latch through sign-in.
12. Both flags are `@Published private(set) var`, so a view cannot write the gate from a
    pre-flight auth read — the compiler refuses it.
13. In ContentView's `ResearchViewWithBinding`, no `.task(id: isActiveTab)` block reads the
    session (`auth.status`, `isAuthenticated`, `isSignedIn`, `isRestoringSession`,
    `hasUnusedStoredCredential`): gating the activation load on it skips the load while the
    session is `.restoring`, and nothing re-runs it.

Review D6: the optional ContentView `onChange(of: appState.auth.status)` trigger (B3) was
DROPPED — a refusal needs a disarmed token, the disarm bumps `identityGeneration`, so the heal
always reaches `handleIdentityChange` through `.reloadOnIdentityChange`. Nothing here pins B3.

There is no XCTest target (testing.md §3), so this pins the Swift source: comments are stripped
before every assertion (the fix's own comments name `isSignedIn`, `auth.status` and every flag),
every check is brace-bound to the declaration it means, and presence is asserted before any
block is sliced out of it.

Mutation-tested IN MEMORY (``pathlib.Path.read_text`` monkeypatched for the one target file —
the real Swift files are never touched; other agents read them concurrently). The table runs on
every pass as ``test_each_mutation_is_killed``, and each mutation must fail with the assertion
message that names it, so a mutation cannot "pass" by tripping an unrelated earlier check.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_API = _IOS / "Core" / "Services" / "APIClient.swift"
_CONTENT = _IOS / "ContentView.swift"

# Any pre-flight read of the session status. `isRestoringSession` is deliberately NOT here: it
# is the correct read INSIDE the refusal arm (which of the two gates to draw).
_STATUS = r"\bisSignedIn\b|\bisAuthenticated\b|auth\.status"

_LOAD_REPORTS = "func loadReports() async"
_LOAD_CREDITS = "func loadCredits() async"
_PERFORM = "private func performBackendLoad() async"
_IDENTITY = "func handleIdentityChange(isActiveTab: Bool) async"

_ARM = "if case .signInRequired = appError"
_CREDITS_ARM = "if case .signInRequired = AppError.from(error)"
_STALE = "guard seq > reportsAppliedSeq else"
_GATE_IF = "if !requiresSignInForReports && !isReconnectingReports"
_ACTIVE_GUARD = "guard isActiveTab else { return }"
_FLAGS = ("requiresSignInForReports", "isReconnectingReports")
_FLAG_WRITE = r"\b(?:requiresSignInForReports|isReconnectingReports)\s*=(?!=)"
_CREDITS_EPOCH = "let epoch = identityEpoch"
_CREDITS_EPOCH_GUARD = "guard epoch == identityEpoch else"
_RESEARCH_VIEW = "struct ResearchViewWithBinding: View"
_ACTIVATION_TASK = r"\.task\s*\(\s*id:\s*isActiveTab\s*\)"
# Section 13: every session accessor a view could gate the activation load on.
_SESSION_READ = _STATUS + r"|\bisRestoringSession\b|\bhasUnusedStoredCredential\b"
# The two deliberate user-TAP gates (section 7): (declaration, requestSignIn reason).
_TAP_GATES = (("func generateAnalysis()", "generate AI analysis"),
              ("func retryReport(_ report: AnalysisReport)", "retry this analysis"))


def _tap_guard_re(reason: str) -> str:
    return (r"guard\s+AppActions\.shared\.isSignedIn\s+else\s*\{\s*"
            rf'AppActions\.shared\.requestSignIn\(for:\s*"{re.escape(reason)}"\)\s*return\s*\}}')


def _writes(src: str, name: str) -> list[str]:
    """The right-hand side of every plain assignment to `name` (`==` excluded)."""
    return [w.strip() for w in re.findall(rf"\b{name}\s*=(?!=)\s*([^\n]*)", src)]


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the fix's own comments name `isSignedIn`, `auth.status`, `lastLoadedAt` and
    both gate flags while explaining why the code no longer does what they describe, so an
    un-stripped scan for their ABSENCE fails on prose and a scan for their PRESENCE passes on a
    revert whose comment survived. A tail needs leading whitespace, so a `https://` inside a
    string literal is not cut.
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


def _block(src: str, header: str, open_: str = "{", close: str = "}") -> str:
    """The balanced `open_`…`close` body that follows the ONLY `header` (a literal prefix)."""
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    start = src.index(open_, src.index(header) + len(header))
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced `{open_}{close}` after `{header}`")


def _after_block(src: str, header: str) -> str:
    """Everything after the closing brace of `_block(src, header)`."""
    body = _block(src, header)
    start = src.index("{", src.index(header) + len(header))
    return src[start + len(body):]


def _without_block(src: str, header: str) -> str:
    """`src` with the `header` and its balanced body cut out."""
    body = _block(src, header)
    at = src.index(header)
    start = src.index("{", at + len(header))
    return src[:at] + src[start + len(body):]


def _blocks(src: str, pattern: str) -> list[str]:
    """The balanced `{…}` body after EVERY match of the regex `pattern` (e.g. each of several
    `.task(id: isActiveTab) {` closures, which `_block`'s exactly-one rule cannot slice)."""
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


def _line_of(src: str, m: re.Match | None) -> str | None:
    """The stripped source line holding match `m` (for a legible failure message)."""
    if m is None:
        return None
    end = src.find("\n", m.end())
    return src[src.rfind("\n", 0, m.start()) + 1: end if end != -1 else len(src)].strip()


def _load() -> str:
    load = _block(_code(_VM), _LOAD_REPORTS)
    # Anti-vacuity: the live list load, not a same-named stub.
    assert ".getMyReports(limit: 50)" in load and "self.reports = backendReports" in load, (
        "the loadReports block is not the live list load — this guard is reading the wrong code")
    return load


def _do_and_catch(load: str) -> tuple[str, str]:
    assert load.count("do {") == 1 and load.count("} catch {") == 1, (
        "loadReports no longer has exactly one do/catch — re-derive these scans")
    do_part = load[load.index("do {"): load.index("} catch {")]
    return do_part, _block(load, "} catch")


# ── 1. loadReports decides the gate from the typed refusal ───────────────────


def test_load_reports_decides_the_gate_from_the_refusal():
    load = _load()
    m = re.search(_STATUS, load)
    assert not m, (
        f"loadReports decides the gate from auth.status again (`{m and m.group(0)}`): on every "
        "signed-in cold launch the token is armed while the status reads .restoring, so the "
        "guard refuses a request that would have succeeded and latches Reconnecting")

    _, catch = _do_and_catch(load)
    assert catch.count(_ARM) == 1, (
        "loadReports has no typed-refusal arm (`if case .signInRequired = appError`), so a "
        "refused pass is flattened into a network failure and can never draw the account gate")
    arm = _block(catch, _ARM)

    assert load.count("isRestoringSession") == 1 and "isRestoringSession" in arm, (
        "isRestoringSession is read outside the typed-refusal arm — a pre-flight session read "
        "decides the gate again instead of APIClient's refusal")
    # A substring test passes on `!AppActions.shared.isRestoringSession` or `… == false`: pin the
    # whole statement, ending at the line break (auth.md §5 — Reconnecting for a healing
    # credential, Sign In only for a signed-out user).
    assert re.search(r"\blet\s+reconnecting\s*=\s*AppActions\.shared\.isRestoringSession\s*\n", arm), (
        "the refused pass inverts or replaces the restoring read (`let reconnecting = "
        "AppActions.shared.isRestoringSession` exactly): a restoring user would get Sign In and a "
        "signed-out user a permanent Reconnecting")

    assert re.search(r"\breports\s*=\s*\[\]", arm), (
        "a refused pass keeps rows it can no longer refresh")
    assert re.search(r"\bisReconnectingReports\s*=\s*reconnecting\b", arm), (
        "the refused pass does not set isReconnectingReports from isRestoringSession")
    assert re.search(r"\brequiresSignInForReports\s*=\s*!reconnecting\b", arm), (
        "the refused pass offers Sign In to a reconnecting user (requiresSignInForReports must "
        "be !reconnecting)")
    # The two presence checks above cannot see a later overwrite inside the same arm.
    for flag in _FLAGS:
        n = len(re.findall(rf"\b{flag}\s*=(?!=)", arm))
        assert n == 1, (
            f"the refused pass overwrites a gate flag it just set (`{flag}` is written {n}x in the "
            "typed-refusal arm) — the last write wins, not the isRestoringSession decision")
    assert re.search(r"\breturn\s*\}\s*$", arm), (
        "the refused pass falls through into the sync-failed analytics and the error alert")
    assert "lastLoadedAt" not in arm, (
        "the refused pass stamps lastLoadedAt, so the 300 s window suppresses the reload that "
        "heals the gate")
    # Review D5: the ordering checks below cannot see a call written INSIDE the arm.
    assert "Analytics.shared.track" not in arm, (
        "the typed-refusal arm itself tracks a sync failure — the 5 s poll would emit one per "
        "refused tick")
    assert "self.error" not in arm, (
        "the typed-refusal arm itself sets self.error — an Error alert over the account gate")

    analytics_at = catch.find("Analytics.shared.track(.backgroundSyncFailed")
    alert_at = catch.find("self.error = appError.message")
    assert analytics_at != -1 and alert_at != -1, (
        "the real-failure path lost its sync-failed analytics or its alert — the ordering "
        "checks below are vacuous")
    arm_at = catch.index(_ARM)
    assert arm_at < analytics_at, (
        "the refusal is counted as a sync failure: the backgroundSyncFailed call runs before "
        "the typed-refusal arm")
    assert arm_at < alert_at, (
        "the refusal raises the error alert: self.error is assigned before the typed-refusal arm")


# ── 2. The flags are written only from an outcome ────────────────────────────


def test_the_gate_flags_are_written_only_from_an_outcome():
    load = _load()
    assert load.count("apiClient.request(") == 1, "loadReports no longer sends exactly one request"
    before = load[: load.index("apiClient.request(")]
    assert not re.search(r"\b(requiresSignInForReports|isReconnectingReports)\s*=(?!=)", before), (
        "the gate flags are written before the request — a pre-flight clear lifts a real gate "
        "for the whole round trip, and a pre-flight set is the bug this fix removed")

    do_part, catch = _do_and_catch(load)
    assert do_part.count(_STALE) == 1, (
        "the success path lost its superseded-answer guard — re-derive this scan")
    after_guard = _after_block(do_part, _STALE)
    for flag in _FLAGS:
        assert re.search(rf"\b{flag}\s*=\s*false\b", do_part), (
            f"a successful load does not clear the gate (`{flag} = false` missing), so a latched "
            "Reconnecting survives the load that disproved it")
        assert re.search(rf"\b{flag}\s*=\s*false\b", after_guard), (
            f"a dropped (stale) success clears the gate: `{flag} = false` runs before or inside "
            "the superseded-answer guard")
    # The presence checks above cannot see a write DUPLICATED ahead of the guard, or one written
    # inside its `else` — both run for an answer the guard then drops.
    upto_guard = do_part[: do_part.index(_STALE)] + _block(do_part, _STALE)
    m = re.search(_FLAG_WRITE, upto_guard)
    assert not m, (
        f"a superseded success still writes a gate flag (`{m and m.group(0)}` before or inside "
        "the success path's superseded-answer guard) — an older armed answer lifts a newer gate")
    for flag in _FLAGS:
        n = len(re.findall(rf"\b{flag}\s*=(?!=)", do_part))
        assert n == 1, (
            f"a successful load writes `{flag}` {n}x — the clear after the superseded-answer "
            "guard must be its only write, or a later one re-latches the gate it just disproved")

    assert catch.count(_ARM) == 1, "loadReports has no typed-refusal arm"
    after_arm = _after_block(catch, _ARM)
    for flag in _FLAGS:
        assert re.search(rf"\b{flag}\s*=\s*false\b", after_arm), (
            f"a real failure leaves a latched account gate in place (`{flag} = false` missing "
            "after the typed-refusal arm)")
    assert catch.count(_STALE) == 1 and catch.index(_STALE) < catch.index(_ARM), (
        "the catch lost its superseded-outcome guard ahead of the typed-refusal arm — re-derive "
        "this scan")
    upto_catch_guard = catch[: catch.index(_STALE)] + _block(catch, _STALE)
    m = re.search(_FLAG_WRITE, upto_catch_guard)
    assert not m, (
        f"a cancelled or superseded failure still writes a gate flag (`{m and m.group(0)}` before "
        "or inside the catch's superseded-outcome guard) — a dropped outcome moves a newer gate")
    for flag in _FLAGS:
        n = len(re.findall(rf"\b{flag}\s*=(?!=)", after_arm))
        assert n == 1, (
            f"a real failure writes `{flag}` {n}x after the typed-refusal arm — the clear must be "
            "its only write, or a failure re-latches an account gate it says nothing about")

    # Only an outcome of the list load decides the gate: no other member (an identity reset, the
    # fan-out, the poll) may write either flag, before or after a load.
    elsewhere = _without_block(_code(_VM), _LOAD_REPORTS)
    m = re.search(_FLAG_WRITE, elsewhere)
    assert not m, (
        f"a gate flag is written outside loadReports (`{m and m.group(0)}`) — the gate is "
        "decided only by a list outcome, never pre-flight by another member")


# ── 3. Newest outcome wins (review D1 / D4) ──────────────────────────────────


def test_a_stale_outcome_cannot_move_a_newer_gate():
    load = _load()
    do_part, catch = _do_and_catch(load)
    assert catch.count(_ARM) == 1, "loadReports has no typed-refusal arm"

    assert catch.count(_STALE) == 1 and catch.index(_STALE) < catch.index(_ARM), (
        "a refusal older than the last applied success can clear a newer list: the catch has no "
        "superseded-outcome guard ahead of the typed-refusal arm")
    assert "print(" in _block(catch, _STALE), (
        "a superseded list outcome is dropped silently — the catch's stale guard must log it")

    arm = _block(catch, _ARM)
    assert re.search(r"\breportsAppliedSeq\s*=\s*seq\b", arm), (
        "a refusal does not advance reportsAppliedSeq, so an older armed failure can lift the "
        "newer gate")
    outside = _without_block(catch, _ARM)
    assert not re.search(r"\breportsAppliedSeq\s*=(?!=)", outside), (
        "a real failure advances reportsAppliedSeq — only a success or a refusal is the newest "
        "truth; a network failure must not shadow an answer still in flight")

    assert do_part.count(_STALE) == 1, "the success path lost its superseded-answer guard"
    assert "print(" in _block(do_part, _STALE), (
        "a superseded list answer is dropped silently — the success path's stale guard must log it")


# ── 4. loadCredits, the same way ─────────────────────────────────────────────


def test_load_credits_decides_from_the_refusal():
    credits = _block(_code(_VM), _LOAD_CREDITS)
    assert ".getUserCredits" in credits and "CreditBalance.from(backendCredits)" in credits, (
        "the loadCredits block is not the live credits load — this guard is reading the wrong code")
    m = re.search(_STATUS, credits)
    assert not m, (
        f"loadCredits decides from auth.status again (`{m and m.group(0)}`), hiding the balance "
        "for an armed session that is still .restoring")
    assert credits.count(_CREDITS_ARM) == 1, (
        "loadCredits has no typed-refusal arm (`if case .signInRequired = AppError.from(error)`)")
    arm = _block(credits, _CREDITS_ARM)
    assert re.search(r"\bcreditBalance\s*=\s*nil\b", arm), (
        "a refused credits pass keeps a balance it can no longer refresh")
    assert re.search(r"\breturn\s*\}\s*$", arm), (
        "a refused credits pass falls through into the failure log")
    fail_log = credits.find('print("⚠️ ResearchVM: Failed to load credits')
    assert fail_log != -1 and credits.index(_CREDITS_ARM) < fail_log, (
        "a refused credits pass is logged as a load failure — the refusal arm must come first")


# ── 5. Only an ungated, same-identity pass is stamped fresh (review D2) ──────


def test_only_an_ungated_pass_is_stamped_fresh():
    vm = _code(_VM)
    perf = _block(vm, _PERFORM)
    assert "async let reportsTask: () = loadReports()" in perf, (
        "performBackendLoad no longer fans out loadReports — re-derive this scan")
    assert perf.count(_GATE_IF) == 1, (
        "performBackendLoad lost its `if !requiresSignInForReports && !isReconnectingReports` check")
    stamp = _block(perf, _GATE_IF)
    assert perf.count("lastLoadedAt") == 1 and "lastLoadedAt" in stamp, (
        "lastLoadedAt is stamped outside the gate check, so a refused pass is marked fresh and "
        "the 300 s window suppresses the reload that heals it")
    # The check above is bounded to performBackendLoad. FILE-wide (a same-file extension shares
    # `private`): exactly one fresh stamp — the gated one above — and the identity reset's clear.
    writes = sorted(_writes(vm, "lastLoadedAt"))
    assert writes == ["Date()", "nil"], (
        f"lastLoadedAt is written by another member as well ({writes}) — only performBackendLoad's "
        "gate check may stamp a pass fresh; a stamp in refresh() or loadBackendData() marks a "
        "refused pass fresh again")
    m = re.search(r"&\s*(?:self\.)?lastLoadedAt\b|\\(?:ResearchViewModel)?\.lastLoadedAt\b", vm)
    assert not m, (
        f"lastLoadedAt is written through a detour the assignment scan cannot see (`{m and m.group(0)}`"
        " — an inout argument or a key path)")
    assert re.search(r"(?m)^\s*private var lastLoadedAt: Date\?\s*$", vm), (
        "lastLoadedAt is no longer private — another file (a view, the tab container) could stamp "
        "a refused pass fresh where no scan here looks")

    assert "let epoch = identityEpoch" in perf and (
        perf.index("let epoch = identityEpoch") < perf.index("async let reportsTask")), (
        "performBackendLoad captures the identity epoch after its loads have started, so a load "
        "that straddled an identity change compares equal")
    assert re.search(
        r"guard\s+epoch\s*==\s*identityEpoch\s+else\s*\{\s*return\s*\}\s*"
        r"if !requiresSignInForReports && !isReconnectingReports \{", perf), (
        "a load that straddled an identity change is stamped fresh: `guard epoch == "
        "identityEpoch` must sit directly before the gate check")

    hic = _block(vm, _IDENTITY)
    assert hic.count(_ACTIVE_GUARD) == 1, (
        "handleIdentityChange no longer has its `guard isActiveTab else { return }` — re-derive")
    assert hic.count("identityEpoch &+= 1") == 1, (
        "handleIdentityChange never bumps identityEpoch (or bumps it twice)")
    assert hic.index("identityEpoch &+= 1") < hic.index(_ACTIVE_GUARD), (
        "the identity epoch is bumped only for the active tab, so a hidden tab's straddling "
        "load is stamped fresh and activation skips the new account's load")
    assert re.search(r"\blastLoadedAt\s*=\s*nil\b", hic[: hic.index(_ACTIVE_GUARD)]), (
        "a hidden tab keeps the previous identity's freshness stamp: `lastLoadedAt = nil` must "
        "run before the isActiveTab return, or activation skips the new account's load")


# ── 6. B4: an identity change waits out an in-flight load, then reloads ──────


def test_an_identity_change_reloads_behind_an_in_flight_load():
    hic = _block(_code(_VM), _IDENTITY)
    assert "applyDefaultPersona(force: true)" in hic and "stopReportsPolling()" in hic, (
        "the handleIdentityChange block is not the live identity reset")
    assert hic.count(_ACTIVE_GUARD) == 1, (
        "handleIdentityChange no longer has its `guard isActiveTab else { return }` — re-derive")
    pre = hic[: hic.index(_ACTIVE_GUARD)]
    tail = hic[hic.index(_ACTIVE_GUARD) + len(_ACTIVE_GUARD):]

    assert re.search(r"\breportsAppliedSeq\s*=\s*reportsRequestSeq\b", pre), (
        "handleIdentityChange lets the previous identity's in-flight list answers land in the "
        "new one (reportsAppliedSeq = reportsRequestSeq must run before the isActiveTab return)")

    assert "await loadBackendData()" in tail, "handleIdentityChange no longer reloads the active tab"
    wait = "if let running = loadTask"
    assert tail.count(wait) == 1 and "await running.value" in _block(tail, wait) and (
        tail.index(wait) < tail.index("await loadBackendData()")), (
        "handleIdentityChange joins an in-flight load whose reports answer it just marked "
        "superseded, so the reload ends with nothing applied")
    assert not re.search(r"\breturn\b", _block(tail, wait)), (
        "handleIdentityChange stops after waiting out the old load instead of reloading")
    # The index check above passes on a reload moved INSIDE the wait: then an identity change with
    # no load in flight clears the list and never reloads it (`.task(id: isActiveTab)` does not
    # re-fire), so the new account sees an empty list until pull-to-refresh.
    assert "loadBackendData" not in _block(tail, wait) and re.match(
            r"\s*await loadBackendData\(\)", _after_block(tail, wait)), (
        "handleIdentityChange reloads only when a load was in flight: `await loadBackendData()` "
        "must run unconditionally, straight after the wait block")
    assert "startReportsPolling()" in tail and (
        tail.index("await loadBackendData()") < tail.rindex("startReportsPolling()")), (
        "handleIdentityChange never re-arms the reports poll it stopped, so processing cards "
        "freeze until the tab is re-entered")


# ── 7. The user-TAP gates stay on auth status ────────────────────────────────


def test_the_user_tap_gates_stay_on_auth_status():
    vm = _code(_VM)
    for decl, reason in _TAP_GATES:
        name = decl.split("(")[0].removeprefix("func ")
        body = _block(vm, decl)
        assert re.search(_tap_guard_re(reason), body), (
            f"{name} lost its user-tap sign-in gate — a paid write would fire on an unvalidated "
            "session and surface the generic alert instead of the Reconnecting toast")


# ── 8. A pre-flight refusal never reaches the restore hook ───────────────────


def test_a_pre_flight_refusal_never_reaches_the_restore_hook():
    req = _block(_code(_API), "func request<T: Decodable>(")
    assert "else if error.isSignInRequired, allowAuthRetry" in req and (
        "handleUnrecoverableAuthFailure" in req), (
        "request<T> lost its server-401 sign-in hook — this ordering guard is vacuous")
    build = "try buildRequest(for: endpoint)"
    assert build in req and "do {" in req and req.index(build) < req.index("do {"), (
        "a pre-flight refusal now reaches handleUnrecoverableAuthFailure: buildRequest moved "
        "inside the do, so every refused poll tick would kick a session restore")


# ── 9. No other member gates the loads on a session read ─────────────────────


def test_no_other_member_gates_the_loads_on_a_session_read():
    """Sections 1 and 4 look only inside loadReports / loadCredits. The same bug returns if the
    pre-flight gate moves one level up — into performBackendLoad, loadIfStale, the poll loop, or a
    helper / computed property the fan-out calls. So FILE-wide (a same-file extension included),
    the only session reads left are the two deliberate tap gates and the refusal arm's
    isRestoringSession; each is cut out exactly before scanning the rest."""
    rest = _code(_VM)
    for decl, reason in _TAP_GATES:
        body = _block(rest, decl)
        cut, n = re.subn(_tap_guard_re(reason), "", body)
        assert n == 1, (
            f"`{decl}` no longer holds exactly one tap gate (found {n}) — section 7 names the "
            "regression; re-derive this scan")
        start = rest.index("{", rest.index(decl) + len(decl))
        rest = rest[:start] + cut + rest[start + len(body):]
    assert _block(rest, _LOAD_REPORTS).count(_ARM) == 1, (
        "loadReports has no typed-refusal arm — re-derive this scan")
    rest = _without_block(rest, _ARM)
    m = re.search(_STATUS + r"|\bisRestoringSession\b", rest)
    line = m and rest[rest.rfind("\n", 0, m.start()) + 1: rest.find("\n", m.end())].strip()
    assert not m, (
        f"a pre-flight session read gates the Reports/credits load from another member (`{line}`) "
        "— on a signed-in cold launch the token is armed while the status reads .restoring, so it "
        "refuses a request that would have succeeded and nothing re-runs it")


# ── 10. A previous identity's credits answer never lands ─────────────────────


def test_a_previous_identitys_credits_answer_never_lands():
    credits = _block(_code(_VM), _LOAD_CREDITS)
    assert ".getUserCredits" in credits and "CreditBalance.from(backendCredits)" in credits, (
        "the loadCredits block is not the live credits load — this guard is reading the wrong code")
    assert credits.count("apiClient.request(") == 1, "loadCredits no longer sends exactly one request"
    request_at = credits.index("apiClient.request(")
    assert credits.count(_CREDITS_EPOCH) == 1 and credits.index(_CREDITS_EPOCH) < request_at, (
        "loadCredits captures the identity epoch after its request was sent (or not at all), so "
        "an answer asked for by the previous identity compares equal")

    assert credits.count("do {") == 1 and credits.count("} catch {") == 1, (
        "loadCredits no longer has exactly one do/catch — re-derive this scan")
    do_part = credits[credits.index("do {"): credits.index("} catch {")]
    assert do_part.count(_CREDITS_EPOCH_GUARD) == 1, (
        "loadCredits has no identity-epoch guard on its answer (`guard epoch == identityEpoch "
        "else`): a credits answer asked for by the previous identity lands in the new one and "
        "shows that account's balance")
    guard_at = do_part.index(_CREDITS_EPOCH_GUARD)
    assert do_part.index("apiClient.request(") < guard_at, (
        "the identity-epoch guard runs before the credits answer arrives, so it checks nothing — "
        "it must sit after the request")
    guard_body = _block(do_part, _CREDITS_EPOCH_GUARD)
    guard_end = do_part.index("{", guard_at) + len(guard_body)
    writes = [m.start() for m in re.finditer(r"\bcreditBalance\s*=(?!=)", do_part)]
    assert writes and all(w >= guard_end for w in writes), (
        "the credits answer is applied before its identity-epoch guard — the previous identity's "
        "balance lands before the guard can drop it")
    assert "print(" in guard_body, (
        "a credits answer from a previous identity is dropped silently — the epoch guard must log it")

    # The refusal arm, the same way: a refusal asked for under the previous identity (decided at
    # buildRequest, after the pre-flight token refresh's await) must not hide the new balance.
    assert credits.count(_CREDITS_ARM) == 1, (
        "loadCredits has no typed-refusal arm — re-derive this scan")
    arm = _block(credits, _CREDITS_ARM)
    assert arm.count(_CREDITS_EPOCH_GUARD) == 1, (
        "loadCredits' refusal arm has no identity-epoch guard (`guard epoch == identityEpoch "
        "else`): a refusal asked for by the previous identity hides the new account's balance")
    arm_guard_at = arm.index(_CREDITS_EPOCH_GUARD)
    arm_guard_end = arm.index("{", arm_guard_at) + len(_block(arm, _CREDITS_EPOCH_GUARD))
    arm_writes = [m.start() for m in re.finditer(r"\bcreditBalance\s*=(?!=)", arm)]
    assert all(w >= arm_guard_end for w in arm_writes), (
        "loadCredits' refusal arm clears the balance before its identity-epoch guard — a refusal "
        "from the previous identity still hides the new account's balance")


# ── 11. The gate flags are read only by the freshness stamp ──────────────────


def _flag_decl(flag: str, *, private_set: bool) -> str:
    setter = r"private\(set\)\s+" if private_set else r"(?:private\(set\)\s+)?"
    return rf"(?m)^[ \t]*@Published\s+{setter}var\s+{flag}\s*:\s*Bool\s*=\s*false[ \t]*$"


def test_the_gate_flags_are_read_only_by_the_freshness_stamp():
    """Section 2 pins every WRITE of the flags; this pins every READ. A pre-flight read of the
    gate itself (`guard !requiresSignInForReports … else { return }`) needs no status read at
    all: once a refusal draws the gate, every heal (handleIdentityChange, pull-to-refresh,
    loadIfStale) reaches loadReports — the only writer — through performBackendLoad, so a guard
    ahead of it returns before the gate can clear, and the gate latches through sign-in."""
    load = _load()
    for flag in _FLAGS:
        refs = len(re.findall(rf"\b{flag}\b", load))
        writes = len(re.findall(rf"\b{flag}\s*=(?!=)", load))
        assert refs == writes, (
            f"loadReports reads `{flag}` pre-flight ({refs - writes} read(s) beside its {writes} "
            "write(s)) — it is the gate's only writer, so a read that skips the request latches "
            "the gate for the rest of the app run")

    rest = _without_block(_code(_VM), _LOAD_REPORTS)
    perf = _block(rest, _PERFORM)
    assert perf.count(_GATE_IF) == 1, (
        "performBackendLoad lost its freshness stamp `if !requiresSignInForReports && "
        "!isReconnectingReports` — re-derive this scan")
    start = rest.index("{", rest.index(_PERFORM) + len(_PERFORM))
    rest = rest[:start] + perf.replace(_GATE_IF, "", 1) + rest[start + len(perf):]
    for flag in _FLAGS:
        rest, n = re.subn(_flag_decl(flag, private_set=False), "", rest)
        assert n == 1, (
            f"expected exactly one `@Published … var {flag}: Bool = false` declaration, found {n} "
            "— re-derive this scan")
    m = re.search(r"\b(?:requiresSignInForReports|isReconnectingReports)\b", rest)
    assert not m, (
        f"a gate flag is read outside performBackendLoad's freshness stamp (`{_line_of(rest, m)}`) "
        "— a pre-flight read of the gate returns before loadReports, its only writer, so a "
        "refusal's gate is never lifted again")


# ── 12. No view can write the gate ───────────────────────────────────────────


def test_no_view_can_write_the_gate_flags():
    vm = _code(_VM)
    for flag in _FLAGS:
        n = len(re.findall(_flag_decl(flag, private_set=True), vm))
        assert n == 1, (
            f"`{flag}` is no longer `@Published private(set) var` — with a public setter a view "
            "(an onChange of auth.status in the tab container) can write the account gate from a "
            "pre-flight auth read, where no scan here looks; private(set) makes that a compile error")


# ── 13. The Research tab's activation tasks never read the session ───────────


def test_the_research_tab_activation_never_reads_the_session():
    """Sections 1, 4 and 9 read only ResearchViewModel.swift, so the launch race can move into the
    view that owns the load: `guard isActiveTab, appState.auth.isAuthenticated` skips the load
    while the session is .restoring, `.task(id:)` does not re-fire, and the first resolution
    bumps no identityGeneration — the list stays empty until the tab is re-entered."""
    view = _block(_code(_CONTENT), _RESEARCH_VIEW)
    assert "@StateObject private var viewModel: ResearchViewModel" in view, (
        "the ResearchViewWithBinding block is not the live Research tab — this guard is reading "
        "the wrong code")
    tasks = _blocks(view, _ACTIVATION_TASK)
    loads = [t for t in tasks if "viewModel.loadIfStale()" in t]
    assert len(loads) == 1, (
        f"the Research tab's activation load is gone or doubled ({len(loads)} `.task(id: "
        "isActiveTab)` blocks call viewModel.loadIfStale()) — re-derive; with none, nothing "
        "loads the list when the tab is entered")
    for task in tasks:
        m = re.search(_SESSION_READ, task)
        assert not m, (
            f"a `.task(id: isActiveTab)` block in ResearchViewWithBinding reads the session "
            f"(`{_line_of(task, m)}`) — while the session is .restoring the activation load is "
            "skipped and nothing re-runs it")


# ── 14. Anti-vacuity ─────────────────────────────────────────────────────────


def test_comment_stripping_is_real_and_load_bearing():
    stripped = _strip_swift_comments(
        "// AppActions.shared.isSignedIn\n"
        "/* auth.status\n   isAuthenticated */\n"
        "    let x = 1  // requiresSignInForReports = true\n")
    assert not re.search(_STATUS, stripped) and "requiresSignInForReports" not in stripped
    assert "let x = 1" in stripped
    # The fix's own comments in loadReports / loadCredits name the status reads they removed.
    # If they ever stop doing so this stays harmless; while they do, it proves the stripping
    # above is what keeps the absence checks honest.
    raw = _VM.read_text(encoding="utf-8")
    head = raw[raw.index(_LOAD_REPORTS): raw.index('print("📋 ResearchVM: Loading reports')]
    assert re.search(_STATUS, head), (
        "loadReports' comment no longer names the removed status read — fine, but then this "
        "anti-vacuity probe needs a new raw sample")
    assert not re.search(_STATUS, _strip_swift_comments(head))


@pytest.mark.parametrize("path,header", [
    (_VM, _LOAD_REPORTS),
    (_VM, _LOAD_CREDITS),
    (_VM, _PERFORM),
    (_VM, _IDENTITY),
    (_VM, "func generateAnalysis()"),
    (_VM, "func retryReport(_ report: AnalysisReport)"),
    (_API, "func request<T: Decodable>("),
])
def test_each_scan_is_bounded_to_its_declaration(path, header):
    src = _code(path)
    block = _block(src, header)
    assert len(block) > 200, f"`{header}` block is only {len(block)} chars — the scan has drifted"
    assert len(block) < len(src) // 2, (
        f"`{header}` block is {len(block)} of {len(src)} chars — `_block` stopped bounding")


# ── 15. The mutations above, re-run in memory on every pass ─────────────────

_LOADING = 'print("📋 ResearchVM: Loading reports from backend...")'
_CREDITS_LOADING = 'print("💳 ResearchVM: Loading credits from backend...")'
_ARM_OPEN = "if case .signInRequired = appError {"
_ARM_TAIL = "                requiresSignInForReports = !reconnecting\n                return\n"
_ARM_HEAD = "                reports = []\n                let reconnecting"
_CATCH_STALE_LOG = (
    '                print("ℹ️ ResearchVM: superseded list outcome dropped (seq \\(seq) ≤ '
    '\\(reportsAppliedSeq), \\(appError.analyticsCode))")\n')
_DO_STALE_LOG = (
    '                print("ℹ️ ResearchVM: superseded list answer dropped (seq \\(seq) ≤ '
    '\\(reportsAppliedSeq))")\n')
_DO_GUARD = "            guard seq > reportsAppliedSeq else {\n" + _DO_STALE_LOG + (
    "                return\n            }\n")
_CATCH_GUARD = "            guard seq > reportsAppliedSeq else {\n" + _CATCH_STALE_LOG + (
    "                return\n            }\n")
_DO_CLEARS = "            requiresSignInForReports = false\n            isReconnectingReports = false\n"
_FAIL_CLEARS = _DO_CLEARS + "            Analytics.shared.track("
_FAILURE_ANALYTICS = "            Analytics.shared.track(.backgroundSyncFailed"
_STAMP = (
    "        if !requiresSignInForReports && !isReconnectingReports {\n"
    "            lastLoadedAt = Date()\n        }\n")
_FANOUT = (
    "        async let reportsTask: () = loadReports()\n"
    "        async let creditsTask: () = loadCredits()\n"
    "        async let trendingTask: () = loadTrending()\n"
    "        async let personasTask: () = loadPersonas()\n"
    "        _ = await (reportsTask, creditsTask, trendingTask, personasTask)\n")
_WAIT = (
    "        if let running = loadTask, !running.isCancelled {\n"
    "            await running.value\n        }\n")
_CREDITS_ARM_OPEN = "            if case .signInRequired = AppError.from(error) {"
_CREDITS_FAIL_LOG = (
    '            print("⚠️ ResearchVM: Failed to load credits — \\(error). '
    'Leaving balance unknown.")\n')


def _tap_guard(reason: str) -> str:
    return ("        guard AppActions.shared.isSignedIn else {\n"
            f'            AppActions.shared.requestSignIn(for: "{reason}")\n'
            "            return\n        }\n")


_T1 = test_load_reports_decides_the_gate_from_the_refusal
_T2 = test_the_gate_flags_are_written_only_from_an_outcome
_T3 = test_a_stale_outcome_cannot_move_a_newer_gate
_T4 = test_load_credits_decides_from_the_refusal
_T5 = test_only_an_ungated_pass_is_stamped_fresh
_T6 = test_an_identity_change_reloads_behind_an_in_flight_load
_T7 = test_the_user_tap_gates_stay_on_auth_status
_T8 = test_a_pre_flight_refusal_never_reaches_the_restore_hook
_T9 = test_no_other_member_gates_the_loads_on_a_session_read
_T10 = test_a_previous_identitys_credits_answer_never_lands
_T11 = test_the_gate_flags_are_read_only_by_the_freshness_stamp
_T12 = test_no_view_can_write_the_gate_flags
_T13 = test_the_research_tab_activation_never_reads_the_session

_FLAG_CLEARS_12 = "requiresSignInForReports = false\n            isReconnectingReports = false\n"
_FLAG_CLEARS_16 = (
    "requiresSignInForReports = false\n                isReconnectingReports = false\n")
_CREDITS_GUARD = (
    "            guard epoch == identityEpoch else {\n"
    '                print("ℹ️ ResearchVM: credits answer from a previous identity dropped")\n'
    "                return\n            }\n")
_CREDITS_REQUEST = (
    "            let backendCredits: BackendCreditsResponse = try await apiClient.request(")
_CREDITS_APPLY = "            self.creditBalance = CreditBalance.from(backendCredits)\n"
_CREDITS_REFUSAL_GUARD = (
    "                guard epoch == identityEpoch else {\n"
    '                    print("ℹ️ ResearchVM: credits refusal from a previous identity dropped")\n'
    "                    return\n"
    "                }\n")
_CREDITS_REFUSAL_CLEAR = _CREDITS_REFUSAL_GUARD + "                creditBalance = nil\n"
_SEQ_BUMP = "        reportsRequestSeq &+= 1\n        let seq = reportsRequestSeq\n"
_PERFORM_HEAD = "    private func performBackendLoad() async {\n        let epoch = identityEpoch\n"
_STALE_CHECK = (
    "        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }\n")
_CREDITS_HEAD = '        let epoch = identityEpoch\n        print("💳'
_LOAD_IF_STALE_TASK = (
    "        .task(id: isActiveTab) {\n"
    "            guard isActiveTab else { return }\n"
    "            await viewModel.loadIfStale()\n        }\n")
_POLL_TASK_BODY = (
    "            guard isActiveTab else { return }\n"
    "            viewModel.startReportsPolling()\n")

# (name, file, ((anchor, replacement), ...), guard, the assertion message it must fail WITH).
# Every anchor must occur EXACTLY once at the moment it is applied; edits apply in order. The
# message is matched so a mutation cannot pass by tripping an unrelated, earlier assertion.
_MUTATIONS = [
    # ── loadReports: the refusal arm ──
    ("M1-status-guard-back", _VM,
     ((_LOADING, "guard AppActions.shared.isSignedIn else { reports = []; return }\n        "
       + _LOADING),),
     _T1, "loadReports decides the gate from auth.status again"),
    # A "softened" guard that lets a restoring session through still decides pre-flight.
    ("M1b-softened-status-guard", _VM,
     ((_LOADING, "guard AppActions.shared.isSignedIn || AppActions.shared.isRestoringSession "
                 "else { reports = []; return }\n        " + _LOADING),),
     _T1, "loadReports decides the gate from auth.status again"),
    ("M2-restoring-pre-flight", _VM,
     ((_LOADING, "guard !AppActions.shared.isRestoringSession else { return }\n        "
       + _LOADING),),
     _T1, "isRestoringSession is read outside the typed-refusal arm"),
    ("M3-arm-disabled", _VM,
     ((_ARM_OPEN, "if false, case .signInRequired = appError {"),),
     _T1, "loadReports has no typed-refusal arm"),
    ("M4-arm-falls-through", _VM,
     ((_ARM_TAIL, "                requiresSignInForReports = !reconnecting\n"),),
     _T1, "the refused pass falls through into the sync-failed analytics"),
    ("M5-sign-in-while-reconnecting", _VM,
     (("requiresSignInForReports = !reconnecting", "requiresSignInForReports = true"),),
     _T1, "the refused pass offers Sign In to a reconnecting user"),
    ("M5b-reconnecting-never-set", _VM,
     (("isReconnectingReports = reconnecting", "isReconnectingReports = false"),),
     _T1, "the refused pass does not set isReconnectingReports"),
    ("M6-arm-keeps-rows", _VM,
     ((_ARM_HEAD, "                let reconnecting"),),
     _T1, "a refused pass keeps rows it can no longer refresh"),
    ("M7-arm-stamps-fresh", _VM,
     ((_ARM_TAIL, "                requiresSignInForReports = !reconnecting\n"
                  "                lastLoadedAt = Date()\n                return\n"),),
     _T1, "the refused pass stamps lastLoadedAt"),
    ("M8-analytics-before-arm", _VM,
     ((_ARM_OPEN, "Analytics.shared.track(.backgroundSyncFailed, [:])\n            " + _ARM_OPEN),),
     _T1, "the refusal is counted as a sync failure: the backgroundSyncFailed call runs before"),
    ("M8b-analytics-inside-arm", _VM,
     ((_ARM_HEAD, "                Analytics.shared.track(.backgroundSyncFailed, [:])\n" + _ARM_HEAD),),
     _T1, "the typed-refusal arm itself tracks a sync failure"),
    ("M9-alert-before-arm", _VM,
     ((_ARM_OPEN, "self.error = appError.message\n            " + _ARM_OPEN),),
     _T1, "the refusal raises the error alert: self.error is assigned before"),
    ("M9b-alert-inside-arm", _VM,
     ((_ARM_HEAD, "                self.error = appError.message\n" + _ARM_HEAD),),
     _T1, "the typed-refusal arm itself sets self.error"),
    # Review G4: an inverted read survives a substring test.
    ("G4-restoring-read-inverted", _VM,
     (("let reconnecting = AppActions.shared.isRestoringSession",
       "let reconnecting = !AppActions.shared.isRestoringSession"),),
     _T1, "the refused pass inverts or replaces the restoring read"),
    # Review G8: a later overwrite inside the arm survives the presence checks.
    ("G8-arm-flag-overwritten", _VM,
     ((_ARM_TAIL, "                requiresSignInForReports = !reconnecting\n"
                  "                isReconnectingReports = false\n"
                  "                requiresSignInForReports = true\n                return\n"),),
     _T1, "the refused pass overwrites a gate flag it just set"),
    # ── the flags are written only from an outcome ──
    ("M10-clear-before-request", _VM,
     ((_LOADING, "requiresSignInForReports = false\n        isReconnectingReports = false\n        "
       + _LOADING),),
     _T2, "the gate flags are written before the request"),
    ("M11-success-keeps-gate", _VM,
     ((_DO_CLEARS + '            print("✅', '            print("✅'),),
     _T2, "a successful load does not clear the gate"),
    ("M11b-stale-success-clears", _VM,
     ((_DO_GUARD + "            reportsAppliedSeq = seq\n" + _DO_CLEARS,
       _DO_CLEARS + _DO_GUARD + "            reportsAppliedSeq = seq\n"),),
     _T2, "a dropped (stale) success clears the gate"),
    ("M12-failure-keeps-gate", _VM,
     ((_FAIL_CLEARS, "            Analytics.shared.track("),),
     _T2, "a real failure leaves a latched account gate in place"),
    # Review G2 / G2b: clears DUPLICATED ahead of the success guard, or inside its `else` — the
    # applied clears after the guard stay, so the presence checks pass.
    ("G2-success-clears-before-stale-guard", _VM,
     (("                responseType: [BackendReportListItem].self\n            )\n",
       "                responseType: [BackendReportListItem].self\n            )\n"
       "            " + _FLAG_CLEARS_12),),
     _T2, "a superseded success still writes a gate flag"),
    ("G2b-success-clears-inside-stale-else", _VM,
     ((_DO_STALE_LOG, _DO_STALE_LOG + "                " + _FLAG_CLEARS_16),),
     _T2, "a superseded success still writes a gate flag"),
    ("G9-success-overwrites-clear", _VM,
     (('            print("✅ ResearchVM: Loaded \\(backendReports.count) reports',
       "            isReconnectingReports = backendReports.isEmpty\n"
       '            print("✅ ResearchVM: Loaded \\(backendReports.count) reports'),),
     _T2, "a successful load writes `isReconnectingReports` 2x"),
    # Review G3 (+ G3b): the catch's mirror — ahead of, or inside, its superseded-outcome guard.
    ("G3-catch-clears-before-stale-guard", _VM,
     (("            guard !appError.isCancellation else { return }\n",
       "            guard !appError.isCancellation else { return }\n"
       "            " + _FLAG_CLEARS_12),),
     _T2, "a cancelled or superseded failure still writes a gate flag"),
    ("G3b-catch-clears-inside-stale-else", _VM,
     ((_CATCH_STALE_LOG, _CATCH_STALE_LOG + "                " + _FLAG_CLEARS_16),),
     _T2, "a cancelled or superseded failure still writes a gate flag"),
    ("G9b-failure-overwrites-clear", _VM,
     ((_FAILURE_ANALYTICS,
       "            requiresSignInForReports = reports.isEmpty\n" + _FAILURE_ANALYTICS),),
     _T2, "a real failure writes `requiresSignInForReports` 2x after the typed-refusal arm"),
    ("G10-identity-reset-writes-flags", _VM,
     (("        creditBalance = nil\n        lastLoadedAt = nil\n",
       "        creditBalance = nil\n        lastLoadedAt = nil\n"
       "        " + "requiresSignInForReports = false\n        isReconnectingReports = false\n"),),
     _T2, "a gate flag is written outside loadReports"),
    # ── newest outcome wins (D1 / D4) ──
    ("M13-no-catch-stale-guard", _VM,
     ((_CATCH_GUARD + "            " + _ARM_OPEN, "            " + _ARM_OPEN),),
     _T3, "a refusal older than the last applied success can clear a newer list"),
    ("M13b-silent-catch-drop", _VM,
     ((_CATCH_STALE_LOG, ""),),
     _T3, "a superseded list outcome is dropped silently"),
    ("M13c-silent-success-drop", _VM,
     ((_DO_STALE_LOG, ""),),
     _T3, "a superseded list answer is dropped silently"),
    ("M14-refusal-does-not-advance", _VM,
     (("                reportsAppliedSeq = seq\n", ""),),
     _T3, "a refusal does not advance reportsAppliedSeq"),
    ("M14b-failure-advances", _VM,
     ((_FAILURE_ANALYTICS, "            reportsAppliedSeq = seq\n" + _FAILURE_ANALYTICS),),
     _T3, "a real failure advances reportsAppliedSeq"),
    # ── loadCredits ──
    ("M15-credits-status-guard", _VM,
     ((_CREDITS_LOADING, "guard AppActions.shared.isSignedIn else { creditBalance = nil; return }\n"
       "        " + _CREDITS_LOADING),),
     _T4, "loadCredits decides from auth.status again"),
    ("M15b-credits-arm-gone", _VM,
     ((_CREDITS_ARM_OPEN, "            if false, case .signInRequired = AppError.from(error) {"),),
     _T4, "loadCredits has no typed-refusal arm"),
    ("M16-credits-arm-keeps-balance", _VM,
     (("                creditBalance = nil\n                return\n", "                return\n"),),
     _T4, "a refused credits pass keeps a balance it can no longer refresh"),
    ("M16b-credits-arm-falls-through", _VM,
     (("                creditBalance = nil\n                return\n",
       "                creditBalance = nil\n"),),
     _T4, "a refused credits pass falls through into the failure log"),
    ("M16c-credits-logged-first", _VM,
     ((_CREDITS_ARM_OPEN, _CREDITS_FAIL_LOG + _CREDITS_ARM_OPEN),),
     _T4, "a refused credits pass is logged as a load failure"),
    # ── loadCredits: the identity-epoch drop ──
    ("MC1-credits-epoch-guard-gone", _VM,
     ((_CREDITS_GUARD, ""),),
     _T10, "loadCredits has no identity-epoch guard on its answer"),
    ("MC2-credits-epoch-captured-late", _VM,
     (('        let epoch = identityEpoch\n        print("💳', '        print("💳'),
      (_CREDITS_GUARD, "            let epoch = identityEpoch\n" + _CREDITS_GUARD)),
     _T10, "loadCredits captures the identity epoch after its request was sent"),
    ("MC3-credits-guard-before-request", _VM,
     ((_CREDITS_GUARD, ""),
      (_CREDITS_REQUEST, _CREDITS_GUARD + _CREDITS_REQUEST)),
     _T10, "the identity-epoch guard runs before the credits answer arrives"),
    ("MC4-credits-applied-before-guard", _VM,
     ((_CREDITS_APPLY, ""),
      (_CREDITS_GUARD, _CREDITS_APPLY + _CREDITS_GUARD)),
     _T10, "the credits answer is applied before its identity-epoch guard"),
    ("MC4b-credits-applied-twice", _VM,
     ((_CREDITS_GUARD, _CREDITS_APPLY + _CREDITS_GUARD),),
     _T10, "the credits answer is applied before its identity-epoch guard"),
    ("MC5-credits-silent-drop", _VM,
     (('                print("ℹ️ ResearchVM: credits answer from a previous identity dropped")\n',
       ""),),
     _T10, "a credits answer from a previous identity is dropped silently"),
    # The refusal arm's own epoch guard (review LOW, fixed in source 2026-10-01).
    ("MC6-credits-refusal-epoch-guard-gone", _VM,
     ((_CREDITS_REFUSAL_CLEAR, "                creditBalance = nil\n"),),
     _T10, "loadCredits' refusal arm has no identity-epoch guard"),
    ("MC7-credits-refusal-cleared-before-guard", _VM,
     ((_CREDITS_REFUSAL_CLEAR,
       "                creditBalance = nil\n" + _CREDITS_REFUSAL_GUARD),),
     _T10, "loadCredits' refusal arm clears the balance before its identity-epoch guard"),
    # ── freshness (D2) ──
    ("M17-stamp-hoisted", _VM,
     ((_STAMP, "        lastLoadedAt = Date()\n"
               "        if !requiresSignInForReports && !isReconnectingReports {\n        }\n"),),
     _T5, "lastLoadedAt is stamped outside the gate check"),
    # Re-derived 2026-10-01: loadCredits' refusal arm now carries the same guard (indented
    # deeper, so the bare line occurred twice); the anchor carries the gate check that follows.
    ("M17b-epoch-guard-gone", _VM,
     (("        guard epoch == identityEpoch else { return }\n"
       "        if !requiresSignInForReports && !isReconnectingReports {\n",
       "        if !requiresSignInForReports && !isReconnectingReports {\n"),),
     _T5, "a load that straddled an identity change is stamped fresh"),
    ("M17c-epoch-captured-late", _VM,
     (("        let epoch = identityEpoch\n" + _FANOUT, _FANOUT + "        let epoch = identityEpoch\n"),),
     _T5, "performBackendLoad captures the identity epoch after its loads have started"),
    ("M17d-epoch-bumped-only-when-active", _VM,
     (("        identityEpoch &+= 1\n", ""),
      ("        " + _ACTIVE_GUARD + "\n",
       "        " + _ACTIVE_GUARD + "\n        identityEpoch &+= 1\n")),
     _T5, "the identity epoch is bumped only for the active tab"),
    # Review G5 / G5b: a second fresh stamp in another member, outside performBackendLoad.
    ("G5-stamp-in-refresh", _VM,
     (("guard !isDeletingReports else { return }",
       "guard !isDeletingReports else { return }; lastLoadedAt = Date()"),),
     _T5, "lastLoadedAt is written by another member as well"),
    ("G5b-stamp-in-loadBackendData", _VM,
     (("        loadTask = task\n        await task.value\n",
       "        loadTask = task\n        await task.value\n        lastLoadedAt = Date()\n"),),
     _T5, "lastLoadedAt is written by another member as well"),
    ("G5c-stamp-through-inout", _VM,
     (("guard !isDeletingReports else { return }",
       "guard !isDeletingReports else { return }; Self.stamp(&lastLoadedAt)"),
      ("    func refresh() async {\n",
       "    private static func stamp(_ d: inout Date?) { d = Date() }\n"
       "    func refresh() async {\n")),
     _T5, "lastLoadedAt is written through a detour the assignment scan cannot see"),
    ("G5d-stamp-through-key-path", _VM,
     (("guard !isDeletingReports else { return }",
       "guard !isDeletingReports else { return }; "
       "self[keyPath: \\ResearchViewModel.lastLoadedAt] = Date()"),),
     _T5, "lastLoadedAt is written through a detour the assignment scan cannot see"),
    ("G5e-stamp-made-public", _VM,
     (("    private var lastLoadedAt: Date?\n", "    var lastLoadedAt: Date?\n"),),
     _T5, "lastLoadedAt is no longer private"),
    ("G5f-clear-only-when-active", _VM,
     (("        lastLoadedAt = nil\n", ""),
      ("        " + _ACTIVE_GUARD + "\n",
       "        " + _ACTIVE_GUARD + "\n        lastLoadedAt = nil\n")),
     _T5, "a hidden tab keeps the previous identity's freshness stamp"),
    # ── B4 ──
    ("M18-old-identity-answers-land", _VM,
     (("        reportsAppliedSeq = reportsRequestSeq\n", ""),),
     _T6, "handleIdentityChange lets the previous identity's in-flight list answers land"),
    ("M21-joins-in-flight-load", _VM,
     ((_WAIT, ""),),
     _T6, "handleIdentityChange joins an in-flight load whose reports answer it just marked"),
    ("M21b-wait-then-return", _VM,
     (("            await running.value\n        }\n        await loadBackendData()\n",
       "            await running.value\n            return\n        }\n"
       "        await loadBackendData()\n"),),
     _T6, "handleIdentityChange stops after waiting out the old load instead of reloading"),
    ("M21c-poll-not-rearmed", _VM,
     (("        startReportsPolling()\n    }\n", "    }\n"),),
     _T6, "handleIdentityChange never re-arms the reports poll it stopped"),
    # Review G7: the reload moved INSIDE the wait — no reload unless a load was in flight.
    ("G7-reload-only-if-in-flight", _VM,
     ((_WAIT + "        await loadBackendData()\n",
       "        if let running = loadTask, !running.isCancelled {\n"
       "            await running.value\n            await loadBackendData()\n        }\n"),),
     _T6, "handleIdentityChange reloads only when a load was in flight"),
    # ── tap gates ──
    ("M22-generate-gate-gone", _VM,
     ((_tap_guard("generate AI analysis"), ""),),
     _T7, "generateAnalysis lost its user-tap sign-in gate"),
    ("M22b-retry-gate-gone", _VM,
     ((_tap_guard("retry this analysis"), ""),),
     _T7, "retryReport lost its user-tap sign-in gate"),
    # ── APIClient ordering ──
    # Re-derived 2026-10-01: request<T> now opens with the proactive pre-flight token refresh
    # (which never ends a session, and returns at once with no token armed), so the anchor
    # carries that line; the mutant keeps it and moves only buildRequest into the do.
    ("M23-build-inside-do", _API,
     ((") async throws -> T {\n        await refreshArmedTokenIfExpired(for: endpoint)\n"
       "        let request = try buildRequest(for: endpoint)\n\n"
       "        logRequest(request, endpoint: endpoint)\n\n        do {",
       ") async throws -> T {\n        await refreshArmedTokenIfExpired(for: endpoint)\n"
       "        do {\n"
       "            let request = try buildRequest(for: endpoint)\n"
       "            logRequest(request, endpoint: endpoint)"),),
     _T8, "a pre-flight refusal now reaches handleUnrecoverableAuthFailure"),
    # ── no other member gates the loads (review G1 / G1b / G6, plus the poll and a detour) ──
    ("G1-preflight-in-performBackendLoad", _VM,
     (("    private func performBackendLoad() async {\n        let epoch = identityEpoch\n",
       "    private func performBackendLoad() async {\n"
       "        guard AppActions.shared.isSignedIn else {\n"
       "            reports = []\n"
       "            let reconnecting = AppActions.shared.isRestoringSession\n"
       "            isReconnectingReports = reconnecting\n"
       "            requiresSignInForReports = !reconnecting\n"
       "            creditBalance = nil\n"
       "            return\n        }\n"
       "        let epoch = identityEpoch\n"),),
     _T9, "a pre-flight session read gates the Reports/credits load from another member"),
    ("G1b-preflight-in-loadIfStale", _VM,
     (("        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }\n",
       "        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }\n"
       "        guard AppActions.shared.isSignedIn else { return }\n"),),
     _T9, "a pre-flight session read gates the Reports/credits load from another member"),
    ("G1c-preflight-in-poll-loop", _VM,
     (("                if self.isSelectingReports { continue }\n",
       "                if self.isSelectingReports { continue }\n"
       "                guard AppActions.shared.isSignedIn else { continue }\n"),),
     _T9, "a pre-flight session read gates the Reports/credits load from another member"),
    # Review G6, as a helper the fan-out calls (a form that certainly compiles).
    ("G6-credits-preflight-via-helper", _VM,
     (("        async let creditsTask: () = loadCredits()\n",
       "        async let creditsTask: () = loadCreditsIfSignedIn()\n"),
      ("    private func performBackendLoad() async {\n",
       "    private func loadCreditsIfSignedIn() async {\n"
       "        guard AppActions.shared.isSignedIn else { creditBalance = nil; return }\n"
       "        await loadCredits()\n    }\n\n"
       "    private func performBackendLoad() async {\n")),
     _T9, "a pre-flight session read gates the Reports/credits load from another member"),
    ("G12-preflight-via-computed-property", _VM,
     (("        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }\n",
       "        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }\n"
       "        guard sessionLooksLive else { return }\n"),
      ("    func refresh() async {\n",
       "    private var sessionLooksLive: Bool { AppActions.shared.isRestoringSession == false }\n"
       "    func refresh() async {\n")),
     _T9, "a pre-flight session read gates the Reports/credits load from another member"),
    # ── the gate flags are read only by the freshness stamp (review V1 / V2 / V2b / V7) ──
    ("V1-loadReports-reads-gate-pre-flight", _VM,
     ((_SEQ_BUMP, "        guard !requiresSignInForReports else { return }\n" + _SEQ_BUMP),),
     _T11, "loadReports reads `requiresSignInForReports` pre-flight"),
    ("V2-performBackendLoad-reads-gate-pre-flight", _VM,
     ((_PERFORM_HEAD, _PERFORM_HEAD
       + "        guard !requiresSignInForReports && !isReconnectingReports else { return }\n"),),
     _T11, "a gate flag is read outside performBackendLoad's freshness stamp"),
    ("V2b-loadIfStale-reads-gate-pre-flight", _VM,
     ((_STALE_CHECK, _STALE_CHECK + "        guard !isReconnectingReports else { return }\n"),),
     _T11, "a gate flag is read outside performBackendLoad's freshness stamp"),
    ("V7-loadCredits-reads-gate-pre-flight", _VM,
     ((_CREDITS_HEAD,
       "        guard !requiresSignInForReports && !isReconnectingReports else { return }\n"
       + _CREDITS_HEAD),),
     _T11, "a gate flag is read outside performBackendLoad's freshness stamp"),
    # ── no view can write the gate ──
    ("P1-requiresSignIn-setter-public", _VM,
     (("    @Published private(set) var requiresSignInForReports: Bool = false\n",
       "    @Published var requiresSignInForReports: Bool = false\n"),),
     _T12, "`requiresSignInForReports` is no longer `@Published private(set) var`"),
    ("P2-isReconnecting-setter-public", _VM,
     (("    @Published private(set) var isReconnectingReports: Bool = false\n",
       "    @Published var isReconnectingReports: Bool = false\n"),),
     _T12, "`isReconnectingReports` is no longer `@Published private(set) var`"),
    # ── the Research tab's activation tasks (review V6, ContentView.swift) ──
    ("V6-activation-load-gated-on-auth", _CONTENT,
     ((_LOAD_IF_STALE_TASK, _LOAD_IF_STALE_TASK.replace(
         "guard isActiveTab else", "guard isActiveTab, appState.auth.isAuthenticated else")),),
     _T13, "a `.task(id: isActiveTab)` block in ResearchViewWithBinding reads the session"),
    ("V6c-poll-task-gated-on-stored-credential", _CONTENT,
     ((_POLL_TASK_BODY, _POLL_TASK_BODY.replace(
         "guard isActiveTab else", "guard isActiveTab, !appState.hasUnusedStoredCredential else")),),
     _T13, "a `.task(id: isActiveTab)` block in ResearchViewWithBinding reads the session"),
    ("V6d-activation-load-gone", _CONTENT,
     ((_LOAD_IF_STALE_TASK, ""),),
     _T13, "the Research tab's activation load is gone or doubled"),
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
    for guard in (_T1, _T2, _T3, _T4, _T5, _T6, _T7, _T8, _T9, _T10, _T11, _T12, _T13):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"
    names = [m[0] for m in _MUTATIONS]
    assert len(names) == len(set(names)), "duplicate mutation ids"
