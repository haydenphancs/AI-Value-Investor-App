"""The APP side of the Home Screen widget's lifecycle: who may refresh it, when, and for whom.

WHY THIS FILE EXISTS. Every defect below shipped with the suite green, because the only widget
lifecycle guards asserted three names inside `clearForEndedSession` and two inside
`forceIsMeaningful` (`test_ios_sign_in_wall.py`, `test_ios_launch_cost_guards.py`). There is no
XCTest target, and each failure leaves a widget that renders — just for the wrong person, or
never again:

  1. **Sign-out re-publish.** `credentialReady` opens once per process and never closes, and
     `AuthService.signOut` keeps the bearer armed through two awaited calls. A foreground in
     that window fetched the ex-user's holdings, wrote them onto a signed-out Home Screen and
     minted a fresh 90-day widget token (auth.md §8a). Fixed by `sessionOpen`, which
     `clearForEndedSession` closes and only `AppState` reopens — after the account-switch
     discard, or the new account's forced refresh is dropped.
  2. **Orphaned run.** A run cancelled by sign-out finished after the next session's run had
     started and nilled THAT run's `inFlight` handle, putting it out of reach of the next
     sign-out's `cancel()`. Fixed by `runID` ownership.
  3. **Epoch only on the token.** Snapshot writes were fenced by `Task.isCancelled` alone, which
     cannot un-finish a response that already arrived. Fixed by capturing `sessionEpoch` at the
     run's start and re-checking it before the writes, the stamps and the token publish.
  4. **Partial success suppressing the sign-in force.** A run whose market leg landed and whose
     portfolio leg failed stamped `lastRefreshIdentity`, so the sign-in force was judged
     redundant and Holdings stayed stale.
  5. **No content triggers.** Switching groups, editing holdings and starring a ticker never
     reached the tile; nor did leaving the app.
  6. **Backup restore / account switch from `.restoring`.** Widget state with no session on the
     device was never cleared, and a holdings snapshot owned by another account survived a
     sign-in the in-memory account-switch guard could not see.
  7. **Settle after a sign-out.** A sign-out landing while `onAuthenticated` awaited the StoreKit
     drain / credits closed the gate, and `settleWidgetSession` then REOPENED it and forced a run
     under the bearer `AuthService.signOut` still had armed. Fixed by the identity captured at
     the top of `establishAuthenticatedSession` and fenced in the helper.
  8. **Fences that only MENTIONED the epoch.** The epoch guards looked for the text
     `epoch == sessionEpoch` before the FIRST write: an `await` moved between the two writes, or a
     log-only `if`, stayed green. Every write / stamp / publish site is now checked for a
     returning `guard` after the last await before it.
  9. **A failed mint stamping the throttle** (the tile said "Sign in to Caydex" to a signed-in
     user until a foreground a minute later), and **a failed content-change portfolio leg never
     retried** (the switched group kept the old name). Fixed by the token guard before the stamp
     and by `portfolioPending`.

Per `.claude/rules/testing.md` §3 every scan is comment-stripped (`//`, `///` and `/* */`) and
brace-bounded to the declaration it means. Rule 3 (mutation-test each guard) is not left to a
one-off hand run: `test_every_guard_kills_its_mutation` applies each MUTATION below to an
in-memory copy of the source and asserts the guard FAILS on it, so a guard that goes vacuous
later fails the build too.

MUTATION_LOG (in-suite, run on every pass; first run 2026-09-30 — all killed; the R-rows were
added by the second review round, same day — all killed):
  refresh-gate        `sessionOpen` dropped from refresh()'s guard           -> killed
  content-gate        `sessionOpen` dropped from contentChanged()'s guard    -> killed
  run-content-gate    `sessionOpen` dropped from runContentRefresh()'s guard -> killed
  background-gate     `sessionOpen` dropped from refreshOnBackground()       -> killed
  clear-keeps-open    `sessionOpen = false` deleted from clearForEndedSession -> killed
  second-opener       `sessionOpen = true` added to markCredentialReady()    -> killed
  orphan-run          the `runID == id` guard deleted from startRefresh      -> killed
  write-unfenced      the epoch check before the snapshot writes -> `true`  -> killed
  recheck-between-writes (R8) the owner re-check's await moved between the writes -> killed
  write-fence-log-only   (R8) the write fence made `if !(…) { log }`, no return   -> killed
  write-cancel-log-only  (R8) the cancellation check before the writes no return  -> killed
  stamp-unfenced      the epoch check before the throttle stamps -> `true`  -> killed
  stamp-fence-log-only   (R8) the stamp fence made log-only                       -> killed
  token-unfenced      the epoch check before publishWidgetToken -> `true`   -> killed
  token-fence-log-only   (R8) the token-publish fence made log-only               -> killed
  token-own-epoch     renew re-captures `let epoch = sessionEpoch` itself    -> killed
  expiry-per-launch   the stored-token expiry backfill removed              -> killed
  identity-on-partial `lastRefreshIdentity` stamped outside `if p != nil`   -> killed
  identity-at-completion  the stamp reads `inFlightIdentity` at completion  -> killed
  unowned-write       `owner: owner` dropped from the portfolio write        -> killed
  owner-recheck          (R9) the mid-run bearer re-check block deleted          -> killed
  owner-recheck-ignored  (R9) `p` no longer nil when the account changed        -> killed
  stamp-without-token    (R14) the widget-token guard before the stamp deleted  -> killed
  token-check-log-only   (R14) that guard made log-only                         -> killed
  pending-on-any-answer  (R11) the pending flag cleared on `p != nil`, not stored -> killed
  pending-throttled      (R11) the throttle no longer consults the pending flag   -> killed
  pending-outlives-session (R11) clearForEndedSession keeps the pending flag      -> killed
  content-not-pending    (R11) runContentRefresh no longer marks it pending       -> killed
  no-watchlist-obs    the watchlist notification dropped from the observers -> killed
  no-observers        `observeContentChanges()` dropped from init           -> killed
  undebounced         the debounce sleep deleted from contentChanged()      -> killed
  identity-clobber    the content refresh routed through refresh(force:...) -> killed
  leaked-assertion    endBackgroundTask removed from the expiration handler -> killed
  held-assertion      the completion endBackgroundTask removed              -> killed
  no-migrate          migrateLegacyIfNeeded() deleted from configure        -> killed
  ungated-open        configure opens the session without a stored token    -> killed
  late-open           configure opens the session AFTER the seed refresh    -> killed
  heal-gated          settleWidgetSession dropped from the same-user branch -> killed
  open-before-discard the normal-path settle moved above the discard        -> killed
  helper-no-open      openSession() deleted from settleWidgetSession        -> killed
  helper-no-owner     the owner-mismatch clearPortfolio() deleted           -> killed
  settle-unfenced     (R3) the settle identity fence made log-only           -> killed
  settle-late-capture (R3) the identity captured AFTER the guest-claim await -> killed
  settle-relaundered  (R3) the tail settle re-reads `identityGeneration`    -> killed
  restore-churn       the no-token clear made unconditional (`if true`)     -> killed
  restore-no-clear    clearOrphanedState() deleted from the no-token branch -> killed
  dead-refresh-id     `lastAuthenticatedUserId = nil` deleted (dead refresh)-> killed
  sync-any-group      the syncTickers hook made unconditional (`if true`)   -> killed
  sync-no-hook        the syncTickers contentChanged() call deleted         -> killed
  background-no-hook  refreshOnBackground() deleted from didEnterBackground -> killed
CONTROL: every guarded token written into a `//` and a `/* */` comment is stripped
(`test_the_comment_stripper_actually_strips`).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_SERVICE = _IOS / "Core" / "Services" / "WidgetRefreshService.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_PORTFOLIO = _IOS / "Core" / "Services" / "PortfolioStore.swift"
_APP = _IOS / "iosApp.swift"

_GATE = re.compile(r"guard\s+credentialReady\s*,\s*sessionOpen\s+else\s*\{")
_EPOCH_CHECK = "epoch == sessionEpoch"
# A FENCE, not a mention: the epoch compared in a `guard` whose else-body returns. A log-only
# `if !(epoch == sessionEpoch) { log }` contains the comparison and fences nothing.
# (`[^}]*` gives a false FAILURE if a log string inside the else-body ever holds a literal `}`.)
_EPOCH_FENCE = re.compile(r"guard\s+epoch\s*==\s*sessionEpoch[^{]*else\s*\{[^}]*\breturn\b")
_CANCEL_FENCE = re.compile(
    r"if\s+Task\.isCancelled\s*\{[^}]*\breturn\b"
    r"|guard\b[^{]*!Task\.isCancelled[^{]*else\s*\{[^}]*\breturn\b"
)
_IDENTITY_FENCE = re.compile(
    r"guard\s+(?:identity\s*==\s*identityGeneration|identityGeneration\s*==\s*identity)"
    r"\s+else\s*\{[^}]*\breturn\b"
)
_TOKEN_FENCE = re.compile(r"guard\s+WidgetAPIConfig\.widgetToken\s*!=\s*nil\s+else\s*\{[^}]*\breturn\b")

_REFRESH = "func refresh(force: Bool = false, identity: Int? = nil)"
_PERFORM = "private func performRefresh(identity: Int?) async"
_RENEW = "private func renewWidgetTokenIfNeeded(client: APIClient, epoch: Int) async"
_CONFIGURE = "func configure(apiClient: APIClient, authService: AuthService)"
_ESTABLISH = "private func establishAuthenticatedSession(userId: String) async"
_ON_AUTH = "private func onAuthenticated(userId: String? = nil, identity: Int) async"
_SETTLE = "private func settleWidgetSession(userId: String?, identity: Int)"
_SETTLE_CALL = "settleWidgetSession(userId: userId, identity: identity)"
_RESTORE = "private func performRestore(trigger: String) async"
_SYNC = "private func syncTickers(for portfolioId: String) async throws"


# ── Scanning helpers ─────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure.

    `(?<![:/])` keeps the `//` of a `https://` literal. Every fix in these files EXPLAINS the bug
    it replaced, naming the very tokens asserted below, so an un-stripped scan passes on prose.
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


def _block_at(src: str, idx: int) -> str:
    """The brace-balanced block opened by the first `{` at or after `idx`."""
    open_brace = src.find("{", idx)
    assert open_brace != -1, f"no block opens after offset {idx}"
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    raise AssertionError(f"unbalanced braces after offset {idx}")


def _block(src: str, header: str) -> str:
    """The brace-balanced body of the declaration whose header is the literal `header`."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    return _block_at(src, start + len(header))


