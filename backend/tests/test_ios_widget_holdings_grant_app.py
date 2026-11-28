"""The APP half of the Holdings widget grant (migration 216, auth.md §8c), pinned from the Swift source.

The grant is a 30-day, per-device, server-revocable key the CaydexWidgets extension sends to
`GET /widget/holdings-mover` so the Holdings tile refreshes with the app closed. The server mints
it ONLY inside a refresh-token rotation (`POST /auth/refresh` with `widget_holdings_grant: true`),
so a stolen access token can never become a 30-day credential. The extension half (Keychain item,
fetcher, store fences, entitlements) is `test_ios_widget_holdings_grant.py`; this file pins what
the APP does, because each of these is a failure that leaves a tile that renders — for the wrong
person, never again, or with a session quietly ended underneath it:

  G1  the refresh asks for a grant only when the widget wants one: `widgetHoldingsGrant` is an
      Optional on the wire body, set to `true` or nil — never a `false` that older backends see;
  G2  `PUT /widget/push-token` is a `.PUT`, `.signInRequired` (no `default:`), and its body sends a
      nil token as an explicit `null` (the server's "clear");
  G3  `AuthService.refreshToken()` reads the flag ONCE before sending, hands a minted grant over only
      AFTER its epoch fence, `saveTokens` and `setAuthToken`, without `try`/`await` (the widget can
      never fail or delay the refresh), revokes a grant that arrived for a session that changed,
      and reports a requested-but-missing grant (the back-off);
  G4  a fresh install revokes an earlier install's grant keys (Keychain items outlive the app);
  G5  revoke THEN delete (D16): a key is deleted only after the server confirmed its revoke, and
      every revoke-then-delete runs on a detached task (the delete is a Keychain write);
  G6  a THROWN `currentConfigurations()` keeps the grant state as is (D10) — only a successful
      answer with no Movers widget retires the grant;
  G7  the forced refresh for an urgent grant runs only from `startForcedGrantRefreshIfAllowed`,
      which checks `applicationState == .active` and the back-off BEFORE it starts, and nothing on
      the background trigger reaches it (D20);
  G8  that refresh never ends a session (the 401 interceptor alone does — auth.md §3/§5), retries
      only on `.transientFailure`, at offsets inside the server's 120 s grace window, under its
      own background-task assertion ended on completion AND expiration;
  G9  the back-off is 10 min → 1 h → 6 h → 24 h, reset by an adoption and at session end;
  G10 adoption: the bearer's subject AND session must be the grant's before anything is stored;
      the Keychain item is written BEFORE the marker; the marker only behind an epoch fence after
      the last await; a key stored for a session that ended meanwhile is revoked (compensation);
      the other keys are swept only after the marker moved;
  G11 a sweep never revokes the live grant or one being adopted;
  G12 the push token: `#available(iOS 26` before `currentPushInfo` (read nowhere else in the app),
      the fingerprint saved only when the server stored it, a day-long resend, `grant_usable:
      false` treated as urgent, one `apnsEnvironment` shared with alert registration;
  G13 a session end drops every per-session grant flag (auth.md §7) and revokes every key.
  G14 the wire agrees with the SERVER's models, read live from `app.schemas`: the push-token body
      sends exactly `WidgetPushTokenRequest`'s fields (extra=forbid → 422), `apnsEnvironment` says
      exactly its Literal, the token is `%02x` per byte and the client's length gate equals the
      server validator's, and every refresh-body key is a `RefreshTokenRequest` field (that model
      ignores unknown keys, so a misspelt flag would never mint, silently).

Session-end ORDER (the marker before the widget token and the snapshots) is pinned in
`test_signout_and_session_hygiene.py`; the assessment's place in `performRefresh` in
`test_ios_widget_refresh_lifecycle.py`; the lossy grant decode in `test_ios_response_schema_parity.py`;
`APIClient.refreshForWidgetGrant()` itself in `test_ios_transient_refresh_keeps_session.py` (T1c).

Per `.claude/rules/testing.md` §3 every scan is comment-stripped (`//`, `///`, `/* */`) and
brace-bounded to its declaration; `test_every_guard_kills_its_mutation` applies each MUTATION to an
in-memory copy (the Swift files are never written) and asserts the guard FAILS on it FOR THE RIGHT
REASON: a lost anchor raises `_ScanDrift` (a LookupError, never an AssertionError), and a mutation
that only breaks an anchor fails the harness instead of counting as a kill
(`test_an_anchor_only_break_is_not_counted_as_a_kill` is that harness's control).
CONTROL: `test_the_comment_stripper_actually_strips`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_SERVICE = _IOS / "Core" / "Services" / "WidgetRefreshService.swift"
_AUTH = _IOS / "Core" / "Services" / "AuthService.swift"
_ENDPOINT = _IOS / "Core" / "Services" / "APIEndpoint.swift"
_PUSH = _IOS / "Core" / "Services" / "PushNotificationManager.swift"

_ASSESS = "private func assessHoldingsGrant(client: APIClient, epoch: Int) async"
_START_FORCED = "private func startForcedGrantRefreshIfAllowed(why: String)"
_RUN_FORCED = "private func runForcedGrantRefresh(epoch: Int) async"
_ATTEMPT = "private func attemptForcedGrantRefreshes(epoch: Int) async"
_ADOPT = "func adoptHoldingsGrant(_ payload: WidgetHoldingsGrantPayload, owner: String)"
_PUBLISH = "private func publishHoldingsGrant(_ grant: WidgetHoldingsGrant.Grant, epoch: Int) async"
_SWEEP = "private func sweepHoldingsGrantKeys(keeping keep: String?, reason: String)"
_REVOKE_DELETE = "nonisolated private static func revokeThenDelete("
_FRESH_REVOKE = "nonisolated static func revokeHoldingsGrantKeysLeftByAnEarlierInstall()"
_END = "private func endHoldingsGrantForEndedSession(endedFingerprint: String?)"
_PUSH_SYNC = "private func syncWidgetPushTokenIfNeeded(client: APIClient, epoch: Int, grant: WidgetHoldingsGrant.Grant) async"
_BACKGROUND = "func refreshOnBackground()"
_CLEAR = "func clearForEndedSession()"
_REFRESH = "func refreshToken() async throws"
_PURGE = "func purgeSessionLeftByAnEarlierInstall() async"

# A FENCE, not a mention: the epoch compared in a `guard` whose else-body returns.
_EPOCH_FENCE = re.compile(r"guard\s+epoch\s*==\s*sessionEpoch[^{]*else\s*\{[^}]*\breturn\b")
# The server's refresh grace window (`config.py`, REFRESH_TOKEN_GRACE_SECONDS): a replay after it
# is reuse, and the server ends the session.
_GRACE_SECONDS = 120


# ── Scanning helpers ─────────────────────────────────────────────────────────────────

class _ScanDrift(LookupError):
    """An ANCHOR the scan needs is gone (a header, a token it measures from, a block). Never a
    guard verdict: it is deliberately not an AssertionError, so a mutation that only breaks an
    anchor is never counted as a kill (testing.md §3 — killed for the RIGHT reason). On the real
    source it still fails `test_the_guard_holds` loudly, as an uncaught error."""


def _need(found, what: str):
    """`found`, or `_ScanDrift(what)` — for an anchor, never for the property a guard asserts."""
    if not found:
        raise _ScanDrift(f"{what} — this scan has drifted")
    return found


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip(src: str) -> str:
    """Drop `/* */` blocks, then `//` and `///` tails, keeping line structure. `(?<![:/])` keeps
    the `//` of a `https://` literal. The comments here NAME every token asserted below."""
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return "\n".join(re.sub(r"(?<![:/])//.*$", "", line) for line in src.splitlines())


def _block_at(src: str, idx: int) -> str:
    """The brace-balanced block opened by the first `{` at or after `idx`."""
    open_brace = src.find("{", idx)
    if open_brace == -1:
        raise _ScanDrift(f"no block opens after offset {idx}")
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace: i + 1]
    raise _ScanDrift(f"unbalanced braces after offset {idx}")


def _block(src: str, header: str) -> str:
    """The body of the ONLY declaration whose header is the literal `header`."""
    if src.count(header) != 1:
        raise _ScanDrift(f"{header!r} occurs {src.count(header)}× — this scan has drifted")
    return _block_at(src, src.find(header) + len(header))


def _idx(src: str, token: str, start: int = 0) -> int:
    """Where an ANCHOR sits. A token whose absence IS the regression is asserted by its guard
    with `str.find`, never located through this (its loss would read as drift, not a kill)."""
    i = src.find(token, start)
    if i == -1:
        raise _ScanDrift(f"{token!r} not found — this scan has drifted")
    return i


def _sources() -> dict[str, str]:
    return {
        "svc": _strip(_read(_SERVICE)),
        "auth": _strip(_read(_AUTH)),
        "ep": _strip(_read(_ENDPOINT)),
        "push": _strip(_read(_PUSH)),
    }


def _arm_returning(switch_body: str, case: str) -> str | None:
    """The `return .X` the arm naming `.case` ends in, in a flat `case …: return .X` switch."""
    for m in re.finditer(r"case\s+([^:]*?)\s*:\s*return\s+\.(\w+)", switch_body, flags=re.S):
        if re.search(rf"\.{re.escape(case)}\b", m.group(1)):
            return m.group(2)
    return None


# ── The guards ───────────────────────────────────────────────────────────────────────

def _check_refresh_flag_wire(s: dict[str, str]) -> None:  # G1
    ep = s["ep"]
    assert re.search(r"case\s+refreshToken\(\s*refreshToken:\s*String\s*,\s*widgetHoldingsGrant:\s*Bool\s*=\s*false\s*\)", ep), (
        "`.refreshToken` no longer carries `widgetHoldingsGrant: Bool = false` — the widget cannot ask "
        "the server for its grant (it is minted ONLY inside a refresh rotation)")
    body = _block(ep, "nonisolated var body: (any Encodable & Sendable)?")
    arm = re.search(r"case\s+\.refreshToken\(\s*let\s+\w+\s*,\s*let\s+(\w+)\s*\)\s*:", body)
    assert arm, "the body arm no longer binds the grant flag"
    flag = arm.group(1)
    after = body[arm.end(): body.find("case ", arm.end())]
    assert re.search(rf"widgetHoldingsGrant:\s*{flag}\s*\?\s*true\s*:\s*nil\b", after), (
        "the refresh body no longer sends the grant flag as `true` or ABSENT — a `false` on the wire "
        "is a new field every older backend must tolerate for nothing")
    req = _block(ep, "nonisolated struct RefreshTokenRequest: Encodable, Sendable")
    assert re.search(r"\bwidgetHoldingsGrant:\s*Bool\?", req), (
        "RefreshTokenRequest.widgetHoldingsGrant is no longer Optional — the synthesised encoder "
        "would send `false` on every refresh")
    assert "encode(to" not in req, "RefreshTokenRequest hand-encodes — the absent-when-nil rule is no longer the synthesised one"


def _check_push_token_endpoint(s: dict[str, str]) -> None:  # G2
    ep = s["ep"]
    assert re.search(r"case\s+registerWidgetPushToken\(\s*grantId:\s*String\s*,\s*token:\s*String\?\s*,\s*environment:\s*String\s*\)", ep), (
        "the push-token endpoint case changed shape")
    path = _block(ep, "nonisolated var path: String")
    assert re.search(r'case\s+\.registerWidgetPushToken\s*:\s*return\s+"/api/v1/widget/push-token"', path), (
        "the push-token path moved")
    method = _block(ep, "nonisolated var method: HTTPMethod")
    assert _arm_returning(method, "registerWidgetPushToken") == "PUT", (
        "`.registerWidgetPushToken` is not in a `.PUT` arm — `method` ends in `default: return .GET`, "
        "so the upload would go out as a GET and 405")
    policy = ep[_idx(ep, "nonisolated var authPolicy: AuthPolicy"):_idx(ep, "nonisolated var requiresAuth: Bool")]
    assert _arm_returning(policy, "registerWidgetPushToken") == "signInRequired", (
        "`.registerWidgetPushToken` is not `.signInRequired` — the route sits on the strict router")
    body = _block(ep, "nonisolated struct WidgetPushTokenRequestBody: Encodable, Sendable")
    enc = _block(body, "func encode(to encoder: Encoder) throws")
    assert re.search(r"try\s+container\.encode\(\s*token\s*,\s*forKey:\s*\.token\s*\)", enc), (
        "a nil push token is no longer sent as an explicit `null` — an absent key is not the server's 'clear'")
    assert "encodeIfPresent" not in enc, "the push-token body omits a nil token instead of sending null"


def _check_refresh_hands_over_after_arming(s: dict[str, str]) -> None:  # G3
    body = _block(s["auth"], _REFRESH)
    read = re.search(r"let\s+(\w+)\s*=\s*WidgetHoldingsGrant\.requestOnNextRefresh\b", body)
    send = _idx(body, "apiClient.request(")
    assert read and read.start() < send, (
        "refreshToken no longer reads the widget's grant request ONCE before sending — the flag the "
        "answer is judged against must be the one this request carried")
    flag = read.group(1)
    assert re.search(rf"\.refreshToken\(\s*refreshToken:\s*\w+\s*,\s*widgetHoldingsGrant:\s*{flag}\s*\)", body[send:]), (
        "the refresh request does not carry the flag it read")
    fence = re.search(r"guard\s+epoch\s*==\s*sessionEpoch\s+else\s*\{", body[send:])
    assert fence, (
        "refreshToken lost its post-request `guard epoch == sessionEpoch else {` — a grant (and the "
        "tokens) of a session that changed mid-refresh would be handed over")
    fence_at = send + fence.start()
    else_body = _block_at(body, fence_at)
    assert "discardHoldingsGrant(" in else_body and re.search(r"\breturn\b", else_body), (
        "a grant that arrived for a session that changed mid-refresh is not revoked — it stays LIVE "
        "on the server for 30 days with no device holding it")
    save = _idx(body, "saveTokens(", fence_at)
    arm = _idx(body, "await apiClient.setAuthToken(response.accessToken)", fence_at)
    adopt = re.search(r"WidgetRefreshService\.shared\.adoptHoldingsGrant\(", body)
    assert adopt, "refreshToken no longer hands the minted grant to the widget service"
    assert s["auth"].count("adoptHoldingsGrant(") == 1, (
        "AuthService hands a grant over outside refreshToken — a grant is minted ONLY by a refresh "
        "rotation, and no other answer may carry one into the widget")
    assert save < adopt.start() and arm < adopt.start(), (
        "the grant is handed over before the refreshed session is stored AND armed — adoption fences "
        "on the armed bearer's subject and session, which would still be the old token's")
    line = body[body.rfind("\n", 0, adopt.start()) + 1: body.find("\n", adopt.start())]
    assert not re.search(r"\b(?:try|await)\b", line), (
        "the grant hand-over is awaited or can throw — the widget may never fail or delay the refresh")
    missing = re.search(rf"else\s+if\s+{flag}\s*\{{", body)
    assert missing and "noteHoldingsGrantNotIssued()" in _block_at(body, missing.start()), (
        "a refresh that asked for a grant and got none is not reported — the forced-refresh back-off "
        "never advances, and a switched-off server is refreshed into every minute")


def _check_fresh_install_revokes_grant_keys(s: dict[str, str]) -> None:  # G4
    purge = _block(s["auth"], _PURGE)
    fresh = purge[_idx(purge, "case .fresh:"):_idx(purge, "case .updated:")]
    assert "WidgetRefreshService.revokeHoldingsGrantKeysLeftByAnEarlierInstall()" in fresh, (
        "a fresh install no longer revokes an earlier install's grant keys — Keychain items outlive "
        "the app, and each is LIVE on the server for up to 30 days")
    fn = _block(s["svc"], _FRESH_REVOKE)
    detached = re.search(r"Task\.detached\b[^{]*\{", fn)
    assert detached, "the fresh-install revoke no longer runs on a detached task — a Keychain sweep at launch on the main actor"
    inner = _block_at(fn, detached.start())
    assert re.search(r"WidgetHoldingsGrant\.pendingKeys\(\s*excluding:\s*nil\s*\)", inner) and "revokeThenDelete(" in inner, (
        "the fresh-install purge no longer revokes EVERY stored grant key, then deletes it")


def _check_revoke_then_delete(s: dict[str, str]) -> None:  # G5
    svc = s["svc"]
    fn = _block(svc, _REVOKE_DELETE)
    confirmed = re.search(r"if\s+await\s+WidgetHoldingsFetcher\.revoke\(", fn)
    assert confirmed, (
        "revokeThenDelete no longer asks the server first (`if await WidgetHoldingsFetcher.revoke(`) — "
        "a key deleted unrevoked stays LIVE on the server for up to 30 days")
    inside = _block_at(fn, confirmed.start())
    assert "WidgetHoldingsGrant.delete(fingerprint:" in inside, "a revoked key is no longer deleted"
    assert fn.count("WidgetHoldingsGrant.delete(") == 1, (
        "revokeThenDelete deletes a key outside the `if <revoke confirmed>` branch — an offline "
        "sign-out (no logout, so no server trigger) strands a live 30-day key no device can revoke")
    for name, src in (("WidgetRefreshService", svc), ("AuthService", s["auth"])):
        stray = src.count("WidgetHoldingsGrant.delete(") - (1 if name == "WidgetRefreshService" else 0)
        assert stray == 0, f"{name} deletes a grant key outside revokeThenDelete — the key dies before its revoke"
    decl_at = svc.find(_REVOKE_DELETE)
    detached = [(m.start(), m.start() + len(_block_at(svc, m.start())) + (svc.find("{", m.start()) - m.start()))
                for m in re.finditer(r"Task\.detached\b[^{]*\{", svc)]
    for m in re.finditer(r"\brevokeThenDelete\(", svc):
        if m.start() == decl_at + len("nonisolated private static func "):
            continue
        assert any(a <= m.start() < b for a, b in detached), (
            "revokeThenDelete is called outside a `Task.detached` — its Keychain delete would run on "
            f"the main actor (line: {svc[svc.rfind(chr(10), 0, m.start()) + 1: svc.find(chr(10), m.start())].strip()!r})")


def _check_config_throw_keeps_state(s: dict[str, str]) -> None:  # G6
    assess = _block(s["svc"], _ASSESS)
    call = _idx(assess, "WidgetCenter.shared.currentConfigurations()")
    do = assess.rfind("do {", 0, call)
    assert do != -1, "the installed-widgets query is no longer inside a do/catch"
    catch = re.search(r"\}\s*catch\s*\{", assess[call:])
    assert catch, "the installed-widgets query has no catch"
    handler = _block_at(assess, call + catch.start() + catch.group(0).index("{") - 1 + 1)
    assert re.search(r"\breturn\b", handler), (
        "a THROWN currentConfigurations() no longer returns — a transient WidgetKit error must keep the "
        "grant state as is (D10)")
    for killer in ("retireHoldingsGrantForRemovedWidget", "removeMarker", "sweepHoldingsGrantKeys",
                   "revoke", "installed = false"):
        assert killer not in handler, (
            f"a THROWN currentConfigurations() reaches `{killer}` — a transient WidgetKit error kills a live grant")
    retire = [m.start() for m in re.finditer(r"retireHoldingsGrantForRemovedWidget\(\)", assess)]
    assert len(retire) == 1, f"retireHoldingsGrantForRemovedWidget() is called {len(retire)}× in the assessment"
    guard = re.search(r"guard\s+installed\s+else\s*\{", assess)
    assert guard and "retireHoldingsGrantForRemovedWidget()" in _block_at(assess, guard.start()), (
        "the grant is retired other than on a SUCCESSFUL answer with no Movers widget")


def _check_forced_refresh_foreground_only(s: dict[str, str]) -> None:  # G7
    svc = s["svc"]
    for callee, only_in in (("refreshForWidgetGrant(", _ATTEMPT),
                            ("attemptForcedGrantRefreshes(epoch:", _RUN_FORCED),
                            ("runForcedGrantRefresh(epoch:", _START_FORCED)):
        body = _block(svc, only_in)
        assert body.count(callee) == 1, f"`{callee}` is not called exactly once from its one caller"
        decl = "func " + callee.split("(")[0] + "("
        total = svc.count(callee) - (1 if decl.split("func ")[1] in svc and svc.count(decl) else 0)
        assert total == 1, (
            f"`{callee}` is reached outside `{only_in}` — the forced grant refresh must have ONE door, "
            "the foreground-only gate")
    start = _block(svc, _START_FORCED)
    task = _idx(start, "Task {")
    active = re.search(r"guard\s+UIApplication\.shared\.applicationState\s*==\s*\.active\s+else\s*\{[^}]*\breturn\b", start)
    assert active and active.start() < task, (
        "the forced grant refresh can start outside the foreground — it rotates the session, and a "
        "suspended app cannot replay a lost answer inside the 120 s grace window (D20)")
    backoff = re.search(r"if\s+let\s+until\s*=\s*Self\.grantBackoffUntil\(\)\s*,\s*until\s*>\s*Date\(\)\s*\{[^}]*\breturn\b", start)
    assert backoff and backoff.start() < task, "the forced grant refresh ignores its back-off"
    one = re.search(r"guard\s+forcedGrantRefresh\s*==\s*nil\s+else\s*\{\s*return\s*\}", start)
    assert one and one.start() < task, "two forced grant refreshes can run at once"
    background = _block(svc, _BACKGROUND)
    for token in ("startForcedGrantRefreshIfAllowed", "runForcedGrantRefresh", "refreshForWidgetGrant",
                  "attemptForcedGrantRefreshes"):
        assert token not in background, f"refreshOnBackground reaches `{token}` — never from the background trigger"


def _check_forced_refresh_never_ends_session(s: dict[str, str]) -> None:  # G8
    svc = s["svc"]
    attempt = _block(svc, _ATTEMPT)
    for ender in ("clearForEndedSession", "signOut", "clearToken", "disarmAuthToken", "setAuthToken",
                  "handleAuthFailure", "removeMarker", "authFailureHandler"):
        assert ender not in attempt, (
            f"the forced grant refresh acts on its outcome with `{ender}` — a rejected refresh ends the "
            "session only through the 401 interceptor (auth.md §3/§5)")
    sw = _need(re.search(r"switch\s+outcome\s*\{", attempt),
               "the forced grant refresh no longer matches its outcome in one switch")
    switch = _block_at(attempt, sw.start())
    arms = {m.group(1): None for m in re.finditer(r"case\s+\.(\w+)\s*:", switch)}
    assert set(arms) == {"refreshed", "credentialRejected", "transientFailure"}, f"outcome arms {sorted(arms)}"
    heads = list(re.finditer(r"case\s+\.(\w+)\s*:", switch))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(switch) - 1
        arms[h.group(1)] = switch[h.end():end]
    assert re.search(r"\breturn\b", arms["refreshed"]) and re.search(r"\breturn\b", arms["credentialRejected"]), (
        "the forced grant refresh retries on an outcome other than `.transientFailure` — a rejected "
        "refresh token replayed is REUSE, and a refreshed one has nothing left to replay")
    assert not re.search(r"\breturn\b", arms["transientFailure"]), (
        "the forced grant refresh no longer retries a `.transientFailure` — a lost answer must be "
        "replayed inside the grace window or the next refresh is reuse (D20)")
    offsets = _need(re.search(r"forcedGrantRetryOffsets:\s*\[TimeInterval\]\s*=\s*\[([^\]]*)\]", svc),
                    "the retry offsets moved")
    values = [float(v) for v in re.findall(r"\d+(?:\.\d+)?", offsets.group(1))]
    assert values == sorted(values) and len(values) == 2 and all(0 < v < _GRACE_SECONDS for v in values), (
        f"the forced grant refresh retries at {values} s — both must sit inside the server's "
        f"{_GRACE_SECONDS} s grace window, in order")
    run = _block(svc, _RUN_FORCED)
    begin = re.search(r"beginBackgroundTask\(withName:[^{]*\{", run)
    assert begin, "the forced grant refresh holds no background-task assertion"
    handler = _block_at(run, begin.start())
    assert "app.endBackgroundTask(assertion)" in handler, "the assertion is not ended on expiration — iOS kills the app"
    tail = run[begin.start() + len(handler):]
    ended = tail.find("app.endBackgroundTask(assertion)")
    assert ended != -1 and _idx(tail, "await attemptForcedGrantRefreshes(epoch: epoch)") < ended, (
        "the assertion is not ended after the attempts complete")


def _check_backoff_steps(s: dict[str, str]) -> None:  # G9
    svc = s["svc"]
    steps = _need(re.search(r"grantBackoffSteps:\s*\[TimeInterval\]\s*=\s*\[([^\]]*)\]", svc),
                  "the back-off steps moved")
    values = []
    for term in steps.group(1).split(","):
        product = 1
        for factor in re.findall(r"\d+", term):
            product *= int(factor)
        values.append(product)
    assert values == [600, 3600, 21600, 86400], (
        f"the grant back-off is {values} s — it must be 10 min → 1 h → 6 h → 24 h")
    publish = _block(svc, _PUBLISH)
    reset = publish.find("Self.resetGrantBackoff()")
    assert reset != -1 and _idx(publish, "WidgetHoldingsGrant.setMarker(") < reset, (
        "an adoption no longer resets the back-off — a device that once backed off for 24 h keeps "
        "waiting after it finally got a grant")
    assert "Self.resetGrantBackoff()" in _block(svc, _END), "a session end no longer resets the back-off"
    step = _block(svc, "private func stepGrantBackoff()")
    assert re.search(r"min\(\s*max\(\s*0\s*,", step), "the back-off step is no longer clamped to the table"


def _check_adoption_fences(s: dict[str, str]) -> None:  # G10
    svc = s["svc"]
    adopt = _block(svc, _ADOPT)
    assert re.search(r"guard\s+sessionOpen\s+else\s*\{[^}]*discardHoldingsGrant\(", adopt), (
        "a grant arriving with no widget session open is not revoked")
    insert = adopt.find("adoptingGrantFingerprints.insert(grant.fp)")
    task = _idx(adopt, "Task {")
    assert insert != -1 and insert < task and "adoptingGrantFingerprints.remove(grant.fp)" in _block_at(adopt, task), (
        "the grant is not protected from a sweep for the whole of its adoption")
    pub = _block(svc, _PUBLISH)
    owner = re.search(r"Self\.subject\(of:\s*bearer\)\?\.lowercased\(\)\s*==\s*grant\.userId\.lowercased\(\)", pub)
    sid = re.search(r"Self\.sessionId\(of:\s*bearer\)", pub)
    store = _idx(pub, "WidgetHoldingsGrant.store(grant)")
    assert owner and owner.start() < store, (
        "the grant is stored without checking it belongs to the ARMED account — an account switched "
        "during the refresh would get the previous account's key")
    assert sid and sid.start() < store, (
        "the grant is stored without checking it belongs to the ARMED session — a password change "
        "would leave an evicted key presented to the extension")
    marker = _idx(pub, "WidgetHoldingsGrant.setMarker(grant.fp)")
    assert store < marker, "the marker is set before the Keychain item exists — the extension would read a key that is not there"
    window = pub[pub.rfind("await", 0, marker):marker]
    fence = _EPOCH_FENCE.search(window)
    assert fence, (
        "the marker is not behind `guard epoch == sessionEpoch … else { … return }` after the last "
        "await — a session that ended during the Keychain write gets a live key presented")
    assert "revokeThenDeleteDetached(" in _block_at(window, fence.start()), (
        "a key stored for a session that ended meanwhile is not revoked (the compensating half of the write)")
    sweep = re.search(r"sweepHoldingsGrantKeys\(\s*keeping:\s*grant\.fp", pub)
    assert sweep and marker < sweep.start(), (
        "the other keys are swept before the marker moved — the extension could be using the key "
        "being revoked")


def _check_sweep_protects_live_and_adopting(s: dict[str, str]) -> None:  # G11
    sweep = _block(s["svc"], _SWEEP)
    flt = re.search(r"\.filter\s*\{", sweep)
    assert flt, "the sweep no longer filters what it revokes"
    body = _block_at(sweep, flt.start())
    for token in ("keep", "self.liveGrantFingerprint", "self.adoptingGrantFingerprints.contains("):
        assert token in body, (
            f"the sweep can revoke a key it must keep (its filter lost `{token}`) — the live grant, or "
            "one adopted while the Keychain was being listed")
    assert _idx(sweep, "WidgetHoldingsGrant.pendingKeys(") < flt.start(), "the sweep decides before it lists"


def _check_push_token_sync(s: dict[str, str]) -> None:  # G12
    svc = s["svc"]
    sync = _block(svc, _PUSH_SYNC)
    avail = re.search(r"guard\s+#available\(iOS\s+26(?:\.0)?\s*,\s*\*\)\s+else\s*\{\s*return\s*\}", sync)
    assert avail and avail.start() < _idx(sync, "WidgetCenter.shared.currentPushInfo"), (
        "currentPushInfo is read without the iOS 26 gate — the deployment target is 18.0")
    assert svc.count("currentPushInfo") == 1 and s["auth"].count("currentPushInfo") == 0, (
        "currentPushInfo is read outside syncWidgetPushTokenIfNeeded")
    stored = re.search(r"if\s+response\.stored\s*\{", sync)
    assert stored, "the push fingerprint is no longer conditional on the server storing the token"
    saves = [m.start() for m in re.finditer(r"defaults\.set\(\s*fingerprint\s*,", sync)]
    inside = _block_at(sync, stored.start())
    assert len(saves) == 1 and "defaults.set(fingerprint," in inside, (
        "the push fingerprint is saved for a token the server did not store — it would not be re-sent for a day")
    assert re.search(r"pushTokenResendInterval:\s*TimeInterval\s*=\s*24\s*\*\s*60\s*\*\s*60", svc), (
        "the push token is no longer re-sent daily")
    assert re.search(r"if\s+response\.grantUsable\s*==\s*false\s*\{[^}]*grantCalledUnusable\(", sync), (
        "`grant_usable: false` is no longer treated as urgent")
    unusable = _block(svc, "private func grantCalledUnusable(_ grant: WidgetHoldingsGrant.Grant)")
    assert "WidgetHoldingsGrant.requestOnNextRefresh = true" in unusable, "an unusable grant does not ask for a new one"
    assert "PushNotificationManager.apnsEnvironment" in sync, "the widget token's environment is not the alert one's"
    assert re.search(r"nonisolated\s+static\s+var\s+apnsEnvironment:\s*String", s["push"]), (
        "apnsEnvironment is no longer the one static mapping shared by alert and widget registration")
    for header in (_END, "private func retireHoldingsGrantForRemovedWidget()"):
        assert "Self.clearPushFingerprint()" in _block(svc, header), (
            f"`{header}` keeps the push fingerprint — the next grant's token would not be uploaded for a day")


def _check_session_end_clears_grant_state(s: dict[str, str]) -> None:  # G13
    svc = s["svc"]
    assert re.search(r"endHoldingsGrantForEndedSession\(\s*endedFingerprint:", _block(svc, _CLEAR)), (
        "clearForEndedSession no longer ends the grant state")
    end = _block(svc, _END)
    for token in ("forcedGrantRefresh?.cancel()", "WidgetHoldingsGrant.requestOnNextRefresh = false",
                  "liveGrantFingerprint = nil", "unusableGrantFingerprint = nil"):
        assert token in end, (
            f"a session end keeps `{token.split('=')[0].strip()}` — a device-global flag that outlives the "
            "session (auth.md §7) mints or forces for the NEXT account")
    assert re.search(r"sweepHoldingsGrantKeys\(\s*keeping:\s*nil\s*,", end), (
        "a session end no longer revokes every key — none belongs to a live session")


def _snake(key: str) -> str:
    """`JSONEncoder.KeyEncodingStrategy.convertToSnakeCase` for the plain keys used here (the
    client's encoder — `APIClient`); an already-snake raw value passes through unchanged."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", key).lower()


def _check_wire_contract(s: dict[str, str]) -> None:  # G14
    from typing import get_args
    from uuid import uuid4

    from pydantic import ValidationError

    from app.schemas.auth import RefreshTokenRequest as ServerRefresh
    from app.schemas.widget import WidgetPushTokenRequest as ServerPush

    ep = s["ep"]
    # The push-token body: the server model is `extra="forbid"`, so ONE extra key is a 422 on
    # every refresh, and a missing required one is a 422 too — the wire keys are exactly its fields.
    body = _block(ep, "nonisolated struct WidgetPushTokenRequestBody: Encodable, Sendable")
    keys = _block(body, "enum CodingKeys: String, CodingKey")
    wire: dict[str, str] = {}
    for line in re.findall(r"\bcase\s+([^\n]+)", keys):
        for part in line.split(","):
            name, _, raw = part.partition("=")
            if name.strip():
                wire[name.strip()] = _snake(raw.strip().strip('"') if raw.strip() else name.strip())
    assert set(wire.values()) == set(ServerPush.model_fields), (
        f"the push-token body sends {sorted(wire.values())}, the server takes exactly "
        f"{sorted(ServerPush.model_fields)} (extra=forbid: anything else is a 422 on every upload)")
    encoded = set(re.findall(r"forKey:\s*\.(\w+)", _block(body, "func encode(to encoder: Encoder) throws")))
    assert encoded == set(wire), f"encode(to:) writes {sorted(encoded)}, CodingKeys declare {sorted(wire)}"
    # The environment: only what the server's Literal admits (anything else 422s), and both of
    # them — a build that always says one would register a sandbox token as production.
    env = _block(s["push"], "static var apnsEnvironment: String")
    said = set(re.findall(r'\breturn\s+"([^"]*)"', env))
    allowed = set(get_args(ServerPush.model_fields["environment"].annotation))
    assert said == allowed, f"apnsEnvironment returns {sorted(said)}, the server admits exactly {sorted(allowed)}"
    # The token: lower-case hex, two characters per byte (so always even), and the client's length
    # gate is the server's — narrower drops real tokens unsent, wider 422s on every refresh.
    sync = _block(s["svc"], _PUSH_SYNC)
    assert re.search(r'info\.token\.map\s*\{\s*String\(format:\s*"%02x"\s*,\s*\$0\s*\)\s*\}\.joined\(\)', sync), (
        "the widget push token is no longer two lower-case hex characters per byte — an unpadded byte "
        "gives an odd length the server refuses")
    gate = re.search(r"if\s+let\s+token\s*,\s*!\(\s*(\d+)\s*\.\.\.\s*(\d+)\s*\)\.contains\(\s*token\.count\s*\)\s*\{", sync)
    assert gate and re.search(r"\breturn\b", _block_at(sync, gate.start())), (
        "the widget push token is uploaded without the server's length gate — an out-of-range token "
        "422s on every refresh instead of being logged once and skipped")
    lo, hi = int(gate.group(1)), int(gate.group(2))

    def server_takes(n: int) -> bool:
        try:
            ServerPush.model_validate({"grant_id": str(uuid4()), "token": "a1" * (n // 2), "environment": "sandbox"})
        except ValidationError:
            return False
        return True

    disagree = [n for n in range(2, 1002, 2) if server_takes(n) != (lo <= n <= hi)]
    assert not disagree, (
        f"the app gates the push token at {lo}...{hi} hex characters; the server disagrees at "
        f"{disagree[:2]}…{disagree[-1:]} characters")
    # The refresh body: the server IGNORES unknown fields, so a misspelt grant flag would never 422
    # — it would silently never mint a grant. Every key the app encodes must be a server field.
    req = _block(ep, "nonisolated struct RefreshTokenRequest: Encodable, Sendable")
    sent = {_snake(n) for n in re.findall(r"\b(?:let|var)\s+(\w+)\s*:", req)}
    assert sent and sent <= set(ServerRefresh.model_fields), (
        f"the refresh body sends {sorted(sent - set(ServerRefresh.model_fields))}, which "
        f"`POST /auth/refresh` does not read (it takes {sorted(ServerRefresh.model_fields)})")


_GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "refresh_flag_wire": _check_refresh_flag_wire,
    "push_token_endpoint": _check_push_token_endpoint,
    "refresh_hands_over_after_arming": _check_refresh_hands_over_after_arming,
    "fresh_install_revokes": _check_fresh_install_revokes_grant_keys,
    "revoke_then_delete": _check_revoke_then_delete,
    "config_throw_keeps_state": _check_config_throw_keeps_state,
    "forced_refresh_foreground_only": _check_forced_refresh_foreground_only,
    "forced_refresh_never_ends_session": _check_forced_refresh_never_ends_session,
    "backoff_steps": _check_backoff_steps,
    "adoption_fences": _check_adoption_fences,
    "sweep_protects": _check_sweep_protects_live_and_adopting,
    "push_token_sync": _check_push_token_sync,
    "session_end_clears": _check_session_end_clears_grant_state,
    "wire_contract": _check_wire_contract,
}


@pytest.mark.parametrize("name", sorted(_GUARDS))
def test_the_guard_holds(name):
    _GUARDS[name](_sources())


# ── Mutations: each must make its guard FAIL ────────────────────────────────────────

def _sub(key: str, pattern: str, new: str) -> Callable[[dict[str, str]], dict[str, str]]:
    def mutate(s: dict[str, str]) -> dict[str, str]:
        out, n = re.subn(pattern, new, s[key], count=1, flags=re.S)
        assert n == 1, f"mutation pattern {pattern!r} not found in {key}"
        return {**s, key: out}
    return mutate


def _chain(*steps: Callable[[dict[str, str]], dict[str, str]]) -> Callable[[dict[str, str]], dict[str, str]]:
    def mutate(s: dict[str, str]) -> dict[str, str]:
        for step in steps:
            s = step(s)
        return s
    return mutate


_MUTATIONS = [
    ("flag-always-sent", "refresh_flag_wire",
     _sub("ep", r"widgetHoldingsGrant:\s*widgetHoldingsGrant\s*\?\s*true\s*:\s*nil", "widgetHoldingsGrant: widgetHoldingsGrant")),
    ("flag-not-optional", "refresh_flag_wire",
     _sub("ep", r"var widgetHoldingsGrant: Bool\? = nil", "var widgetHoldingsGrant: Bool = false")),
    ("push-token-get", "push_token_endpoint",
     _sub("ep", r"\.setMoneyMoveBookmark,\s*\.registerWidgetPushToken:", ".setMoneyMoveBookmark:")),
    ("push-token-null-omitted", "push_token_endpoint",
     _sub("ep", r"try container\.encode\(token, forKey: \.token\)", "try container.encodeIfPresent(token, forKey: .token)")),
    # MOVED, not added: the count stays one, so only the order check can catch it.
    ("adopt-before-save", "refresh_hands_over_after_arming", _chain(
        _sub("auth", r"if let grant = response\.widgetHoldingsGrant \{\n\s*WidgetRefreshService\.shared\.adoptHoldingsGrant\(grant, owner: response\.userId\)\n\s*\} else if",
             "if false {\n        } else if"),
        _sub("auth", r"(WidgetRefreshService\.shared\.discardHoldingsGrant\(grant, owner: response\.userId\)\n\s*\}\n\s*return\n\s*\}\n)",
             r"\1        if let early = response.widgetHoldingsGrant { WidgetRefreshService.shared.adoptHoldingsGrant(early, owner: response.userId) }\n"))),
    ("adopt-from-sign-in", "refresh_hands_over_after_arming",
     _sub("auth", r"(DeviceProofStore\.remember\(response\.deviceToken, for: email\)\n)",
          r"\1        if let g = response.widgetHoldingsGrant { WidgetRefreshService.shared.adoptHoldingsGrant(g, owner: response.userId) }\n")),
    ("adopt-awaited", "refresh_hands_over_after_arming",
     _sub("auth", r"WidgetRefreshService\.shared\.adoptHoldingsGrant\(grant, owner: response\.userId\)\n\s*\} else if",
          "await WidgetRefreshService.shared.adoptHoldingsGrant(grant, owner: response.userId)\n        } else if")),
    ("changed-session-grant-kept", "refresh_hands_over_after_arming",
     _sub("auth", r"WidgetRefreshService\.shared\.discardHoldingsGrant\(grant, owner: response\.userId\)", "_ = grant")),
    ("flag-hardcoded", "refresh_hands_over_after_arming",
     _sub("auth", r"widgetHoldingsGrant: wantsHoldingsGrant\)", "widgetHoldingsGrant: true)")),
    ("missing-grant-unreported", "refresh_hands_over_after_arming",
     _sub("auth", r"WidgetRefreshService\.shared\.noteHoldingsGrantNotIssued\(\)", "_ = wantsHoldingsGrant")),
    ("fresh-install-keeps-keys", "fresh_install_revokes",
     _sub("auth", r"WidgetRefreshService\.revokeHoldingsGrantKeysLeftByAnEarlierInstall\(\)\n", "")),
    ("delete-before-revoke", "revoke_then_delete",
     _sub("svc", r"(for grant in grants \{\n)", r"\1            WidgetHoldingsGrant.delete(fingerprint: grant.fp)\n")),
    ("revoke-on-main", "revoke_then_delete",
     _sub("svc", r"(self\.revokeThenDeleteDetached\(doomed, reason: reason\))",
          "await Self.revokeThenDelete(doomed, reason: reason, log: log)")),
    ("config-throw-retires", "config_throw_keeps_state",
     _sub("svc", r"(grant state kept as is: [^\n]*\n)\s*return\n", r"\1            installed = false\n")),
    ("forced-in-background", "forced_refresh_foreground_only",
     _sub("svc", r"guard UIApplication\.shared\.applicationState == \.active else \{[^}]*\}\n", "")),
    ("forced-ignores-backoff", "forced_refresh_foreground_only",
     _sub("svc", r"if let until = Self\.grantBackoffUntil\(\), until > Date\(\) \{[^}]*\}\n", "")),
    ("forced-from-background-trigger", "forced_refresh_foreground_only",
     _sub("svc", r"(func refreshOnBackground\(\) \{\n)", r'\1        startForcedGrantRefreshIfAllowed(why: "background")\n')),
    ("second-door", "forced_refresh_foreground_only",
     _sub("svc", r"(\n\s*await assessHoldingsGrant\(client: client, epoch: epoch\)\n)",
          r"\1            _ = await client.refreshForWidgetGrant()\n")),
    ("rejected-ends-session", "forced_refresh_never_ends_session",
     _sub("svc", r"(case \.credentialRejected:\n)", r"\1                clearForEndedSession()\n")),
    ("rejected-retried", "forced_refresh_never_ends_session",
     _sub("svc", r"(case \.credentialRejected:\n[^\n]*\n\s*stepGrantBackoff\(\)\n)\s*return\n", r"\1")),
    ("transient-not-retried", "forced_refresh_never_ends_session",
     _sub("svc", r"(did not complete\"\)\n)", r"\1                return\n")),
    ("retry-after-grace", "forced_refresh_never_ends_session",
     _sub("svc", r"forcedGrantRetryOffsets: \[TimeInterval\] = \[20, 90\]", "forcedGrantRetryOffsets: [TimeInterval] = [20, 150]")),
    ("assertion-leaks", "forced_refresh_never_ends_session",
     _sub("svc", r"(the forced refresh ran out of background time — ending the assertion\"\)\n\s*guard assertion != \.invalid else \{ return \}\n)\s*app\.endBackgroundTask\(assertion\)\n", r"\1")),
    ("backoff-shortened", "backoff_steps",
     _sub("svc", r"\[10 \* 60, 60 \* 60, 6 \* 60 \* 60, 24 \* 60 \* 60\]", "[10 * 60, 60 * 60, 60 * 60, 60 * 60]")),
    ("adoption-keeps-backoff", "backoff_steps",
     _sub("svc", r"(WidgetHoldingsGrant\.requestOnNextRefresh = false\n\s*)Self\.resetGrantBackoff\(\)\n(\s*WidgetSnapshotStore\.reloadTimelines)",
          r"\1\2")),
    ("adopt-any-owner", "adoption_fences",
     _sub("svc", r"Self\.subject\(of: bearer\)\?\.lowercased\(\) == grant\.userId\.lowercased\(\)", "true")),
    ("adopt-any-session", "adoption_fences",
     _sub("svc", r"guard let sid = Self\.sessionId\(of: bearer\), sid\.lowercased\(\) == grant\.sessionId\.lowercased\(\) else",
          "guard true else")),
    ("marker-unfenced", "adoption_fences",
     _sub("svc", r"(\n\s*)guard epoch == sessionEpoch, sessionOpen else \{\n(\s*log\.info\(\"widget holdings grant [^\n]*the session ended during the Keychain write)",
          r"\1if !(epoch == sessionEpoch && sessionOpen) {\n\2")),
    ("marker-before-store", "adoption_fences",
     _sub("svc", r"(let failure = await Task\.detached\(priority: \.utility\) \{ \(\) -> String\? in\n)",
          r"WidgetHoldingsGrant.setMarker(grant.fp)\n        \1")),
    ("sweep-before-marker", "adoption_fences",
     _sub("svc", r"(\n\s*WidgetHoldingsGrant\.setMarker\(grant\.fp\)\n)",
          r"\n        sweepHoldingsGrantKeys(keeping: grant.fp, reason: \"app_revoked\")\1")),
    ("sweep-revokes-adopting", "sweep_protects",
     _sub("svc", r"\n\s*&& !self\.adoptingGrantFingerprints\.contains\(key\.fp\)", "")),
    ("push-ungated", "push_token_sync",
     _sub("svc", r"guard #available\(iOS 26\.0, \*\) else \{ return \}\n", "")),
    ("push-fp-on-refusal", "push_token_sync",
     _sub("svc", r"(\n\s*if response\.stored \{\n)", r"\n            defaults.set(fingerprint, forKey: Self.pushFingerprintKey)\1")),
    ("unusable-ignored", "push_token_sync",
     _sub("svc", r"if response\.grantUsable == false \{\n\s*grantCalledUnusable\(grant\)\n\s*\}", "_ = response.grantUsable")),
    ("push-env-private", "push_token_sync",
     _sub("push", r"nonisolated static var apnsEnvironment: String", "private var apnsEnvironment: String")),
    ("end-keeps-flag", "session_end_clears",
     _sub("svc", r"(lastGrantAdoption = nil\n)\s*WidgetHoldingsGrant\.requestOnNextRefresh = false\n", r"\1")),
    ("end-keeps-keys", "session_end_clears",
     _sub("svc", r"(revoking the ended session's keys\"\)\n\s*\}\n)\s*sweepHoldingsGrantKeys\(keeping: nil, reason: \"app_revoked\"\)\n", r"\1")),
    ("push-body-extra-field", "wire_contract",
     _sub("ep", r'(case grantId = "grant_id"\n)', r'\1        case deviceModel = "device_model"\n')),
    ("push-body-key-renamed", "wire_contract",
     _sub("ep", r'case grantId = "grant_id"', 'case grantId = "grant"')),
    ("push-body-field-not-encoded", "wire_contract",
     _sub("ep", r"\n\s*try container\.encode\(environment, forKey: \.environment\)", "")),
    ("apns-env-unknown", "wire_contract",
     _sub("push", r'return "sandbox"', 'return "development"')),
    ("push-token-unpadded-hex", "wire_contract",
     _sub("svc", r'String\(format: "%02x", \$0\)', 'String(format: "%x", $0)')),
    ("push-token-gate-wider", "wire_contract",
     _sub("svc", r"!\(32\.\.\.400\)\.contains\(token\.count\)", "!(32...512).contains(token.count)")),
    ("push-token-gate-narrower", "wire_contract",
     _sub("svc", r"!\(32\.\.\.400\)\.contains\(token\.count\)", "!(64...400).contains(token.count)")),
    ("push-token-gate-not-returning", "wire_contract",
     _sub("svc", r"(outside what the server stores; not uploaded\"\)\n)\s*return\n", r"\1")),
    ("refresh-flag-misspelt", "wire_contract",
     _sub("ep", r"var widgetHoldingsGrant: Bool\? = nil", "var widgetHoldingsGrants: Bool? = nil")),
]


def _verdict(guard: str, src: dict[str, str]) -> str | None:
    """None when `guard` passes on `src`, else the AssertionError it failed with. A `_ScanDrift`
    (an anchor gone) is NOT a verdict and propagates — the caller decides what it means."""
    try:
        _GUARDS[guard](src)
    except AssertionError as exc:
        return str(exc) or "<an assertion with no message>"
    return None


@pytest.mark.parametrize("label,guard,mutate", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_every_guard_kills_its_mutation(label, guard, mutate):
    real = _sources()
    assert _verdict(guard, real) is None  # the real source passes…
    mutated = mutate(real)
    assert mutated != real
    try:
        verdict = _verdict(guard, mutated)  # …and the mutated one fails, for the RIGHT reason
    except _ScanDrift as exc:
        pytest.fail(f"{label}: the mutation only broke an anchor of `{guard}` ({exc}) — that proves "
                    "nothing about the regression it names; aim it at the guarded property")
    assert verdict is not None, f"`{guard}` stayed green on the regression `{label}`"
    assert "this scan has drifted" not in verdict, (
        f"{label}: `{guard}` failed on a drift message ({verdict!r}), not on its property")


def test_an_anchor_only_break_is_not_counted_as_a_kill():
    """CONTROL for the harness above: renaming a header a guard anchors on must surface as drift,
    never as the guard's verdict — else a mutation that breaks only an anchor would 'prove' it."""
    real = _sources()
    renamed = {**real, "svc": real["svc"].replace(_ASSESS, _ASSESS.replace("assessHoldingsGrant", "assessGrantX"), 1)}
    assert renamed != real
    with pytest.raises(_ScanDrift):
        _verdict("config_throw_keeps_state", renamed)
    gone = {**real, "svc": real["svc"].replace("forcedGrantRetryOffsets", "forcedRetryOffsetsX")}
    with pytest.raises(_ScanDrift):
        _verdict("forced_refresh_never_ends_session", gone)
    assert not issubclass(_ScanDrift, AssertionError)


def test_every_guard_has_a_mutation():
    covered = {m[1] for m in _MUTATIONS}
    assert set(_GUARDS) <= covered, f"guards with no mutation: {sorted(set(_GUARDS) - covered)}"


def test_mutation_labels_are_unique():
    labels = [m[0] for m in _MUTATIONS]
    assert len(labels) == len(set(labels))


def test_the_comment_stripper_actually_strips():
    sample = (
        "    // WidgetRefreshService.shared.adoptHoldingsGrant(grant, owner: id)\n"
        "    /* WidgetHoldingsGrant.delete(fingerprint: fp) */\n"
        "    let url = \"https://x\" // trailing currentPushInfo\n"
    )
    out = _strip(sample)
    for token in ("adoptHoldingsGrant", "WidgetHoldingsGrant.delete", "currentPushInfo"):
        assert token not in out
    assert "https://x" in out