def _idx(src: str, token: str, start: int = 0) -> int:
    """`src.index`, but a missing token is a guard FAILURE (AssertionError), not a crash."""
    i = src.find(token, start)
    assert i != -1, f"{token!r} not found"
    return i


def _sources() -> dict[str, str]:
    return {
        "svc": _strip(_read(_SERVICE)),
        "app": _strip(_read(_APP_STATE)),
        "ps": _strip(_read(_PORTFOLIO)),
        "ios": _strip(_read(_APP)),
    }


def _edit_block(src: str, header: str, edit: Callable[[str], str]) -> str:
    """Apply `edit` to one declaration's body only, so a mutation cannot leak elsewhere."""
    body = _block(src, header)
    start = src.find(body, src.find(header))
    return src[:start] + edit(body) + src[start + len(body):]


def _replace_once(old: str, new: str) -> Callable[[str], str]:
    def edit(body: str) -> str:
        assert old in body, f"mutation target {old!r} not found"
        return body.replace(old, new, 1)
    return edit


def _sub_once(pattern: str, new: str) -> Callable[[str], str]:
    def edit(body: str) -> str:
        out, n = re.subn(pattern, new, body, count=1, flags=re.S)
        assert n == 1, f"mutation pattern {pattern!r} not found"
        return out
    return edit


# ── The guards ───────────────────────────────────────────────────────────────────────
#
# Each takes the stripped sources and raises AssertionError when the guarded property is gone.

def _gate_on(header: str) -> Callable[[dict[str, str]], None]:
    def check(s: dict[str, str]) -> None:
        body = _block(s["svc"], header)
        m = _GATE.search(body)
        assert m, (
            f"`{header}` no longer requires `credentialReady, sessionOpen`. `credentialReady` "
            "never closes, so a trigger between sign-out and the bearer being disarmed fetched "
            "the ex-user's holdings onto a signed-out Home Screen"
        )
        # The gate must come first: before any join, start or queue.
        for later in ("startRefresh()", "forcedRefreshPending", "refresh()", "contentDebounce"):
            idx = body.find(later)
            if idx != -1:
                assert m.start() < idx, f"`{later}` in `{header}` runs before the session gate"
    return check


def _check_clear_closes_the_session(s: dict[str, str]) -> None:
    svc = s["svc"]
    clear = _block(svc, "func clearForEndedSession()")
    assert re.search(r"\bsessionOpen\s*=\s*false\b", clear), (
        "clearForEndedSession leaves the session gate open — every trigger can still fetch "
        "while AuthService.signOut has the ex-user's bearer armed"
    )
    for token in ("sessionEpoch &+= 1", "contentDebounce?.cancel()", "forcedRefreshPending = false"):
        assert token in clear, f"clearForEndedSession no longer does `{token}`"
    # One opener, and it is the one AppState calls — a second would reopen the gate implicitly.
    opens = re.findall(r"\bsessionOpen\s*=\s*true\b", svc)
    assert len(opens) == 1, f"`sessionOpen = true` appears {len(opens)} times; only openSession() may open it"
    assert re.search(r"\bsessionOpen\s*=\s*true\b", _block(svc, "func openSession()"))


def _check_run_ownership(s: dict[str, str]) -> None:
    start = _block(s["svc"], "private func startRefresh()")
    assert "runID &+= 1" in start and "let id = runID" in start, "startRefresh no longer tags its run"
    task = _block(start, "inFlight = Task")
    owner = re.search(r"guard\s+self\.runID\s*==\s*id\s+else\s*\{\s*return\s*\}", task)
    assert owner, (
        "the finishing run clears `inFlight` without checking it still owns it — a run "
        "cancelled by sign-out orphans the next session's run, which the next sign-out's "
        "`inFlight?.cancel()` then cannot reach"
    )
    assert task.count("inFlight = nil") == 1
    assert owner.start() < _idx(task, "self.inFlight = nil")
    assert owner.start() < _idx(task, "forcedRefreshPending = false"), (
        "a stale run can drain a request queued behind the live one"
    )


def _sites(body: str, token: str) -> list[int]:
    out, i = [], body.find(token)
    while i != -1:
        out.append(i)
        i = body.find(token, i + 1)
    return out


def _assert_every_site_fenced(body: str, token: str, why: str, *, cancel: bool = False) -> None:
    """EVERY `token` must sit behind an epoch FENCE placed after the last `await` before it.

    Per site, not the first one only: a second write, or an `await` moved between two writes,
    reopens the window for whatever follows it. `main actor + no await between fence and site`
    is what makes the check-then-write atomic.
    """
    sites = _sites(body, token)
    assert sites, f"{token!r} not found — this scan has drifted"
    for i in sites:
        last_await = body.rfind("await", 0, i)
        assert last_await != -1, f"no await precedes {token!r} — this scan has drifted"
        window = body[last_await:i]
        assert _EPOCH_FENCE.search(window), (
            f"{token!r} at offset {i} is not behind `guard epoch == sessionEpoch else {{ … return }}` "
            f"placed after the last await before it — {why} (a log string holding a literal "
            "`}` inside that else-body would also trip this)"
        )
        if cancel:
            assert _CANCEL_FENCE.search(window), (
                f"{token!r} at offset {i} has no cancellation check that RETURNS after the last await"
            )


def _check_writes_are_epoch_fenced(s: dict[str, str]) -> None:
    body = _block(s["svc"], _PERFORM)
    cap = body.find("let epoch = sessionEpoch")
    assert cap != -1, "performRefresh no longer captures the session epoch"
    assert cap < _idx(body, "await "), "the epoch must be captured BEFORE the run's first await"
    _assert_every_site_fenced(
        body, "WidgetSnapshotStore.write(",
        "a response that arrived before sign-out re-publishes the ended session's holdings "
        "after the wipe",
        cancel=True,
    )


def _check_stamps_are_epoch_fenced(s: dict[str, str]) -> None:
    body = _block(s["svc"], _PERFORM)
    _assert_every_site_fenced(
        body, "lastRefresh = Date()",
        "an ended session's run throttles the next session's first refresh",
        cancel=True,
    )


def _check_token_publish_is_fenced_on_the_runs_epoch(s: dict[str, str]) -> None:
    svc = s["svc"]
    renew = _block(svc, _RENEW)
    _idx(renew, "client.request(")
    _assert_every_site_fenced(
        renew, "WidgetAPIConfig.publishWidgetToken(",
        "a signed-out device gets a fresh 90-day widget token (auth.md §8a)",
    )
    assert "let epoch = sessionEpoch" not in renew, (
        "renewWidgetTokenIfNeeded captures its OWN epoch, after the run's fetches — a run that "
        "started after the sign-out bump passes it. The epoch must be the RUN's"
    )
    perform = _block(svc, _PERFORM)
    assert re.search(r"renewWidgetTokenIfNeeded\(client:\s*client,\s*epoch:\s*epoch\)", perform)


def _check_token_expiry_is_backfilled(s: dict[str, str]) -> None:
    renew = _block(s["svc"], _RENEW)
    req = _idx(renew, "client.request(")
    assert "WidgetAPIConfig.widgetTokenExpiry" in renew[:req], (
        "a nil in-memory expiry is no longer backfilled from the stored token before deciding "
        "to mint — every cold launch mints a new, un-revocable 90-day widget token"
    )


def _check_identity_stamp_needs_the_portfolio_leg(s: dict[str, str]) -> None:
    body = _block(s["svc"], _PERFORM)
    assert body.count("lastRefreshIdentity =") == 1
    m = re.search(r"if\s+p\s*!=\s*nil\s*\{", body)
    assert m, "the identity stamp is no longer conditional on the portfolio leg"
    guarded = _block_at(body, m.start())
    assert "lastRefreshIdentity = identity" in guarded, (
        "`lastRefreshIdentity` is stamped when only the MARKET leg succeeded — the sign-in "
        "force is then judged redundant and the Holdings tile stays stale"
    )
    assert "lastRefresh = Date()" not in guarded, "any success must still stamp the throttle"
    # …and with the identity the run STARTED under. `inFlightIdentity` is overwritten by a force
    # that queues behind the run, so reading it at completion credits this run with the NEXT one's
    # identity — and a later force for that identity is suppressed against a fetch never made.
    assert "inFlightIdentity" not in body, "performRefresh reads inFlightIdentity at completion"
    start = _block(s["svc"], "private func startRefresh()")
    capture = start.find("let identity = inFlightIdentity")
    assert capture != -1 and capture < _idx(start, "inFlight = Task"), (
        "startRefresh no longer captures the run's identity before the run starts"
    )
    assert "performRefresh(identity: identity)" in start


def _check_portfolio_write_carries_the_owner(s: dict[str, str]) -> None:
    body = _block(s["svc"], _PERFORM)
    assert _idx(body, "client.currentAuthToken()") < _idx(body, "fetch(.getWidgetPortfolioMover"), (
        "the owner must be read from the bearer BEFORE the fetch it describes"
    )
    assert re.search(
        r"WidgetSnapshotStore\.write\(mode:\s*\.portfolio,\s*snapshot:\s*p,\s*owner:\s*owner\)", body
    ), "the portfolio snapshot is written without its owner — AppState cannot detect a foreign one"


def _check_owner_is_rechecked_after_the_fetch(s: dict[str, str]) -> None:
    """The bearer can be REPLACED under a running fetch (B signing in from a `.restoring` window
    bumps no epoch), and then the answer cannot be attributed. The run re-reads the bearer after
    the fetch and drops the portfolio answer when its subject is no longer `owner` — the only
    defence on that path, or A's holdings land in the slot B's settle just cleared."""
    body = _block(s["svc"], _PERFORM)
    fetched = _idx(body, "await (market, portfolio)")
    write = re.compile(r"WidgetSnapshotStore\.write\(mode:\s*\.portfolio").search(body, fetched)
    assert write, "the portfolio write is gone — this scan has drifted"
    between = body[fetched:write.start()]
    recheck = re.search(r"let\s+(\w+)\s*=\s*await\s+client\.currentAuthToken\(\)", between)
    assert recheck, (
        "the bearer is not re-read between the fetch and the portfolio write — a sign-in landing "
        "mid-run writes the PREVIOUS account's holdings, stamped with its owner, after the new "
        "account's settle cleared the slot"
    )
    changed = re.search(
        rf"(\w+)\s*=\s*(?:Self\.)?subject\(of:\s*{recheck.group(1)}\)\s*!=\s*owner\b", between
    )
    assert changed, "the re-read bearer's subject is not compared with the run's `owner`"
    assert re.search(
        rf"let\s+p\b[^=\n]*=\s*{changed.group(1)}\s*\?\s*nil\s*:\s*fetchedPortfolio\b", between
    ), "the portfolio answer is not dropped (`p` nil) when the account changed mid-run"


def _check_stamp_needs_a_widget_token(s: dict[str, str]) -> None:
    """No widget token → the extension renders "Sign in to Caydex" in both modes. A failed mint
    followed by the throttle stamp left a signed-in user on that until a foreground a minute
    later; unstamped, the next trigger retries the mint."""
    body = _block(s["svc"], _PERFORM)
    stamps = _sites(body, "lastRefresh = Date()")
    assert len(stamps) == 1, f"`lastRefresh = Date()` appears {len(stamps)} times in performRefresh"
    stamp = stamps[0]
    start = body.rfind("await", 0, stamp)
    window = body[start:stamp]
    assert window.startswith("await renewWidgetTokenIfNeeded("), (
        "the token renewal is no longer the last await before the throttle stamp — this scan "
        "has drifted, or the stamp no longer waits for the mint"
    )
    guarded = bool(_TOKEN_FENCE.search(window))
    if not guarded:
        m = re.search(r"if\s+WidgetAPIConfig\.widgetToken\s*!=\s*nil\s*\{", window)
        guarded = bool(m) and "lastRefresh = Date()" in _block_at(body, start + m.start())
    assert guarded, (
        "the throttle is stamped whether or not a widget token exists — a failed mint leaves a "
        "signed-in user's tile on 'Sign in to Caydex' and the 60s throttle blocks the retry"
    )


def _check_pending_holdings_bypass_the_throttle(s: dict[str, str]) -> None:
    """A content change exists only for its portfolio leg. When that leg fails and the market
    leg lands, the throttle is stamped anyway — so the swipe home was throttled and the tile kept
    the OLD group. `portfolioPending` survives until a run STORES, and lets the next trigger
    through the throttle."""
    svc = s["svc"]
    assert re.search(r"private\s+var\s+portfolioPending\s*=\s*false\b", svc), "the flag is gone"

    rc = _block(svc, "private func runContentRefresh()")
    mark = re.search(r"markPortfolioPending\(\)|\bportfolioPending\s*=\s*true\b", rc)
    assert mark, "a content change no longer marks its Holdings answer as pending"
    assert mark.start() < _idx(rc, "forcedRefreshPending = true") and mark.start() < _idx(rc, "startRefresh()"), (
        "the content change marks the Holdings answer pending in only one of its branches"
    )
    if mark.group(0).startswith("markPortfolioPending"):
        assert re.search(r"\bportfolioPending\s*=\s*true\b", _block(svc, "private func markPortfolioPending()"))

    perf = _block(svc, _PERFORM)
    w = re.search(r"(\w+)\s*=\s*WidgetSnapshotStore\.write\(mode:\s*\.portfolio", perf)
    assert w, (
        "the portfolio write's stored/refused result is discarded — a degraded 200 refused over "
        "the old group's snapshot would settle the pending request"
    )
    clears = _sites(perf, "portfolioPending = false")
    assert len(clears) == 1, f"performRefresh clears the pending flag {len(clears)} times"
    cond = re.search(rf"if\s+{w.group(1)}\b[^{{]*\{{", perf)
    assert cond and "portfolioPending = false" in _block_at(perf, cond.start()), (
        "the pending Holdings request is settled by something other than a STORED portfolio write"
    )

    ref = _block(svc, _REFRESH)
    thr = _idx(ref, "Self.minimumInterval")
    ifs = list(re.finditer(r"\bif\b", ref[:thr]))
    assert ifs, "the throttle condition moved — this scan has drifted"
    condition = ref[ifs[-1].start():thr]
    block = _block_at(ref, thr)
    in_condition = re.search(r"!\s*portfolioPending\b", condition)
    in_block = "portfolioPending" in block and block.index("portfolioPending") < _idx(block, "return")
    assert in_condition or in_block, (
        "refresh() throttles a trigger while a Holdings answer is still pending — the switched "
        "group stays on the tile until a foreground a minute later"
    )

    assert re.search(r"\bportfolioPending\s*=\s*false\b", _block(svc, "func clearForEndedSession()")), (
        "a pending Holdings retry outlives the session that asked for it"
    )


def _check_content_observers(s: dict[str, str]) -> None:
    svc = s["svc"]
    assert "observeContentChanges()" in _block(svc, "private init()"), "the observers are never registered"
    obs = _block(svc, "private func observeContentChanges()")
    for name in ("PortfolioStore.activeGroupDidChangeNotification",
                 "PortfolioStore.watchlistDidChangeNotification"):
        assert name in obs, f"the widget no longer observes `{name}`"
    assert "addObserver(" in obs and "contentChanged()" in obs


def _check_content_change_is_debounced_and_identity_neutral(s: dict[str, str]) -> None:
    svc = s["svc"]
    cc = _block(svc, "func contentChanged()")
    assert "refresh(force" not in cc, (
        "a content change routed through refresh(force:identity:) overwrites inFlightIdentity "
        "when it joins — the drained run stamps nil and every launch's force is honoured again"
    )
    assert "contentDebounce?.cancel()" in cc and "Task.sleep(" in cc, "contentChanged is not debounced"
    assert _idx(cc, "Task.sleep(") < _idx(cc, "runContentRefresh()")
    rc = _block(svc, "private func runContentRefresh()")
    assert "forcedRefreshPending = true" in rc and "startRefresh()" in rc
    assert "inFlightIdentity" not in rc
    m = re.search(r"contentDebounceNanoseconds:\s*UInt64\s*=\s*([\d_]+)", svc)
    assert m, "the debounce interval is gone"
    assert 500_000_000 <= int(m.group(1).replace("_", "")) <= 5_000_000_000


def _check_background_refresh_holds_an_assertion(s: dict[str, str]) -> None:
    svc = s["svc"]
    assert "holdBackgroundAssertionUntilSettled()" in _block(svc, "func refreshOnBackground()")
    hold = _block(svc, "private func holdBackgroundAssertionUntilSettled()")
    begin = hold.find("beginBackgroundTask(")
    assert begin != -1, "the background refresh is no longer held open by an assertion"
    expiration = _block_at(hold, begin)
    assert "endBackgroundTask(" in expiration, (
        "the expiration handler does not end the assertion — iOS terminates an app that holds "
        "one past its allowance"
    )
    after = hold[hold.index(expiration, begin) + len(expiration):]
    settle = after.find("waitForRunsToSettle()")
    assert settle != -1 and "endBackgroundTask(" in after[settle:], (
        "the assertion is not ended when the run settles"
    )


def _check_configure_opens_the_session_before_the_seed(s: dict[str, str]) -> None:
    cfg = _block(s["app"], _CONFIGURE)
    seed = _idx(cfg, "WidgetRefreshService.shared.refresh(identity:")
    assert "WidgetSnapshotStore.migrateLegacyIfNeeded()" in cfg, "v1 snapshots are never migrated"
    assert cfg.index("WidgetSnapshotStore.migrateLegacyIfNeeded()") < seed
    opener = re.search(r"if\s+authService\.hasStoredToken\s*\{", cfg)
    assert opener, "configure opens the widget session without a stored credential (or not at all)"
    assert "WidgetRefreshService.shared.openSession()" in _block_at(cfg, opener.start())
    assert cfg.count("WidgetRefreshService.shared.openSession()") == 1
    ready = _idx(cfg, "WidgetRefreshService.shared.markCredentialReady()")
    assert cfg.index("WidgetRefreshService.shared.openSession()") < ready < seed, (
        "the session must open BEFORE the credential gate and the seed, or the seed is dropped"
    )


def _check_on_authenticated_opens_the_session(s: dict[str, str]) -> None:
    app = s["app"]
    oa = _block(app, _ON_AUTH)
    m = re.search(r"if\s+let\s+userId\s*,\s*userId\s*==\s*lastAuthenticatedUserId", oa)
    assert m, "the same-user early return moved — this scan has drifted"
    same = _block_at(oa, m.start())
    assert "settleWidgetSession(" in same and same.index("settleWidgetSession(") < _idx(same, "return"), (
        "the same-user branch (a heal from .restoring) never opens the widget session"
    )
    rest = oa[oa.index(same) + len(same):]
    discard = _idx(rest, "discardDataForEndedSession()")
    settle = rest.find("settleWidgetSession(")
    assert settle != -1 and discard < settle, (
        "the normal path opens the widget session BEFORE the account-switch discard, which "
        "closes it again — the new account's forced refresh is dropped"
    )
    helper = _block(app, _SETTLE)
    opened = helper.find("WidgetRefreshService.shared.openSession()")
    forced = helper.find("WidgetRefreshService.shared.refresh(")
    assert opened != -1 and forced != -1 and opened < forced, "settleWidgetSession must open, then force"
    assert "force: true" in helper[forced:]
    for token in ("WidgetSnapshotStore.portfolioOwner()", "WidgetSnapshotStore.clearPortfolio()"):
        idx = helper.find(token)
        assert idx != -1 and idx < opened, (
            f"`{token}` is gone from settleWidgetSession — a holdings snapshot owned by another "
            "account survives a sign-in the in-memory account-switch guard cannot see"
        )


def _check_settle_refuses_a_moved_identity(s: dict[str, str]) -> None:
    """A sign-out tapped while `onAuthenticated` awaits the StoreKit drain / credits (or the
    guest claim before it) closes the widget gate synchronously — and settle used to REOPEN it
    unconditionally and force a run under the bearer `AuthService.signOut` still had armed: the
    ex-user's holdings back on a signed-out Home Screen, a fresh 90-day widget token, and the
    gate left open for the rest of the process."""
    app = s["app"]
    est = _block(app, _ESTABLISH)
    cap = re.search(r"let\s+(\w+)\s*=\s*identityGeneration\b", est)
    assert cap and cap.start() < _idx(est, "await "), (
        "establishAuthenticatedSession no longer captures `identityGeneration` before its first "
        "await — a sign-out during the guest claim is laundered into the capture"
    )
    assert re.search(rf"await\s+onAuthenticated\(userId:\s*userId,\s*identity:\s*{cap.group(1)}\)", est), (
        "the captured identity is not handed to onAuthenticated"
    )
    oa = _block(app, _ON_AUTH)
    calls = re.findall(r"settleWidgetSession\(([^)]*)\)", oa)
    assert len(calls) == 2, f"onAuthenticated settles the widget {len(calls)} times (expected both branches)"
    for args in calls:
        assert re.fullmatch(r"\s*userId:\s*userId,\s*identity:\s*identity\s*", args), (
            f"settleWidgetSession({args}) does not pass the identity captured BEFORE the awaits — "
            "a re-read after them cannot see the sign-out that landed during them"
        )
    helper = _block(app, _SETTLE)
    fence = _IDENTITY_FENCE.search(helper)
    assert fence, (
        "settleWidgetSession reopens the widget gate without checking the session it settles "
        "still exists — `guard identity == identityGeneration else { … return }`"
    )
    for token in ("WidgetSnapshotStore.clearPortfolio()", "WidgetRefreshService.shared.openSession()",
                  "WidgetRefreshService.shared.refresh("):
        assert fence.start() < _idx(helper, token), f"`{token}` runs before the identity fence"


def _check_a_tokenless_launch_clears_orphaned_state(s: dict[str, str]) -> None:
    restore = _block(s["app"], _RESTORE)
    no_token = _block_at(restore, _idx(restore, "guard let token = authService.getStoredToken() else"))
    m = re.search(r"if\s+WidgetSnapshotStore\.hasAnyState\b[^{]*\{", no_token)
    assert m, (
        "the no-token branch clears the widget unconditionally (or not at all) — guard it on "
        "`WidgetSnapshotStore.hasAnyState` so an ordinary signed-out launch does not churn"
    )
    assert "WidgetRefreshService.shared.clearOrphanedState()" in _block_at(no_token, m.start()), (
        "widget state restored from a backup with no session is never cleared — the extension "
        "keeps fetching FMP data on a signed-out phone (auth.md §8a)"
    )
    assert "clearForEndedSession()" in _block(s["svc"], "func clearOrphanedState()")


def _check_the_dead_refresh_branch_forgets_the_user(s: dict[str, str]) -> None:
    restore = _block(s["app"], _RESTORE)
    after = restore[_idx(restore, "try await authService.refreshToken()"):]
    m = re.search(r"if\s+AppError\.from\(error\)\.isAuthError\s*\{", after)
    assert m, "the dead-refresh branch moved — this scan has drifted"
    dead = _block_at(after, m.start())
    reset = dead.find("lastAuthenticatedUserId = nil")
    assert reset != -1 and reset < _idx(dead, "discardDataForEndedSession()"), (
        "the dead-refresh branch discards the session but keeps `lastAuthenticatedUserId` — "
        "the same user signing back in skips the fan-out that rebuilds what was discarded"
    )


def _check_sync_tickers_reaches_the_widget(s: dict[str, str]) -> None:
    sync = _block(s["ps"], _SYNC)
    req = _idx(sync, "apiClient.request(")
    m = re.compile(r"if\s+portfolioId\s*==\s*activePortfolioId\s*\{").search(sync, req)
    assert m, "syncTickers no longer reaches the widget for the ACTIVE group after the server confirms"
    assert "WidgetRefreshService.shared.contentChanged()" in _block_at(sync, m.start()), (
        "an add / remove / reorder on the active group never reaches the Holdings tile"
    )
    # The switch reaches the widget through `activeGroupDidChangeNotification`; a direct call there
    # would sit among the two epoch guards test_ios_session_end_and_polling.py pins.
    assert "WidgetRefreshService" not in _block(s["ps"], "func setActivePortfolio(_ id: String) async")


def _check_background_hook(s: dict[str, str]) -> None:
    ios = s["ios"]
    anchor = ios.find("UIApplication.didEnterBackgroundNotification)")
    assert anchor != -1, "the didEnterBackground handler moved — this scan has drifted"
    handler = _block_at(ios, anchor)
    assert "WidgetRefreshService.shared.refreshOnBackground()" in handler, (
        "leaving the app no longer refreshes the Holdings tile"
    )


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "refresh_gate": _gate_on(_REFRESH),
    "content_gate": _gate_on("func contentChanged()"),
    "run_content_gate": _gate_on("private func runContentRefresh()"),
    "background_gate": _gate_on("func refreshOnBackground()"),
    "clear_closes_the_session": _check_clear_closes_the_session,
    "run_ownership": _check_run_ownership,
    "writes_epoch_fenced": _check_writes_are_epoch_fenced,
    "stamps_epoch_fenced": _check_stamps_are_epoch_fenced,
    "token_publish_fenced": _check_token_publish_is_fenced_on_the_runs_epoch,
    "token_expiry_backfilled": _check_token_expiry_is_backfilled,
    "identity_stamp_needs_portfolio": _check_identity_stamp_needs_the_portfolio_leg,
    "portfolio_write_owner": _check_portfolio_write_carries_the_owner,
    "owner_rechecked_after_fetch": _check_owner_is_rechecked_after_the_fetch,
    "stamp_needs_widget_token": _check_stamp_needs_a_widget_token,
    "pending_holdings_bypass_throttle": _check_pending_holdings_bypass_the_throttle,
    "content_observers": _check_content_observers,
    "content_debounced": _check_content_change_is_debounced_and_identity_neutral,
    "background_assertion": _check_background_refresh_holds_an_assertion,
    "configure_opens_before_seed": _check_configure_opens_the_session_before_the_seed,
    "on_authenticated_opens": _check_on_authenticated_opens_the_session,
    "settle_refuses_moved_identity": _check_settle_refuses_a_moved_identity,
    "tokenless_launch_clears": _check_a_tokenless_launch_clears_orphaned_state,
    "dead_refresh_forgets_user": _check_the_dead_refresh_branch_forgets_the_user,
    "sync_tickers_hook": _check_sync_tickers_reaches_the_widget,
    "background_hook": _check_background_hook,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_the_guard_holds(name):
    GUARDS[name](_sources())


# ── Mutations: each must make its guard FAIL (testing.md §3 rule 3, kept in-suite) ──

def _in(key: str, header: str, edit: Callable[[str], str]) -> Callable[[dict[str, str]], dict[str, str]]:
    def mutate(s: dict[str, str]) -> dict[str, str]:
        out = dict(s)
        out[key] = _edit_block(s[key], header, edit)
        return out
    return mutate


def _drop_gate(header: str):
    return _in("svc", header, _sub_once(r"guard\s+credentialReady\s*,\s*sessionOpen\s+else", "guard credentialReady else"))


def _move_settle_above_discard(s: dict[str, str]) -> dict[str, str]:
    def edit(body: str) -> str:
        tail = body.rindex(_SETTLE_CALL)
        body = body[:tail] + body[tail + len(_SETTLE_CALL):]
        anchor = body.index("if let userId, let previous = lastAuthenticatedUserId")
        return body[:anchor] + _SETTLE_CALL + "\n        " + body[anchor:]
    out = dict(s)
    out["app"] = _edit_block(s["app"], _ON_AUTH, edit)
    return out


def _relaunder_tail_settle(s: dict[str, str]) -> dict[str, str]:
    """The normal-path settle re-reads the generation AFTER the awaits instead of using the
    capture — which always matches, so a sign-out during the awaits passes the fence."""
    def edit(body: str) -> str:
        tail = body.rindex(_SETTLE_CALL)
        relaundered = "settleWidgetSession(userId: userId, identity: identityGeneration)"
        return body[:tail] + relaundered + body[tail + len(_SETTLE_CALL):]
    out = dict(s)
    out["app"] = _edit_block(s["app"], _ON_AUTH, edit)
    return out


def _move_owner_recheck_between_writes(body: str) -> str:
    """The owner re-check's natural-looking home between the two writes: its `await` then sits
    between the epoch fence and the portfolio write, so a sign-out during it writes the
    ex-user's holdings after `clearAll()`."""
    start = _idx(body, "var ownerChanged = false")
    let_p = re.search(r"let\s+p\s*:\s*WidgetMoverSnapshot\?\s*=\s*ownerChanged\s*\?\s*nil\s*:\s*fetchedPortfolio", body)
    assert let_p, "mutation target (the owner re-check block) not found"
    block = body[start:let_p.end()]
    body = body[:start] + body[let_p.end():]
    anchor = re.search(r"if\s+let\s+p\s*\{", body)
    assert anchor, "mutation target (the portfolio write) not found"
    return body[:anchor.start()] + block + "\n        " + body[anchor.start():]


def _open_after_seed(s: dict[str, str]) -> dict[str, str]:
    def edit(body: str) -> str:
        body = re.sub(r"if\s+authService\.hasStoredToken\s*\{\s*WidgetRefreshService\.shared\.openSession\(\)\s*\}",
                      "", body, count=1)
        seed = "WidgetRefreshService.shared.refresh(identity: identityGeneration)"
        return body.replace(seed, seed + "\n            if authService.hasStoredToken { "
                            "WidgetRefreshService.shared.openSession() }", 1)
    out = dict(s)
    out["app"] = _edit_block(s["app"], _CONFIGURE, edit)
    return out


MUTATIONS: list[tuple[str, str, Callable[[dict[str, str]], dict[str, str]]]] = [
    ("refresh-gate", "refresh_gate", _drop_gate(_REFRESH)),
    ("content-gate", "content_gate", _drop_gate("func contentChanged()")),
    ("run-content-gate", "run_content_gate", _drop_gate("private func runContentRefresh()")),
    ("background-gate", "background_gate", _drop_gate("func refreshOnBackground()")),
    ("clear-keeps-open", "clear_closes_the_session",
     _in("svc", "func clearForEndedSession()", _replace_once("sessionOpen = false", ""))),
    ("second-opener", "clear_closes_the_session",
     _in("svc", "func markCredentialReady()", _replace_once("credentialReady = true",
                                                            "credentialReady = true\n        sessionOpen = true"))),
    ("orphan-run", "run_ownership",
     _in("svc", "private func startRefresh()", _sub_once(r"guard\s+self\.runID\s*==\s*id\s+else\s*\{\s*return\s*\}", ""))),
    ("write-unfenced", "writes_epoch_fenced", _in("svc", _PERFORM, _replace_once(_EPOCH_CHECK, "true"))),
    ("recheck-between-writes", "writes_epoch_fenced", _in("svc", _PERFORM, _move_owner_recheck_between_writes)),
    ("write-fence-log-only", "writes_epoch_fenced",
     _in("svc", _PERFORM, _sub_once(r"guard\s+epoch\s*==\s*sessionEpoch\s+else\s*\{([^}]*?)\breturn\b",
                                    r"if !(epoch == sessionEpoch) {\1"))),
    ("write-cancel-log-only", "writes_epoch_fenced",
     _in("svc", _PERFORM, _sub_once(r"(if\s+Task\.isCancelled\s*\{[^}]*?)\breturn\b", r"\1"))),
    ("stamp-unfenced", "stamps_epoch_fenced",
     _in("svc", _PERFORM, _sub_once(r"(renewWidgetTokenIfNeeded.*?)epoch == sessionEpoch", r"\1true"))),
    ("stamp-fence-log-only", "stamps_epoch_fenced",
     _in("svc", _PERFORM, _sub_once(
         r"guard\s+epoch\s*==\s*sessionEpoch\s*,\s*!Task\.isCancelled\s+else\s*\{([^}]*?)\breturn\b",
         r"if !(epoch == sessionEpoch && !Task.isCancelled) {\1"))),
    ("token-unfenced", "token_publish_fenced", _in("svc", _RENEW, _replace_once(_EPOCH_CHECK, "true"))),
    ("token-fence-log-only", "token_publish_fenced",
     _in("svc", _RENEW, _sub_once(r"guard\s+epoch\s*==\s*sessionEpoch\s+else\s*\{([^}]*?)\breturn\b",
                                  r"if !(epoch == sessionEpoch) {\1"))),
    ("token-own-epoch", "token_publish_fenced",
     _in("svc", _RENEW, _sub_once(r"\{", "{\n        let epoch = sessionEpoch"))),
    ("expiry-per-launch", "token_expiry_backfilled",
     _in("svc", _RENEW, _replace_once("widgetTokenExpiry = WidgetAPIConfig.widgetTokenExpiry", "widgetTokenExpiry = nil"))),
    ("identity-on-partial", "identity_stamp_needs_portfolio",
     _in("svc", _PERFORM, _sub_once(r"if\s+p\s*!=\s*nil\s*\{\s*(lastRefreshIdentity = identity)\s*\}", r"\1"))),
    ("identity-at-completion", "identity_stamp_needs_portfolio",
     _in("svc", _PERFORM, _replace_once("lastRefreshIdentity = identity", "lastRefreshIdentity = inFlightIdentity"))),
    ("unowned-write", "portfolio_write_owner", _in("svc", _PERFORM, _replace_once(", owner: owner)", ")"))),
    ("owner-recheck", "owner_rechecked_after_fetch",
     _in("svc", _PERFORM, _sub_once(
         r"var ownerChanged = false.*?let\s+p\s*:\s*WidgetMoverSnapshot\?\s*=\s*ownerChanged\s*\?\s*nil\s*:\s*fetchedPortfolio",
         "let p: WidgetMoverSnapshot? = fetchedPortfolio"))),
    ("owner-recheck-ignored", "owner_rechecked_after_fetch",
     _in("svc", _PERFORM, _replace_once("ownerChanged ? nil : fetchedPortfolio", "fetchedPortfolio"))),
    ("stamp-without-token", "stamp_needs_widget_token",
     _in("svc", _PERFORM, _sub_once(r"guard\s+WidgetAPIConfig\.widgetToken\s*!=\s*nil\s+else\s*\{[^}]*\}", ""))),
    ("token-check-log-only", "stamp_needs_widget_token",
     _in("svc", _PERFORM, _sub_once(r"guard\s+WidgetAPIConfig\.widgetToken\s*!=\s*nil\s+else\s*\{([^}]*?)\breturn\b",
                                    r"if WidgetAPIConfig.widgetToken == nil {\1"))),
    ("pending-on-any-answer", "pending_holdings_bypass_throttle",
     _in("svc", _PERFORM, _sub_once(r"if\s+portfolioStored\s*,", "if p != nil,"))),
    ("pending-throttled", "pending_holdings_bypass_throttle",
     _in("svc", _REFRESH, _sub_once(r"guard\s+portfolioPending\s+else\s*\{\s*return\s*\}", "return"))),
    ("pending-outlives-session", "pending_holdings_bypass_throttle",
     _in("svc", "func clearForEndedSession()", _replace_once("portfolioPending = false", ""))),
    ("content-not-pending", "pending_holdings_bypass_throttle",
     _in("svc", "private func runContentRefresh()", _replace_once("markPortfolioPending()", ""))),
    ("no-watchlist-obs", "content_observers",
     _in("svc", "private func observeContentChanges()", _replace_once("PortfolioStore.watchlistDidChangeNotification,", ""))),
    ("no-observers", "content_observers", _in("svc", "private init()", _replace_once("observeContentChanges()", ""))),
    ("undebounced", "content_debounced",
     _in("svc", "func contentChanged()", _sub_once(r"try\?\s*await\s+Task\.sleep\([^)]*\)", ""))),
    ("identity-clobber", "content_debounced",
     _in("svc", "func contentChanged()", _replace_once("self.runContentRefresh()", "self.refresh(force: true, identity: nil)"))),
    ("leaked-assertion", "background_assertion",
     _in("svc", "private func holdBackgroundAssertionUntilSettled()",
         _sub_once(r"(beginBackgroundTask\(.*?)app\.endBackgroundTask\(assertion\)", r"\1"))),
    ("held-assertion", "background_assertion",
     _in("svc", "private func holdBackgroundAssertionUntilSettled()",
         _sub_once(r"(waitForRunsToSettle\(\).*?)app\.endBackgroundTask\(assertion\)", r"\1"))),
    ("no-migrate", "configure_opens_before_seed",
     _in("app", _CONFIGURE, _replace_once("WidgetSnapshotStore.migrateLegacyIfNeeded()", ""))),
    ("ungated-open", "configure_opens_before_seed",
     _in("app", _CONFIGURE, _replace_once("if authService.hasStoredToken {", "if true {"))),
    ("late-open", "configure_opens_before_seed", _open_after_seed),
    ("heal-gated", "on_authenticated_opens",
     _in("app", _ON_AUTH, _sub_once(r"settleWidgetSession\(userId: userId, identity: identity\)(\s*return)", r"\1"))),
    ("open-before-discard", "on_authenticated_opens", _move_settle_above_discard),
    ("settle-unfenced", "settle_refuses_moved_identity",
     _in("app", _SETTLE, _sub_once(r"guard\s+identity\s*==\s*identityGeneration\s+else\s*\{([^}]*?)\breturn\b",
                                   r"if identity != identityGeneration {\1"))),
    ("settle-late-capture", "settle_refuses_moved_identity",
     _in("app", _ESTABLISH, _sub_once(
         r"let identity = identityGeneration\s*(await claimGuestDataForIncomingIdentity\(userId: userId\))",
         r"\1\n        let identity = identityGeneration"))),
    ("settle-relaundered", "settle_refuses_moved_identity", _relaunder_tail_settle),
    ("helper-no-open", "on_authenticated_opens",
     _in("app", _SETTLE, _replace_once("WidgetRefreshService.shared.openSession()", ""))),
    ("helper-no-owner", "on_authenticated_opens",
     _in("app", _SETTLE, _replace_once("WidgetSnapshotStore.clearPortfolio()", ""))),
    ("restore-churn", "tokenless_launch_clears",
     _in("app", _RESTORE, _sub_once(r"if\s+WidgetSnapshotStore\.hasAnyState[^{]*\{", "if true {"))),
    ("restore-no-clear", "tokenless_launch_clears",
     _in("app", _RESTORE, _replace_once("WidgetRefreshService.shared.clearOrphanedState()", ""))),
    ("dead-refresh-id", "dead_refresh_forgets_user",
     _in("app", _RESTORE, _sub_once(r"(refreshToken\(\).*?)lastAuthenticatedUserId = nil", r"\1"))),
    ("sync-any-group", "sync_tickers_hook",
     _in("ps", _SYNC, _replace_once("if portfolioId == activePortfolioId {", "if true {"))),
    ("sync-no-hook", "sync_tickers_hook",
     _in("ps", _SYNC, _replace_once("WidgetRefreshService.shared.contentChanged()", ""))),
    ("background-no-hook", "background_hook",
     lambda s: {**s, "ios": s["ios"].replace("WidgetRefreshService.shared.refreshOnBackground()", "", 1)}),
]


@pytest.mark.parametrize("label,guard,mutate", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_every_guard_kills_its_mutation(label, guard, mutate):
    sources = _sources()
    mutated = mutate(sources)
    assert mutated != sources, f"mutation {label!r} changed nothing — it no longer matches the source"
    with pytest.raises(AssertionError):
        GUARDS[guard](mutated)


def test_every_guard_has_a_mutation():
    """A guard with no mutation is one nobody has seen fail."""
    covered = {guard for _, guard, _ in MUTATIONS}
    assert covered == set(GUARDS), f"guards with no mutation: {sorted(set(GUARDS) - covered)}"


def test_the_comment_stripper_actually_strips():
    """The CONTROL: every guarded token, written only in comments, must vanish."""
    prose = (
        "// guard credentialReady, sessionOpen else {\n"
        "/// sessionOpen = false  WidgetRefreshService.shared.refreshOnBackground()\n"
        "/* guard self.runID == id else { return }\n   epoch == sessionEpoch */\n"
        'let url = "https://example.com"  // lastRefreshIdentity = inFlightIdentity\n'
        "/// guard identity == identityGeneration else { return }  portfolioPending = false\n"
        "x()  // guard WidgetAPIConfig.widgetToken != nil else { return }\n"
    )
    code = _strip(prose)
    for token in ("sessionOpen", "runID", "sessionEpoch", "refreshOnBackground", "lastRefreshIdentity",
                  "identityGeneration", "portfolioPending", "widgetToken"):
        assert token not in code, f"{token!r} survived the stripper"
    assert "https://example.com" in code, "the stripper ate a URL literal"
    assert len(code.splitlines()) == len(prose.splitlines()), "the stripper must keep line structure"
