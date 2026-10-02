"""A token refresh that could not COMPLETE must never sign the user out.

The bug (found while fixing TestFlight 1.0 (9) "the Reports screen blinks"): when the 24 h
access token expires, every request 401s and `APIClient` runs ONE single-flight refresh. If
that refresh fails transiently (offline, a 5xx, a 429 from the shared per-IP refresh limiter
on a carrier NAT), the refresher in `iosApp.swift` answers `.transientFailure`, and each of the
four transports (`request<T>`, `request` (void), `downloadData`, `openStream`) used to
RE-THROW THE ORIGINAL 401. That 401 maps to `.tokenExpired` (AUTH_TOKEN_INVALID),
`.sessionEnded` (AUTH_SESSION_EXPIRED) or `.unauthorized` (a bare 401), all `isAuthError`, and
`AppState.handleError` answers each with `signOut()`: the Keychain was wiped for a user whose
refresh token was fine, and a running report ended as "Your session has expired".

The fix: each transient arm throws `APIClient.transientRefreshError()`, a client-made
`.authError(code: "AUTH_UNAVAILABLE", message: …)`. It maps to `.authUnavailable`, which is
retryable, not `isAuthError`, does not trigger a refresh, is a transient poll miss, and is
never answered with a sign-out. Only a refresh ANSWERED with a rejection
(`.credentialRejected`) may still end the session.

What this pins (T1-T9):
  T1  every reference to the single-flight, in any spelling (`self.`-prefixed, a method
      reference), sits in one of the four transports' 401 interceptors (exactly one each) or in
      the ONE allow-listed pre-flight, `refreshArmedTokenIfExpired(for:)`; nothing else calls the
      refresher or reads the in-flight refresh task;
  T1b that pre-flight — when present — can never end a session: plain `async` (no `throws`, no
      return value), no `throw`, never touches the credential or the auth-failure handler,
      writes `authToken` only to adopt a `.refreshed` token, and only LOGS on any other outcome
      (`.transientFailure` / `.credentialRejected` may appear only as the pattern of a
      do-nothing switch arm); every outcome it sees (the single-flight's, or the in-flight
      task's it joins) is discarded or pattern-matched — never stored, passed on or compared,
      which would hand it to a transport without a return value. The test passes with the
      pre-flight absent too;
  T2  each transport's interceptor has exactly one UNCONDITIONAL `.transientFailure` arm, the
      first thing done with the refresh outcome and before the dead-credential path, that
      throws only `Self.transientRefreshError()` and never touches the credential;
  T3  the helper builds AUTH_UNAVAILABLE with honest, non-empty copy (SignInView renders an
      `.authError`'s message verbatim);
  T4  AppError: AUTH_UNAVAILABLE REACHES `.authUnavailable` (no earlier arm shadows it); that
      case is retryable, outside `isAuthError`, and AUTH_UNAVAILABLE is outside
      `triggersTokenRefresh`; `isAuthError` / `isRetryable` are nothing but their switch, and
      `isAuthError`'s true set is EXACTLY `.unauthorized, .tokenExpired, .sessionEnded` (every
      arm a literal `return true` / `return false`) — the refresher's only classification;
  T5  AppState's two funnels never sign out, end the session (any session ender, `clearToken`,
      a nil token) or route to the session logic on `.authUnavailable` — neither in the arm it
      lands in nor in code before/after the switch;
  T6  the status poll rides out `.authUnavailable` but stays terminal on a dead session (a
      transient `.tokenExpired` would spin a zombie monitor whose `.timeout` arm re-adds the
      ended account's report id), and the classifier is nothing but its switch;
  T7  the refresher maps only a genuine auth failure to `.credentialRejected` (one `catch`,
      `.credentialRejected` only from the stored-token guard and the isAuthError ternary), and
      its callee `AuthService.refreshToken()` lets the refresh request's error reach that
      ternary as thrown — no `catch` / `try?` converting or swallowing it, no throw beyond the
      no-refresh-token guard, and no clearing the token or signing out itself;
  T8  the single-flight, where every outcome is created, hands each one back untouched: it
      never reports an auth failure, writes `authToken` only to adopt a refreshed token,
      answers `.credentialRejected` only when no refresher is wired, binds and returns exactly
      `await refresher()` / the task's value (no helper in between), and `setTokenRefresher`
      stores the refresher it was given, unwrapped;
  T9  only the four transports reach `handleUnrecoverableAuthFailure`, only it calls
      `authFailureHandler`, and `authToken` is written only by `setAuthToken`, the
      single-flight, `handleUnrecoverableAuthFailure` and (adopting) the pre-flight — so no
      helper can end the session on the pre-flight's behalf.

There is no XCTest target (testing.md §3), so this reads the Swift source. Comments are
stripped before every assertion (the fix's own comments contain `throw error`, `.tokenExpired`
and `transientRefreshError`), string literals are blanked for the reference scans (T1/T1b/T8/T9)
so a log message naming a symbol is not a call, every check is brace-bounded to the declaration
it means, and each test first asserts it is reading the real declaration (anti-vacuity).

Mutation-tested IN MEMORY on every run (`test_each_mutation_is_killed`): `pathlib.Path.read_text`
is monkeypatched for the one target file, the real Swift is never written, and each mutation
must fail WITH the assertion message that names it. `test_each_compliant_variant_passes` does
the reverse for the pre-flight allow-list: the pre-flight removed, or replaced by other
compliant shapes, must keep every APIClient guard green.
"""
from __future__ import annotations

import pathlib
import re
from typing import Callable

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_CLIENT = _IOS / "Core" / "Services" / "APIClient.swift"
_APP_ERROR = _IOS / "Core" / "Utilities" / "AppError.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_POLL = _IOS / "Core" / "Services" / "TaskPollingManager.swift"
_APP = _IOS / "iosApp.swift"
_AUTH_SERVICE = _IOS / "Core" / "Services" / "AuthService.swift"

_REFRESH_CALL = "await refreshTokenSingleFlight()"
_ARM = "if case .transientFailure = outcome"
_HELPER = "nonisolated private static func transientRefreshError() -> APIError"
_THROW = "throw Self.transientRefreshError()"
_DEAD_PATH = "await handleUnrecoverableAuthFailure("
# Every header stops BEFORE the `{`: `_block` bounds the first `{` after the header, so a
# header that swallowed the brace would bound the wrong (next) block.
_TRANSPORTS = {
    "request<T>": "func request<T: Decodable>(",
    "request(void)": "func request(endpoint: APIEndpoint, allowAuthRetry: Bool = true)",
    "downloadData": "func downloadData(endpoint: APIEndpoint,",
    "openStream": "private func openStream(endpoint: APIEndpoint,",
}
_SF_DECL = "private func refreshTokenSingleFlight()"
_SF_REF = re.compile(r"\brefreshTokenSingleFlight\b")
_HUAF_DECL = "private func handleUnrecoverableAuthFailure("
# The pre-flight is allow-listed BY NAME: `refreshArmedTokenIfExpired(for:)`, one parameter
# labelled `for`. `\b` after the name keeps `refreshArmedTokenIfExpiredNow` out.
_PREFLIGHT_ANY_DECL = re.compile(r"\bfunc\s+refreshArmedTokenIfExpired\b")
_PREFLIGHT_DECL = re.compile(r"func\s+refreshArmedTokenIfExpired\s*\(\s*for\s+\w+\s*:\s*[^,()]+\)")
_PREFLIGHT = "refreshArmedTokenIfExpired(for:)"
_AUTH_TOKEN_WRITE = re.compile(r"\b(?:self\.)?authToken\s*=(?!=)")
_OUTCOME_NAME = re.compile(r"\.(?:refreshed|transientFailure|credentialRejected)\b")
_NOT_REFRESHED = re.compile(r"\.(?:transientFailure|credentialRejected)\b")
# What a "do nothing" branch may contain besides `break` / `return`: an os.Logger call or print.
_LOG_CALL = re.compile(
    r"(?:\b[Ss]elf\.)?\b(?:\w*Log|logger|log)\.(?:debug|info|notice|warning|error|fault|trace|critical|log)\s*\("
    r"|\bprint\s*\(")


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the fix's comments name `transientRefreshError()`, `.tokenExpired`,
    `.authUnavailable` and `signOut()` while explaining the bug, so an un-stripped scan for
    their ABSENCE fails on prose and a scan for their PRESENCE passes on a revert whose comment
    survived. A tail needs leading whitespace, so a `https://` inside a string literal is not cut.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", raw))
    return "\n".join(out)


def _blank_comments(src: str) -> str:
    """The same comments as `_strip_swift_comments`, overwritten with spaces (offsets kept)."""
    src = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), src, flags=re.S)
    out = []
    for raw in src.splitlines(keepends=True):
        line = raw.rstrip("\n")
        if line.strip().startswith("//"):
            line = " " * len(line)
        else:
            m = re.search(r"\s//.*$", line)
            if m:
                line = line[: m.start()] + " " * (len(line) - m.start())
        out.append(line + raw[len(raw.rstrip("\n")):])
    return "".join(out)


def _blank_strings(src: str) -> str:
    """Overwrite every one-line string literal's contents with spaces (offsets kept), so a log
    message that names a symbol is not a reference to it, and a brace in a message cannot
    unbalance a block."""
    return re.sub(r'"(?:[^"\\\n]|\\.)*"', lambda m: '"' + " " * (len(m.group()) - 2) + '"', src)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _scan(path: pathlib.Path) -> str:
    """`_code` with string literals blanked — for the reference scans (T1/T1b/T8/T9)."""
    return _blank_strings(_code(path))


def _balanced_end(src: str, start: int, open_: str = "{", close: str = "}") -> int:
    """Index of the `close` matching the `open_` at `start`."""
    assert src[start] == open_, f"expected `{open_}` at offset {start}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError(f"unbalanced `{open_}{close}` from offset {start}")


def _span(src: str, header: str | re.Pattern, open_: str = "{", close: str = "}") -> tuple[int, int]:
    """`[start, end)` of the balanced `open_`…`close` body after the ONLY `header` (a literal
    prefix, or a compiled pattern)."""
    if isinstance(header, re.Pattern):
        hits = list(header.finditer(src))
        assert len(hits) == 1, f"expected exactly one match of `{header.pattern}`, found {len(hits)}"
        after = hits[0].end()
    else:
        assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
        after = src.index(header) + len(header)
    start = src.index(open_, after)
    return start, _balanced_end(src, start, open_, close) + 1


def _block(src: str, header: str | re.Pattern, open_: str = "{", close: str = "}") -> str:
    """The balanced `open_`…`close` body that follows the ONLY `header` (a literal prefix)."""
    start, end = _span(src, header, open_, close)
    return src[start:end]


def _squash(text: str) -> str:
    """`text` on one line, for an assertion message."""
    return re.sub(r"\s+", " ", text).strip()


def _line_at(src: str, pos: int) -> str:
    return src[src.rfind("\n", 0, pos) + 1: (src.find("\n", pos) + 1 or len(src) + 1) - 1].strip()


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in spans)


def _arms(switch_body: str) -> list[tuple[str, str]]:
    """Split a brace-bounded `switch` body into `(pattern, arm body)` pairs, in source order.

    The pattern is the text between `case` and its `:` (it may span lines), or `"default"`.
    Only for the flat switches below — a nested switch would be split at its inner `case`s.
    """
    inner = switch_body[1:-1]
    heads = list(re.finditer(r"^\s*(case\b[^:]*|default)\s*:", inner, flags=re.M))
    arms = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(inner)
        pattern = m.group(1).strip()
        arms.append(("default" if pattern == "default" else pattern[len("case"):].strip(), inner[m.end(): end]))
    return arms


def _arm_for(arms: list[tuple[str, str]], case: str) -> tuple[str, str]:
    """The arm a value of `case` actually lands in: the first `case` naming it, else `default`."""
    for pattern, body in arms:
        if pattern != "default" and re.search(re.escape(case) + r"\b", pattern):
            return pattern, body
    for pattern, body in arms:
        if pattern == "default":
            return pattern, body
    raise AssertionError(f"no arm reaches `{case}` and there is no `default` — the switch is not exhaustive")


def _returns(body: str, value: str) -> bool:
    return re.fullmatch(rf"\s*return {value}\s*", body) is not None


def _only_switch(block: str, header: str, what: str) -> None:
    """`block` (a `{…}` body) must be its `header` switch and nothing else — code before or after
    the switch would decide the answer without the arms the guards read."""
    rest = block.replace(_block(block, header), "", 1).replace(header, "", 1)
    assert re.fullmatch(r"\{\s*\}", rest), (
        f"{what}: code outside its `{header}` decides the answer before the arms this guard reads "
        f"(got {_squash(rest)[:120]!r})")


# ── T1 / T1b / T2 / T3 / T8 / T9: APIClient ─────────────────────────────────


def _interceptor_span(scan: str, name: str, header: str) -> tuple[int, int]:
    """`[start, end)` of `name`'s 401 interceptor: the `if <error>.triggersTokenRefresh … { }`."""
    start, end = _span(scan, header)
    hits = list(re.finditer(r"\bif\s+\w+\.triggersTokenRefresh\b", scan[start:end]))
    assert len(hits) == 1, (
        f"{name}: expected one 401 interceptor (`if <error>.triggersTokenRefresh`), found {len(hits)}")
    brace = scan.index("{", start + hits[0].end())
    return brace, _balanced_end(scan, brace) + 1


def _preflight(scan: str) -> tuple[str, tuple[int, int]] | None:
    """`(signature, body span)` of the allow-listed `refreshArmedTokenIfExpired(for:)`, or None
    when it is absent. The signature is the text between `)` and the body's `{`."""
    decls = list(_PREFLIGHT_ANY_DECL.finditer(scan))
    assert len(decls) <= 1, (
        f"`refreshArmedTokenIfExpired` is declared {len(decls)}× — the allow-list names exactly one "
        f"method, `{_PREFLIGHT}`; an overload would share its exemption")
    if not decls:
        return None
    m = _PREFLIGHT_DECL.match(scan, decls[0].start())
    assert m, (
        f"the allow-listed pre-flight must be exactly `{_PREFLIGHT}` (one parameter, labelled "
        f"`for`), got `{_line_at(scan, decls[0].start())}` — a renamed method is not exempt")
    brace = scan.index("{", m.end())
    return scan[m.end(): brace], (brace, _balanced_end(scan, brace) + 1)


def test_every_refresh_call_site_is_one_of_the_four_guarded_transports():  # T1
    """A fifth transport that refreshes without the transient arm would bring the bug back.

    Counts every REFERENCE to the single-flight — `await refreshTokenSingleFlight()`,
    `self.refreshTokenSingleFlight()`, a method reference — not one literal spelling. The only
    exemption beyond the four interceptors is the pre-flight, by name, whose own body T1b pins.
    """
    scan = _scan(_CLIENT)
    sf = _span(scan, _SF_DECL)
    decl_pos = scan.index(_SF_DECL) + len("private func ")
    refs = [m.start() for m in _SF_REF.finditer(scan) if m.start() != decl_pos]
    interceptors = {name: _interceptor_span(scan, name, header) for name, header in _TRANSPORTS.items()}
    for name, (start, end) in interceptors.items():
        k = sum(start <= p < end for p in refs)
        assert k == 1, f"{name}: expected exactly one refresh call in its 401 interceptor, found {k}"
    pre = _preflight(scan)
    allowed = list(interceptors.values()) + ([pre[1]] if pre else [])
    outside = [_line_at(scan, p) for p in refs if not _inside(p, allowed)]
    assert not outside, (
        "a refresh call site outside the four guarded transports' 401 interceptors and the "
        f"allow-listed `{_PREFLIGHT}` ({len(outside)}: {outside}) — a refresh nobody guarded "
        "against the transient-failure sign-out")

    # The refresher itself: only the single-flight calls it (the setter stores it), so no
    # transport can run a refresh — and interpret its outcome — behind the single-flight's back.
    setter = _span(scan, "func setTokenRefresher(")
    decl = "private var tokenRefresher:"
    assert scan.count(decl) == 1, "the refresher property moved — re-derive this guard"
    decl_at = scan.index(decl) + len("private var ")
    stray = [_line_at(scan, m.start()) for m in re.finditer(r"\btokenRefresher\b", scan)
             if m.start() != decl_at and not _inside(m.start(), [sf, setter])
             and not re.match(r"tokenRefresher\s*!=\s*nil\b", scan[m.start():])]
    assert not stray, (
        "the refresher is reached outside refreshTokenSingleFlight — only `tokenRefresher != nil` "
        f"checks may read it elsewhere: {stray}")

    # The in-flight refresh task is a second door to an outcome: only the single-flight and the
    # pre-flight (which discards it) may read it.
    decl = "private var refreshInFlight:"
    assert scan.count(decl) == 1, "the in-flight task property moved — re-derive this guard"
    decl_at = scan.index(decl) + len("private var ")
    stray = [_line_at(scan, m.start()) for m in re.finditer(r"\brefreshInFlight\b", scan)
             if m.start() != decl_at and not _inside(m.start(), [sf] + ([pre[1]] if pre else []))]
    assert not stray, (
        f"the in-flight refresh task is read outside refreshTokenSingleFlight and `{_PREFLIGHT}` — "
        f"a second place that interprets a refresh outcome: {stray}")


def _non_refreshed_branches(body: str) -> list[tuple[str, str]]:
    """Every branch of `body` that runs on an outcome other than `.refreshed`: the non-
    `.refreshed` arms of a switch over the outcome, and the `else` of `if case .refreshed` /
    `guard case .refreshed`."""
    out = []
    for m in re.finditer(r"\bswitch\b[^{]*\{", body):
        brace = m.end() - 1
        arms = _arms(body[brace: _balanced_end(body, brace) + 1])
        if not any(p != "default" and _OUTCOME_NAME.search(p) for p, _ in arms):
            continue
        for pattern, arm in arms:
            refreshed_only = (pattern != "default" and re.match(r"(?:let\s+)?\.refreshed\b", pattern)
                              and not _NOT_REFRESHED.search(pattern))
            if not refreshed_only:
                out.append((f"arm `{pattern}`", arm))
    for m in re.finditer(r"\bif\s+case\s+(?:let\s+)?\.refreshed\b[^{]*\{", body):
        end = _balanced_end(body, m.end() - 1)
        e = re.match(r"\s*else\b", body[end + 1:])
        if e:
            brace = body.index("{", end + 1 + e.end())
            out.append(("the `else` of `if case .refreshed`", body[end + 1 + e.end(): _balanced_end(body, brace) + 1]))
    for m in re.finditer(r"\bguard\s+case\s+(?:let\s+)?\.refreshed\b[^{]*?\belse\s*\{", body):
        out.append(("the `else` of `guard case .refreshed`", body[m.end() - 1: _balanced_end(body, m.end() - 1) + 1]))
    return out


def _inert(branch: str) -> bool:
    """True when `branch` only logs, `break`s or `return`s (string literals already blanked)."""
    s = branch.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    while (m := _LOG_CALL.search(s)) is not None:
        s = s[: m.start()] + s[_balanced_end(s, m.end() - 1, "(", ")") + 1:]
    return not re.sub(r"\b(?:break|return)\b", "", s).strip()


# How the pre-flight may consume an outcome expression (`await refreshTokenSingleFlight()`, or
# `await <in-flight task>.value`): discard it, bind it to a `let`, or match on it directly.
_CONSUMER = re.compile(
    r"(?:\b_\s*=|\blet\s+(?P<bind>\w+)\s*(?::\s*TokenRefreshOutcome\s*)?=|\bswitch"
    r"|(?:\bif|\bguard|,)\s*case\b[^{}\n:;=]*=)\s*$")
# What a bound outcome may be used as: the subject of a `switch` or of an `if/guard case … =`.
_SUBJECT = re.compile(r"(?:\bswitch|(?:\bif|\bguard|,)\s*case\b[^{}\n:;=]*=)\s*$")


def _escaped_outcomes(body: str) -> list[str]:
    """Lines of the pre-flight's `body` (strings blanked) where a refresh outcome — from the
    single-flight or from the in-flight task it joins — is anything but discarded or the subject
    of a pattern match: stored in a property, passed to a call, compared, or an un-awaited
    reference to either source."""
    bad: list[str] = []

    def before(pos: int) -> str:
        return body[body.rfind("\n", 0, pos) + 1: pos]

    def consume(await_at: int, end: int) -> None:
        """Check the outcome expression `body[await_at:end]` and every use of its binding."""
        m = _CONSUMER.search(before(await_at))
        if m is None:
            bad.append(_line_at(body, await_at))
            return
        if m.group("bind"):
            for u in re.finditer(rf"(?<![.\w]){re.escape(m.group('bind'))}\b", body[end:]):
                at = end + u.start()
                if not (_SUBJECT.search(before(at))
                        and re.match(r"\s*(?:\{|,|else\b)", body[at + len(m.group("bind")):])):
                    bad.append(_line_at(body, at))

    def awaited(pos: int) -> int | None:
        """Offset of the `await` that directly precedes `pos` on its line, else None."""
        a = re.search(r"\bawait\s+(?:self\.)?$", before(pos))
        return None if a is None else pos - len(before(pos)) + a.start()

    for m in _SF_REF.finditer(body):
        call = re.match(r"\s*\(\s*\)", body[m.end():])
        at = awaited(m.start())
        if call is None or at is None:
            bad.append(_line_at(body, m.start()))
        else:
            consume(at, m.end() + call.end())

    tasks: list[tuple[str, int]] = []
    for m in re.finditer(r"\brefreshInFlight\b", body):
        rest = body[m.end():]
        bound = re.search(r"\b(?:if|guard)\s+let\s+(?:(\w+)\s*=\s*(?:self\.)?)?$", before(m.start()))
        if bound:
            if bound.group(1):
                tasks.append((bound.group(1), m.end()))
            continue
        if re.match(r"\s*[!=]=\s*nil\b", rest):
            continue
        value = re.match(r"\s*[?!]?\s*\.\s*value\b", rest)
        at = awaited(m.start())
        if value is None or at is None:
            bad.append(_line_at(body, m.start()))
        else:
            consume(at, m.end() + value.end())
    for name, after in tasks:
        for u in re.finditer(rf"(?<![.\w]){re.escape(name)}\b", body[after:]):
            pos = after + u.start()
            value = re.match(r"\s*\.\s*value\b", body[pos + len(name):])
            at = awaited(pos)
            if value is None or at is None:
                bad.append(_line_at(body, pos))
            else:
                consume(at, pos + len(name) + value.end())
    return bad


def test_the_pre_flight_refresh_can_never_end_a_session():  # T1b
    """The one refresh call site outside the 401 interceptors must never interpret a failed
    refresh. Absent, there is nothing to check (T1 still forbids any other call site)."""
    scan = _scan(_CLIENT)
    pre = _preflight(scan)
    if pre is None:
        return
    signature, (start, end) = pre
    body = scan[start:end]
    assert not re.search(r"\bthrow\b", body), (
        f"`{_PREFLIGHT}` must never throw — a thrown error reaches AppState.handleError, and the "
        "401 interceptor alone may turn a failed refresh into an error")
    for touch in ("handleUnrecoverableAuthFailure", "authFailureHandler", "setAuthToken", "Keychain"):
        assert touch not in body, (
            f"`{_PREFLIGHT}` touches the session (`{touch}`) — a pre-flight refresh that did not "
            "complete says nothing about the credential")
    adopted = set(re.findall(r"\.refreshed\(let (\w+)\)", body))
    for m in _AUTH_TOKEN_WRITE.finditer(body):
        rhs = re.match(r"\s*([^\n;]*)", body[m.end():]).group(1).strip()
        assert rhs in adopted, (
            f"`{_PREFLIGHT}` writes `authToken = {rhs}` — it may only adopt a token bound by "
            "`.refreshed(let …)`, never clear or replace the credential")
    outside_heads = re.sub(r"^\s*(case\b[^:]*|default)\s*:", "", body, flags=re.M)
    for name in (".credentialRejected", ".transientFailure"):
        assert not re.search(re.escape(name) + r"\b", outside_heads), (
            f"`{_PREFLIGHT}` names `{name}` outside a switch arm's pattern — it may never act on a "
            "failed refresh; the 401 interceptor alone interprets one")
    for where, branch in _non_refreshed_branches(body):
        assert _inert(branch), (
            f"`{_PREFLIGHT}` acts on a non-.refreshed outcome ({where}: "
            f"{_squash(branch)[:100]!r}) — it may only log there; the 401 "
            "interceptor alone interprets a failed refresh")
    assert re.fullmatch(r"\s*async\s*", signature), (
        f"`{_PREFLIGHT}` must be plain `async` (got `{signature.strip()}`) — `throws` or a return "
        "value hands the refresh outcome to a caller that can end the session with it")
    # The same hand-off without a return value: the outcome stored in a property, passed to a
    # call, or the task / single-flight referenced un-awaited, for a transport to act on later.
    escaped = _escaped_outcomes(body)
    assert not escaped, (
        f"`{_PREFLIGHT}` lets a refresh outcome out of a pattern match ({escaped}) — stored or "
        "passed on, it hands a failed refresh to code that can end the session; the pre-flight "
        "may only discard it (`_ =`) or match on it (`switch` / `if case` / `guard case`)")


def test_a_transient_refresh_surfaces_auth_unavailable_never_the_original_401():  # T2
    code = _code(_CLIENT)
    for name, header in _TRANSPORTS.items():
        body = _block(code, header)
        # Anti-vacuity: this is the 401 interceptor, not a same-named stub.
        assert "triggersTokenRefresh" in body and _REFRESH_CALL in body and _DEAD_PATH in body, (
            f"{name}: not the 401 interceptor (no triggersTokenRefresh / refresh / dead-credential path)")
        assert body.count(_ARM) == 1, (
            f"{name}: expected exactly one `.transientFailure` arm — without it a rate-limited "
            "refresh falls through to handleUnrecoverableAuthFailure and ENDS the session")
        assert re.search(re.escape(_ARM) + r"\s*\{", body), (
            f"{name}: the `.transientFailure` arm is conditional (`{_ARM}, …`) — when the extra "
            "condition is false a transient outcome falls through to the dead-credential path")
        assert body.index(_ARM) < body.index(_DEAD_PATH), (
            f"{name}: the `.transientFailure` arm must run before handleUnrecoverableAuthFailure — "
            "otherwise a refresh that merely could not complete is treated as a dead credential")
        assert re.search(r"let outcome = await (?:self\.)?refreshTokenSingleFlight\(\)\s*"
                         + re.escape(_ARM) + r"\s*\{", body), (
            f"{name}: the `.transientFailure` arm is not the first thing done with the refresh "
            "outcome — code between the refresh and the arm runs on a transient failure too")
        arm = _block(body, _ARM)
        throws = [t.strip() for t in re.findall(r"\bthrow\b[^\n}]*", arm)]
        assert throws == [_THROW], (
            f"{name}: a transient refresh failure re-throws the original 401 (got {throws}) — "
            ".tokenExpired/.sessionEnded/.unauthorized are answered with signOut()")
        for touch in ("handleUnrecoverableAuthFailure", "authFailureHandler", "authToken",
                      "setAuthToken", "Keychain"):
            assert touch not in arm, (
                f"{name}: the transient arm touches the credential (`{touch}`) — a refresh that "
                "could not complete says nothing about it")


def test_the_transient_error_is_auth_unavailable_with_honest_copy():  # T3
    body = _block(_code(_CLIENT), _HELPER)
    m = re.fullmatch(
        r'\{\s*(?:return\s+)?(?:APIError)?\.authError\(\s*code:\s*"AUTH_UNAVAILABLE"\s*,'
        r'\s*message:\s*"([^"\\]*)"\s*\)\s*\}',
        body)
    assert m, (
        'transientRefreshError must build `.authError(code: "AUTH_UNAVAILABLE", message: …)` '
        f"and nothing else, got {body!r}")
    copy = m.group(1)
    assert copy.strip(), (
        "transientRefreshError needs a non-empty message (SignInView.friendlyError renders an "
        ".authError's message verbatim)")
    for lie in ("expired", "sign in", "signed out", "password", "session has ended"):
        assert lie not in copy.lower(), (
            f"the transient copy must not tell the user their session ended (`{lie}`) — the "
            "session is kept")


def test_the_single_flight_hands_back_every_outcome_untouched():  # T8
    """Every outcome is CREATED here, so a change here signs the user out before any transport's
    transient arm runs."""
    scan = _scan(_CLIENT)
    sf = _block(scan, _SF_DECL)
    assert "await refresher()" in sf and "refreshInFlight = task" in sf, (
        "not the single-flight (no `await refresher()` / `refreshInFlight = task`) — re-derive")
    for touch in ("authFailureHandler", "handleUnrecoverableAuthFailure", "setAuthToken", "Keychain", "throw"):
        assert not re.search(rf"\b{touch}\b", sf), (
            f"refreshTokenSingleFlight touches the session (`{touch}`) — every refresh outcome is "
            "created there, so a transient one would end the session before any transport's arm runs")
    writes = _AUTH_TOKEN_WRITE.findall(sf)
    assert len(writes) == 1 and re.search(
            r"if case \.refreshed\(let (\w+)\) = outcome \{\s*self\.authToken = \1\s*\}", sf), (
        f"refreshTokenSingleFlight writes authToken other than adopting a refreshed token "
        f"({len(writes)} writes)")
    rejected = re.findall(r"\.credentialRejected\b", sf)
    assert len(rejected) == 1 and re.search(
            r"guard let refresher = tokenRefresher else \{\s*return \.credentialRejected\s*\}", sf), (
        "refreshTokenSingleFlight answers .credentialRejected other than when no refresher is "
        f"wired ({len(rejected)}×) — it would turn a transient outcome into a sign-out")

    # "Untouched" end to end: the refresher's value reaches every caller with no function in
    # between. The checks above only see the session touched INSIDE this block; a helper
    # declared anywhere else (`escalateRepeatedTransient(outcome)`, an outcome extension, a
    # wrapping setter) could still turn a run of `.transientFailure`s into `.credentialRejected`
    # — the NAT-429 sign-out back again, with every transport's transient arm intact.
    rets = [r.strip() for r in re.findall(r"\breturn\b[^\n}]*", sf)]
    assert rets == ["return await inFlight.value", "return .credentialRejected",
                    "return outcome", "return outcome"], (
        f"refreshTokenSingleFlight returns something other than the refresher's own outcome (got "
        f"{rets}) — the follower, the task and the leader must hand back exactly what the "
        "refresher produced")
    lets = [r.strip() for r in re.findall(r"\blet outcome\s*=\s*([^\n]*)", sf)]
    assert lets == ["await refresher()", "await task.value"], (
        f"refreshTokenSingleFlight transforms the refresh outcome before handing it back (got "
        f"{lets}) — `outcome` must be bound to exactly `await refresher()` in the task and "
        "`await task.value` in the leader, never passed through a helper")
    setter = _block(scan, "func setTokenRefresher(")
    assert re.fullmatch(r"\{\s*self\.tokenRefresher = refresher\s*\}", setter), (
        f"setTokenRefresher stores something other than the refresher it was given "
        f"({_squash(setter)[:120]!r}) — a wrapper there rewrites every outcome before the "
        "single-flight sees it")


def test_only_the_401_interceptors_can_end_the_session():  # T9
    """The session ends through ONE door — handleUnrecoverableAuthFailure, called only by the
    four transports — so no helper can end it on the pre-flight's (or anyone's) behalf."""
    scan = _scan(_CLIENT)
    transports = [_span(scan, header) for header in _TRANSPORTS.values()]
    huaf = _span(scan, _HUAF_DECL)
    decl_at = scan.index(_HUAF_DECL) + len("private func ")
    stray = [_line_at(scan, m.start()) for m in re.finditer(r"\bhandleUnrecoverableAuthFailure\b", scan)
             if m.start() != decl_at and not _inside(m.start(), transports)]
    assert not stray, (
        f"handleUnrecoverableAuthFailure is reached from outside the four guarded transports: {stray}")

    decl = "private var authFailureHandler:"
    assert scan.count(decl) == 1, "the auth-failure handler property moved — re-derive this guard"
    decl_at = scan.index(decl) + len("private var ")
    setter = _span(scan, "func setAuthFailureHandler(")
    stray = [_line_at(scan, m.start()) for m in re.finditer(r"\bauthFailureHandler\b", scan)
             if m.start() != decl_at and not _inside(m.start(), [setter, huaf])]
    assert not stray, f"authFailureHandler is called from outside handleUnrecoverableAuthFailure: {stray}"

    pre = _preflight(scan)
    writers = [_span(scan, "func setAuthToken("), _span(scan, _SF_DECL), huaf] + ([pre[1]] if pre else [])
    stray = [_line_at(scan, m.start()) for m in _AUTH_TOKEN_WRITE.finditer(scan)
             if not _inside(m.start(), writers)]
    assert not stray, (
        "authToken is written outside setAuthToken / the single-flight / handleUnrecoverableAuthFailure "
        f"/ `{_PREFLIGHT}`: {stray}")


# ── T4: AppError mapping ─────────────────────────────────────────────────────


def test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session():  # T4
    err = _code(_APP_ERROR)

    mapping = _block(err, "private static func mapAPIError(")
    assert "case .authError(let code, let message):" in mapping and "case .notFound:" in mapping, (
        "mapAPIError no longer has the `.authError` arm — this guard is reading the wrong switch")
    auth = mapping[mapping.index("case .authError(let code, let message):"): mapping.index("case .notFound:")]
    assert re.search(r'case "AUTH_UNAVAILABLE":\s*return \.authUnavailable\(message: message\)', auth), (
        "AUTH_UNAVAILABLE must map to .authUnavailable(message:) — the client-made "
        "transientRefreshError depends on it to keep the session")
    # Swift does not diagnose a duplicate string pattern, and the FIRST matching arm wins, so the
    # explicit arm above proves nothing if an earlier one also lists the code. The opening quote
    # only: `_arm_for` appends `\b`, which never matches after a closing quote.
    pattern, body = _arm_for(_arms(_block(auth, "switch code")), '"AUTH_UNAVAILABLE')
    assert _returns(body, r"\.authUnavailable\(message: message\)"), (
        f"AUTH_UNAVAILABLE is shadowed: it reaches arm `{pattern}` first, not "
        ".authUnavailable(message:) — a transient refresh would end the session")

    is_auth = _block(err, "var isAuthError: Bool")
    assert "switch self" in is_auth, "isAuthError is no longer a switch — re-derive this guard"
    arms = _arms(_block(is_auth, "switch self"))
    assert _returns(_arm_for(arms, ".tokenExpired")[1], "true"), (
        "isAuthError no longer returns true for .tokenExpired — the refresher's rejection "
        "detection (iosApp.swift) depends on it; this guard is reading the wrong switch")
    pattern, body = _arm_for(arms, ".authUnavailable")
    assert _returns(body, "false"), (
        f"`.authUnavailable` joined isAuthError (arm `{pattern}`) — a transient refresh would "
        "clear the Keychain and sign the user out")
    _only_switch(is_auth, "switch self", "isAuthError")
    # The EXACT true set. The refresher's `isAuthError ? .credentialRejected : .transientFailure`
    # is the ONLY classification of a failed refresh, so a case added here (`.rateLimited`,
    # `.serverError`, `.forbidden` re-added) turns a 429/5xx/403 refresh into a sign-out. Every
    # arm must be a literal answer: a computed one (`return retryAfter > 300`) is a hidden true.
    true_cases: set[str] = set()
    for pattern, body in arms:
        if _returns(body, "true"):
            true_cases |= {"default"} if pattern == "default" else set(re.findall(r"\.\w+", pattern))
        else:
            assert _returns(body, "false"), (
                f"isAuthError arm `{pattern}` decides other than `return true` / `return false` "
                f"({_squash(body)[:80]!r}) — a computed answer widens the true set the refresher "
                "rejects on")
    assert true_cases == {".unauthorized", ".tokenExpired", ".sessionEnded"}, (
        f"isAuthError's true set is {sorted(true_cases)}, not exactly the three genuine auth "
        "failures — the refresher answers .credentialRejected on isAuthError, so a widened set "
        "turns a 429/5xx/403 refresh into a sign-out")

    refresh = _block(err, "var triggersTokenRefresh: Bool")
    assert '"AUTH_TOKEN_INVALID"' in refresh, "not triggersTokenRefresh (AUTH_TOKEN_INVALID is gone)"
    assert "AUTH_UNAVAILABLE" not in refresh, (
        "AUTH_UNAVAILABLE must not trigger a refresh — the client-made error would loop the "
        "interceptor into another refresh")

    retry = _block(err, "var isRetryable: Bool")
    assert "switch self" in retry, "isRetryable is no longer a switch — re-derive this guard"
    pattern, body = _arm_for(_arms(_block(retry, "switch self")), ".authUnavailable")
    assert _returns(body, "true"), (
        f"`.authUnavailable` must stay retryable (it lands in arm `{pattern}`) — the user is "
        "offered Try Again, not sign-in")
    _only_switch(retry, "switch self", "isRetryable")


# ── T5: AppState funnels ─────────────────────────────────────────────────────

# What ends a session in AppState. Code OUTSIDE a funnel's switch must reach none of them.
_SESSION_ENDERS = ("signOut(", "endSessionForDeadCredential(", "discardDataForEndedSession(", "clearToken(")
# Disarming the credential directly: `apiClient.setAuthToken(nil)` or a nil `authToken` write.
_NIL_TOKEN_WRITE = re.compile(r"\bsetAuthToken\(\s*nil\s*\)|\bauthToken\s*=\s*nil\b")


def _session_enders_in(code: str) -> list[str]:
    """Every session ender `code` reaches — the named enders and a nil-token write."""
    return [e for e in _SESSION_ENDERS if e in code] + [m.group() for m in _NIL_TOKEN_WRITE.finditer(code)]


def test_app_state_never_signs_out_on_auth_unavailable():  # T5
    st = _code(_APP_STATE)

    handle = _block(st, "func handleError(_ error: Error)")
    assert "signOut()" in handle and ".tokenExpired" in handle, "not the global sink (handleError)"
    assert "switch appError" in handle, "handleError no longer switches on appError — re-derive"
    arms = _arms(_block(handle, "switch appError"))
    assert "signOut()" in _arm_for(arms, ".sessionEnded")[1], (
        "handleError's .sessionEnded arm no longer signs out — the arm parser is reading the wrong switch")
    pattern, body = _arm_for(arms, ".authUnavailable")
    assert "signOut()" not in body, (
        f"AppState.handleError would sign out on .authUnavailable (arm `{pattern}`) — that is "
        "exactly the transient-refresh sign-out this fix removed")
    enders = _session_enders_in(body)
    assert not enders, (
        f"AppState.handleError ends the session on .authUnavailable (arm `{pattern}`: {enders}) — "
        "a refresh that could not complete says nothing about the credential")
    outside = handle.replace(_block(handle, "switch appError"), "", 1)
    for ender in _SESSION_ENDERS:
        assert ender not in outside, (
            f"AppState.handleError ends the session outside its switch (`{ender}`) — an early "
            "`if case .authUnavailable` there signs out before the arms this guard reads")

    report = _block(st, "func reportMutationFailure(")
    assert "handleError(error)" in report and "showToast(" in report, "not the mutation funnel"
    assert "switch appError" in report, "reportMutationFailure no longer switches on appError — re-derive"
    pattern, body = _arm_for(_arms(_block(report, "switch appError")), ".authUnavailable")
    assert "handleError(" not in body, (
        f"reportMutationFailure routes .authUnavailable to the session logic (arm `{pattern}`)")
    assert "showToast(" in body, (
        f"reportMutationFailure no longer tells the user a .authUnavailable mutation failed "
        f"(arm `{pattern}`) — auth.md §6: no silent failure")
    enders = _session_enders_in(body)
    assert not enders, (
        f"reportMutationFailure ends the session on .authUnavailable (arm `{pattern}`: {enders}) — "
        "a refresh that could not complete says nothing about the credential")
    outside = report.replace(_block(report, "switch appError"), "", 1)
    for ender in ("handleError(",) + _SESSION_ENDERS:
        assert ender not in outside, (
            f"reportMutationFailure reaches the session logic outside its switch (`{ender}`) — "
            "an early `if case .authUnavailable` there bypasses the arms this guard reads")


# ── T6: the status poll ──────────────────────────────────────────────────────


def test_the_poll_rides_out_auth_unavailable_but_stops_on_a_dead_session():  # T6
    poll = _block(_code(_POLL), "nonisolated static func isTransientPollFailure(_ error: AppError) -> Bool")
    assert "switch error" in poll, "isTransientPollFailure is no longer a switch — re-derive"
    arms = _arms(_block(poll, "switch error"))
    assert _returns(_arm_for(arms, ".noConnection")[1], "true"), "not the classifier (.noConnection is not transient)"
    pattern, body = _arm_for(arms, ".authUnavailable")
    assert _returns(body, "true"), (
        f"`.authUnavailable` must be a transient poll miss (it lands in arm `{pattern}`) — a "
        "transient refresh would end a running report as failed")
    for dead in (".tokenExpired", ".sessionEnded", ".unauthorized"):
        pattern, body = _arm_for(arms, dead)
        assert _returns(body, "false"), (
            f"`{dead}` is a transient poll failure (arm `{pattern}`) — zombie monitor to "
            "maxPollDuration whose .timeout arm re-adds the ended account's report id")
    _only_switch(poll, "switch error", "isTransientPollFailure")


# ── T7: the refresher's classification ───────────────────────────────────────


def test_the_refresher_rejects_only_on_a_genuine_auth_failure():  # T7
    closure = _block(_code(_APP), "await apiClient.setTokenRefresher")
    assert "authService.refreshToken()" in closure and "return .refreshed(" in closure, "not the refresher"
    assert re.search(
        r"AppError\.from\(error\)\.isAuthError\s*\?\s*\.credentialRejected\s*:\s*\.transientFailure",
        closure), (
        "the refresher ends the session on a transient failure — only an isAuthError refresh "
        "error may answer .credentialRejected; a 429/5xx/offline must be .transientFailure")
    catches = re.findall(r"\bcatch\b", closure)
    assert len(catches) == 1, (
        f"the refresher has {len(catches)} `catch` clauses — an earlier one (`catch is APIError`) "
        "answers before the isAuthError ternary, and a 429/5xx refresh is an APIError")
    rejected = re.findall(r"\.credentialRejected\b", closure)
    assert len(rejected) == 2 and re.search(
            r"guard let token = await authService\.getStoredToken\(\) else \{\s*return \.credentialRejected\s*\}",
            closure), (
        f"the refresher answers .credentialRejected ({len(rejected)}×) outside the stored-token "
        "guard and the isAuthError ternary — a transient failure would end the session")

    # The callee. The ternary classifies whatever `AuthService.refreshToken()` throws, so that
    # error must reach it as the request threw it: a `catch` that converts it (`throw
    # APIError.unauthorized`) makes every 429/5xx/offline refresh isAuthError → a sign-out, and
    # one that clears the Keychain first leaves the NEXT refresh no refresh token → .unauthorized
    # → a sign-out one 401 later, while this refresh still answered .transientFailure.
    body = _block(_code(_AUTH_SERVICE), "func refreshToken() async throws")
    assert ("apiClient.request(" in body and ".refreshToken(refreshToken:" in body
            and "saveTokens(" in body), "not AuthService.refreshToken (no refresh request / saveTokens) — re-derive"
    assert not re.search(r"\bcatch\b|\btry\?", body), (
        "AuthService.refreshToken catches its refresh error — the refresher (iosApp.swift) "
        "classifies THAT error, so a converted or swallowed one makes a 429/5xx refresh answer "
        ".credentialRejected (or the session be cleared on a refresh that may be transient)")
    throws = [t.strip() for t in re.findall(r"\bthrow\b[^\n}]*", body)]
    assert throws == ["throw APIError.unauthorized"] and re.search(
            r"guard let refreshToken = getStoredRefreshToken\(\) else \{\s*throw APIError\.unauthorized\s*\}",
            body), (
        f"AuthService.refreshToken throws {throws} beyond the no-refresh-token guard — a client-made "
        "auth error for anything but a missing refresh token is answered .credentialRejected")
    enders = [e for e in ("clearToken(", "signOut(", "keychain.delete(") if e in body] + [
        m.group() for m in _NIL_TOKEN_WRITE.finditer(body)]
    assert not enders, (
        f"AuthService.refreshToken ends the session itself ({enders}) — on a refresh that may be "
        "transient; only the refresher's .credentialRejected → the 401 interceptor may end it")


# ── The mutations above, re-run in memory on every pass ─────────────────────

_I16, _I20 = " " * 16, " " * 20
_AFTER = {
    "request<T>": "if case .refreshed = outcome {\n" + _I20 + "return try await self.request(\n",
    "request(void)": "if case .refreshed = outcome {\n" + _I20
    + "return try await self.request(endpoint: endpoint, allowAuthRetry: false)",
    "downloadData": "if case .refreshed = outcome {\n" + _I20 + "return try await downloadData(",
    "openStream": "if case .refreshed = outcome {\n" + _I20 + "return try await openStreamOnce(",
}


def _tail(name: str, throw: str = _THROW) -> str:
    """The end of `name`'s transient arm plus the start of the next arm (unique per transport)."""
    return f"{_I20}{throw}\n{_I16}}}\n{_I16}{_AFTER[name]}"


_MSG_RETHROW = "a transient refresh failure re-throws the original 401"
_MSG_OUTSIDE = "a refresh call site outside the four guarded transports"
_COPY_RX = re.compile(r'(code: "AUTH_UNAVAILABLE",\s*message: )"[^"]*"')
_VOID_DEAD = "if await handleUnrecoverableAuthFailure(apiError, endpoint: endpoint) {"
_MARK = "    // MARK: - Request Methods\n"


# ── Function-based mutations (APIClient): structure, not text, so they survive edits to the
#    pre-flight's body. Each asserts its own anchors occur exactly once.

_PREFLIGHT_CALL_LINE = re.compile(r"^[ \t]*await (?:self\.)?refreshArmedTokenIfExpired\(for: \w+\)\n", re.M)


def _without_preflight(src: str, drop_calls: bool = True) -> str:
    """`src` with the pre-flight method (and, by default, its calls) removed — or unchanged
    when it is absent."""
    text = _blank_strings(_blank_comments(src))
    hits = list(_PREFLIGHT_ANY_DECL.finditer(text))
    assert len(hits) <= 1, f"`func refreshArmedTokenIfExpired` occurs {len(hits)}× — re-derive"
    if hits:
        start = text.rindex("\n", 0, hits[0].start()) + 1
        end = _balanced_end(text, text.index("{", hits[0].end())) + 1
        end += src[end: end + 1] == "\n"
        src = src[:start] + src[end:]
    return _PREFLIGHT_CALL_LINE.sub("", src) if drop_calls else src


def _insert_method(method: str, base: Callable[[str], str] = lambda s: s) -> Callable[[str], str]:
    """Insert a method (4-space indented Swift) after `// MARK: - Request Methods`."""
    def transform(src: str) -> str:
        src = base(src)
        assert src.count(_MARK) == 1, f"`{_MARK.strip()}` occurs {src.count(_MARK)}× — re-derive"
        return src.replace(_MARK, _MARK + "\n" + method + "\n", 1)
    return transform


def _replace_each(*steps: tuple[str, str]) -> Callable[[str], str]:
    """Apply several literal replacements in order; each anchor must occur exactly once."""
    def transform(src: str) -> str:
        for old, new in steps:
            assert src.count(old) == 1, f"mutation anchor `{old[:60]}` occurs {src.count(old)}× — re-derive"
            src = src.replace(old, new, 1)
        return src
    return transform


# A plausible "stop the zombie" product change: escalate a run of transient refreshes to a
# rejection — the NAT-429 sign-out back again, through a helper outside the single-flight.
_SF_END = "        refreshInFlight = nil\n        return outcome\n    }\n"
_ESCALATE = (
    "\n"
    "    private var consecutiveTransientRefreshFailures = 0\n"
    "\n"
    "    private func escalateRepeatedTransient(_ outcome: TokenRefreshOutcome) -> TokenRefreshOutcome {\n"
    "        guard case .transientFailure = outcome else {\n"
    "            consecutiveTransientRefreshFailures = 0\n"
    "            return outcome\n"
    "        }\n"
    "        consecutiveTransientRefreshFailures += 1\n"
    "        return consecutiveTransientRefreshFailures >= 3 ? .credentialRejected : outcome\n"
    "    }\n")
_ESCALATE_EXT = (
    "\nextension TokenRefreshOutcome {\n"
    "    func escalated(after failures: Int) -> TokenRefreshOutcome {\n"
    "        if case .transientFailure = self, failures >= 3 { return .credentialRejected }\n"
    "        return self\n"
    "    }\n"
    "}\n")
_TASK_REFRESH = "            let outcome = await refresher()\n"
_IS_AUTH_TRUE = "case .unauthorized, .tokenExpired, .sessionEnded:\n            return true"
_REFRESH_REQ = (
    "        let response = try await apiClient.request(\n"
    "            endpoint: .refreshToken(refreshToken: refreshToken),\n"
    "            responseType: AuthResponse.self\n"
    "        )\n")


def _refresh_req_caught(catch_body: str) -> str:
    """`_REFRESH_REQ` wrapped in `do { } catch { <catch_body> }` (12-space indented lines)."""
    return ("        let response: AuthResponse\n        do {\n"
            "            response = try await apiClient.request(\n"
            "                endpoint: .refreshToken(refreshToken: refreshToken),\n"
            "                responseType: AuthResponse.self\n"
            "            )\n"
            f"        }} catch {{\n{catch_body}        }}\n")


_HANDLE_SIGN_IN_ARM = "        case .signInRequired:\n            // Never a sign-out: there was no session to end.\n"


def _with_preflight(method: str) -> Callable[[str], str]:
    """Replace the pre-flight (present or not) with `method`, keeping the transports' calls."""
    return _insert_method(method, base=lambda s: _without_preflight(s, drop_calls=False))


def _at_body_start(header: str, statement: str) -> Callable[[str], str]:
    """Insert `statement` as the first line of the ONLY `header`'s body."""
    def transform(src: str) -> str:
        text = _blank_strings(_blank_comments(src))
        assert text.count(header) == 1, f"`{header}` occurs {text.count(header)}× — re-derive"
        brace = text.index("{", text.index(header) + len(header))
        return src[: brace + 1] + "\n" + statement + src[brace + 1:]
    return transform


def _preflight_variant(arm: str, signature: str = "async", tail: str = "",
                       name: str = "refreshArmedTokenIfExpired(for endpoint: APIEndpoint)") -> str:
    """A pre-flight whose non-`.refreshed` arm is `arm`."""
    return (
        f"    private func {name} {signature} {{\n"
        "        guard !endpoint.isAuthEndpoint, tokenRefresher != nil else { return }\n"
        "        let outcome = await self.refreshTokenSingleFlight()\n"
        "        switch outcome {\n"
        "        case .refreshed(let fresh):\n"
        "            preflightExemptToken = fresh\n"
        "        case .transientFailure, .credentialRejected:\n"
        f"            {arm}\n"
        "        }\n"
        f"{tail}"
        "    }\n")


_LOG_ONLY = 'Self.preflightLog.info("auth-preflight: did not complete (\\(endpoint.path, privacy: .public))")'
_CLIENT_TESTS = (
    test_every_refresh_call_site_is_one_of_the_four_guarded_transports,
    test_the_pre_flight_refresh_can_never_end_a_session,
    test_a_transient_refresh_surfaces_auth_unavailable_never_the_original_401,
    test_the_transient_error_is_auth_unavailable_with_honest_copy,
    test_the_single_flight_hands_back_every_outcome_untouched,
    test_only_the_401_interceptors_can_end_the_session,
)

# The pre-flight allow-list must hold BOTH ways: these shapes keep every APIClient guard green.
_COMPLIANT = [
    # The pre-flight absent (as before it landed), its calls removed with it.
    ("p01-preflight-absent", _without_preflight, False),
    # The minimal shape: adopts a refreshed token itself, does nothing otherwise.
    ("p02-preflight-adopts-refreshed-token", _with_preflight(
        "    private func refreshArmedTokenIfExpired(for endpoint: APIEndpoint) async {\n"
        "        guard !endpoint.isAuthEndpoint, tokenRefresher != nil, authToken != nil else { return }\n"
        "        let outcome = await self.refreshTokenSingleFlight()\n"
        "        if case .refreshed(let fresh) = outcome {\n"
        "            authToken = fresh\n"
        "        }\n"
        "    }\n"), True),
    # A switch whose non-.refreshed arm only logs (the shape that landed).
    ("p03-preflight-switch-log-only", _with_preflight(_preflight_variant(_LOG_ONLY)), True),
    # `guard case .refreshed … else { log; return }`, joining an in-flight refresh first.
    ("p04-preflight-guard-case-log-and-return", _with_preflight(
        "    private func refreshArmedTokenIfExpired(for endpoint: APIEndpoint) async {\n"
        "        guard !endpoint.isAuthEndpoint, tokenRefresher != nil else { return }\n"
        "        if let inFlight = refreshInFlight {\n"
        "            _ = await inFlight.value\n"
        "        }\n"
        "        let outcome = await refreshTokenSingleFlight()\n"
        "        guard case .refreshed(let fresh) = outcome else {\n"
        f"            {_LOG_ONLY}\n"
        "            return\n"
        "        }\n"
        "        preflightExemptToken = fresh\n"
        "    }\n"), True),
    # `default: break` instead of naming the failure cases.
    ("p05-preflight-default-break", _with_preflight(
        _preflight_variant(_LOG_ONLY).replace(
            "        case .transientFailure, .credentialRejected:\n            " + _LOG_ONLY,
            "        default:\n            break")), True),
    # Shorthand `if let refreshInFlight`, and a switch directly on the awaited single-flight.
    ("p06-preflight-shorthand-join-and-direct-switch", _with_preflight(
        "    private func refreshArmedTokenIfExpired(for endpoint: APIEndpoint) async {\n"
        "        if let refreshInFlight {\n"
        "            _ = await refreshInFlight.value\n"
        "        }\n"
        "        switch await self.refreshTokenSingleFlight() {\n"
        "        case .refreshed(let fresh):\n"
        "            preflightExemptToken = fresh\n"
        "        case .transientFailure, .credentialRejected:\n"
        f"            {_LOG_ONLY}\n"
        "        }\n"
        "    }\n"), True),
    # Discard only: the single-flight adopts a refreshed token itself.
    ("p07-preflight-discards-outcome", _with_preflight(
        "    private func refreshArmedTokenIfExpired(for endpoint: APIEndpoint) async {\n"
        "        guard refreshInFlight == nil, !endpoint.isAuthEndpoint else { return }\n"
        "        _ = await refreshTokenSingleFlight()\n"
        "    }\n"), True),
]

_T1 = test_every_refresh_call_site_is_one_of_the_four_guarded_transports
_T1B = test_the_pre_flight_refresh_can_never_end_a_session
_T2 = test_a_transient_refresh_surfaces_auth_unavailable_never_the_original_401
_T8 = test_the_single_flight_hands_back_every_outcome_untouched
_T9 = test_only_the_401_interceptors_can_end_the_session

# (id, file, anchor — a literal, a compiled regex, or a callable `src -> mutated src`;
# replacement (None for a callable); guard; the message it must fail WITH). Matching the message
# means a mutation cannot "pass" by tripping an unrelated, earlier assertion. Each literal/regex
# anchor must occur exactly once in the real source; a callable asserts its own anchors.
_MUTATIONS = [
    # 1-4: each transport re-throws the original 401 again.
    ("m01-request<T>-rethrows", _CLIENT, _tail("request<T>"), _tail("request<T>", "throw error"),
     _T2, f"request<T>: {_MSG_RETHROW}"),
    ("m02-request(void)-rethrows", _CLIENT, _tail("request(void)"), _tail("request(void)", "throw apiError"),
     _T2, f"request(void): {_MSG_RETHROW}"),
    ("m03-downloadData-rethrows", _CLIENT, _tail("downloadData"), _tail("downloadData", "throw error"),
     _T2, f"downloadData: {_MSG_RETHROW}"),
    ("m04-openStream-rethrows", _CLIENT, _tail("openStream"), _tail("openStream", "throw error"),
     _T2, f"openStream: {_MSG_RETHROW}"),
    # 5: the fix survives only as prose — proves comments are stripped.
    ("m05-openStream-fix-only-in-a-comment", _CLIENT, _tail("openStream"),
     _tail("openStream", f"// {_THROW}  (transient: session kept)\n{_I20}throw error"),
     _T2, f"openStream: {_MSG_RETHROW}"),
    # 6: the arm clears the credential before throwing the right error.
    ("m06-openStream-arm-clears-token", _CLIENT, _tail("openStream"),
     _tail("openStream", f"authToken = nil\n{_I20}{_THROW}"),
     _T2, "openStream: the transient arm touches the credential"),
    # 7: the arm is deleted (the transient outcome falls through to the dead-credential path).
    ("m07-downloadData-arm-deleted", _CLIENT,
     re.compile(r"if case \.transientFailure = outcome \{[^{}]*\}\n\s*"
                r"(?=if case \.refreshed = outcome \{\n\s*return try await downloadData\()"),
     "", _T2, "downloadData: expected exactly one `.transientFailure` arm"),
    # 8: a fifth refresh call site.
    ("m08-fifth-refresh-call-site", _CLIENT, "    // MARK: - Request Methods\n",
     "    // MARK: - Request Methods\n\n    func probe() async { _ = await refreshTokenSingleFlight() }\n",
     _T1, _MSG_OUTSIDE),
    # 9-12: the helper builds the wrong value.
    ("m09-helper-wrong-code", _CLIENT, 'code: "AUTH_UNAVAILABLE"', 'code: "AUTH_TOKEN_INVALID"',
     test_the_transient_error_is_auth_unavailable_with_honest_copy, "transientRefreshError must build"),
    ("m10-helper-empty-message", _CLIENT, _COPY_RX, r'\1""',
     test_the_transient_error_is_auth_unavailable_with_honest_copy, "transientRefreshError needs a non-empty message"),
    ("m11-helper-session-expired-copy", _CLIENT, _COPY_RX, r'\1"Your session has expired. Please sign in again."',
     test_the_transient_error_is_auth_unavailable_with_honest_copy,
     "the transient copy must not tell the user their session ended"),
    ("m12-helper-returns-unauthorized", _CLIENT,
     re.compile(r'\.authError\(\s*code: "AUTH_UNAVAILABLE",\s*message: "[^"]*"\s*\)'), ".unauthorized",
     test_the_transient_error_is_auth_unavailable_with_honest_copy, "transientRefreshError must build"),
    # 13-16: AppError semantics.
    ("m13-mapping-to-tokenExpired", _APP_ERROR,
     'case "AUTH_UNAVAILABLE":\n                return .authUnavailable(message: message)',
     'case "AUTH_UNAVAILABLE":\n                return .tokenExpired',
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session,
     "AUTH_UNAVAILABLE must map to .authUnavailable"),
    ("m14-isAuthError-gains-authUnavailable", _APP_ERROR,
     "case .unauthorized, .tokenExpired, .sessionEnded:\n            return true",
     "case .unauthorized, .tokenExpired, .sessionEnded, .authUnavailable:\n            return true",
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session, "`.authUnavailable` joined isAuthError"),
    ("m15-triggersTokenRefresh-gains-code", _APP_ERROR,
     'return code == "AUTH_TOKEN_INVALID" || code == "AUTH_SESSION_EXPIRED"',
     'return code == "AUTH_TOKEN_INVALID" || code == "AUTH_SESSION_EXPIRED" || code == "AUTH_UNAVAILABLE"',
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session,
     "AUTH_UNAVAILABLE must not trigger a refresh"),
    ("m16-isRetryable-loses-authUnavailable", _APP_ERROR,
     "case .timeout, .serverError, .rateLimited, .authUnavailable:\n            return true",
     "case .timeout, .serverError, .rateLimited:\n            return true",
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session, "`.authUnavailable` must stay retryable"),
    # 17-18: AppState funnels.
    ("m17-handleError-signs-out", _APP_STATE,
     "case .unauthorized, .tokenExpired:\n            if hasUnusedStoredCredential",
     "case .unauthorized, .tokenExpired, .authUnavailable:\n            if hasUnusedStoredCredential",
     test_app_state_never_signs_out_on_auth_unavailable, "AppState.handleError would sign out on .authUnavailable"),
    ("m18-reportMutationFailure-routes-to-session", _APP_STATE,
     "case .unauthorized, .tokenExpired, .sessionEnded:",
     "case .unauthorized, .tokenExpired, .sessionEnded, .authUnavailable:",
     test_app_state_never_signs_out_on_auth_unavailable,
     "reportMutationFailure routes .authUnavailable to the session logic"),
    # 19-20: the poll classifier.
    ("m19-poll-tokenExpired-transient", _POLL,
     "case .noConnection, .timeout, .serverError, .rateLimited, .authUnavailable,",
     "case .noConnection, .timeout, .serverError, .rateLimited, .authUnavailable, .tokenExpired,",
     test_the_poll_rides_out_auth_unavailable_but_stops_on_a_dead_session,
     "`.tokenExpired` is a transient poll failure"),
    ("m20-poll-loses-authUnavailable", _POLL,
     "case .noConnection, .timeout, .serverError, .rateLimited, .authUnavailable,",
     "case .noConnection, .timeout, .serverError, .rateLimited,",
     test_the_poll_rides_out_auth_unavailable_but_stops_on_a_dead_session,
     "`.authUnavailable` must be a transient poll miss"),
    # 21: the refresher rejects on a transient failure.
    ("m21-refresher-rejects-transient", _APP, ": .transientFailure", ": .credentialRejected",
     test_the_refresher_rejects_only_on_a_genuine_auth_failure,
     "the refresher ends the session on a transient failure"),
    # ── Beyond the design's 21: the fall-through and ordering holes. ──
    # 22: the transient arm moved AFTER the dead-credential path (it would never run first).
    ("m22-request(void)-arm-after-dead-path", _CLIENT,
     re.compile(r"(if case \.transientFailure = outcome \{[^{}]*\}\n\s*)"
                r"(if case \.refreshed = outcome \{\n\s*return try await self\.request\(endpoint: endpoint, "
                r"allowAuthRetry: false\)\n\s*\}\n\s*" + re.escape(_VOID_DEAD)
                + r"\n\s*return try await self\.request\(endpoint: endpoint, allowAuthRetry: false\)\n\s*\}\n\s*)"),
     r"\2\1", _T2, "request(void): the `.transientFailure` arm must run before handleUnrecoverableAuthFailure"),
    # 23: handleError's default arm signs out (`.authUnavailable` falls into it).
    ("m23-handleError-default-signs-out", _APP_STATE,
     "        default:\n            break\n        }\n\n        currentError = appError\n    }",
     "        default:\n            signOut()\n        }\n\n        currentError = appError\n    }",
     test_app_state_never_signs_out_on_auth_unavailable, "AppState.handleError would sign out on .authUnavailable"),
    # 24: reportMutationFailure's default arm hands everything to the session logic.
    ("m24-reportMutationFailure-default-to-session", _APP_STATE,
     '        default:\n            showToast("Couldn\'t \\(action). \\(appError.message)", type: .error)',
     '        default:\n            handleError(error)\n'
     '            showToast("Couldn\'t \\(action). \\(appError.message)", type: .error)',
     test_app_state_never_signs_out_on_auth_unavailable,
     "reportMutationFailure routes .authUnavailable to the session logic"),
    # 25: isAuthError's default flips to true (`.authUnavailable` falls into it).
    ("m25-isAuthError-default-true", _APP_ERROR,
     "is transient by definition.\n        default:\n            return false",
     "is transient by definition.\n        default:\n            return true",
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session, "`.authUnavailable` joined isAuthError"),
    # 26: the poll's default flips to true (every dead-session case becomes transient).
    ("m26-poll-default-true", _POLL,
     "return message != Self.decodeFailureMessage\n        default:\n            return false",
     "return message != Self.decodeFailureMessage\n        default:\n            return true",
     test_the_poll_rides_out_auth_unavailable_but_stops_on_a_dead_session,
     "`.tokenExpired` is a transient poll failure"),
    # ── The verifier's proven gaps (G1-G10) and the pre-flight allow-list. ──
    # 27 (G1): a new call site spelled `self.refreshTokenSingleFlight()` — the literal count missed it.
    ("m27-self-prefixed-call-site", _CLIENT, _insert_method(
        "    func refreshForWidget() async throws {\n"
        "        let o = await self.refreshTokenSingleFlight()\n"
        "        if case .transientFailure = o { throw APIError.unauthorized }\n"
        "    }\n"), None, _T1, _MSG_OUTSIDE),
    # 28: a method REFERENCE escapes any `(`-based count.
    ("m28-method-reference", _CLIENT, _insert_method(
        "    func refreshHook() -> () async -> TokenRefreshOutcome { refreshTokenSingleFlight }\n"),
     None, _T1, _MSG_OUTSIDE),
    # 29: a call inside a transport but OUTSIDE its 401 interceptor (before the request).
    ("m29-call-in-transport-outside-interceptor", _CLIENT, _at_body_start(
        "func downloadData(endpoint: APIEndpoint,",
        "        if case .transientFailure = await refreshTokenSingleFlight() { throw APIError.unauthorized }"),
     None, _T1, _MSG_OUTSIDE),
    # 30: a second call inside one interceptor.
    ("m30-second-call-in-an-interceptor", _CLIENT,
     re.compile(
         r"let outcome = await refreshTokenSingleFlight\(\)(?=\n\s*if case \.transientFailure = outcome \{[^{}]*\}"
         r"\n\s*if case \.refreshed = outcome \{\n\s*return try await self\.request\(endpoint: endpoint, "
         r"allowAuthRetry: false\))"),
     "_ = await refreshTokenSingleFlight()\n" + _I16 + "let outcome = await refreshTokenSingleFlight()",
     _T1, "request(void): expected exactly one refresh call in its 401 interceptor, found 2"),
    # 31: the exemption is BY NAME — a look-alike name gets none.
    ("m31-lookalike-name-not-exempt", _CLIENT, _insert_method(_preflight_variant(
        _LOG_ONLY, name="refreshArmedTokenIfExpiredNow(for endpoint: APIEndpoint)")),
     None, _T1, _MSG_OUTSIDE),
    # 32: an overload would share the exemption.
    ("m32-overload-shares-exemption", _CLIENT, _insert_method(_preflight_variant(
        _LOG_ONLY, name="refreshArmedTokenIfExpired(for endpoint: APIEndpoint, force: Bool)"),
        base=_with_preflight(_preflight_variant(_LOG_ONLY))),
     None, _T1, "`refreshArmedTokenIfExpired` is declared 2×"),
    # 33: the pre-flight renamed to another signature is no longer the allow-listed method.
    ("m33-preflight-signature-renamed", _CLIENT, _with_preflight(_preflight_variant(
        _LOG_ONLY, name="refreshArmedTokenIfExpired(for endpoint: APIEndpoint, force: Bool)")),
     None, _T1, f"the allow-listed pre-flight must be exactly `{_PREFLIGHT}`"),
    # 34 (G9): a transport calls the refresher directly, behind the single-flight's back.
    ("m34-transport-calls-refresher-directly", _CLIENT, _at_body_start(
        "func downloadData(endpoint: APIEndpoint,",
        "        if let r = tokenRefresher, case .transientFailure = await r() { throw APIError.unauthorized }"),
     None, _T1, "the refresher is reached outside refreshTokenSingleFlight"),
    # 35: a transport reads the in-flight refresh's outcome itself.
    ("m35-transport-reads-in-flight-task", _CLIENT, _at_body_start(
        "private func openStream(endpoint: APIEndpoint,",
        "        if let t = refreshInFlight, case .transientFailure = await t.value { throw APIError.unauthorized }"),
     None, _T1, "the in-flight refresh task is read outside refreshTokenSingleFlight"),
    # 36-43: the pre-flight's contract (T1b), each variant replacing the real pre-flight.
    ("m36-preflight-throws", _CLIENT, _with_preflight(_preflight_variant(
        "throw Self.transientRefreshError()", signature="async throws")),
     None, _T1B, f"`{_PREFLIGHT}` must never throw"),
    ("m37-preflight-ends-session", _CLIENT, _with_preflight(_preflight_variant(
        "_ = await handleUnrecoverableAuthFailure(.unauthorized, endpoint: endpoint)")),
     None, _T1B, f"`{_PREFLIGHT}` touches the session (`handleUnrecoverableAuthFailure`)"),
    ("m38-preflight-reports-auth-failure", _CLIENT, _with_preflight(_preflight_variant(
        "await authFailureHandler?(.credentialRejected)")),
     None, _T1B, f"`{_PREFLIGHT}` touches the session (`authFailureHandler`)"),
    ("m39-preflight-clears-token", _CLIENT, _with_preflight(_preflight_variant("authToken = nil")),
     None, _T1B, f"`{_PREFLIGHT}` writes `authToken = nil`"),
    ("m40-preflight-acts-on-rejection-outside-switch", _CLIENT, _with_preflight(_preflight_variant(
        _LOG_ONLY, tail="        if case .credentialRejected = outcome { expiryMemo = nil }\n")),
     None, _T1B, f"`{_PREFLIGHT}` names `.credentialRejected` outside a switch arm's pattern"),
    ("m41-preflight-arm-not-inert", _CLIENT, _with_preflight(_preflight_variant("preflightExemptToken = nil")),
     None, _T1B, f"`{_PREFLIGHT}` acts on a non-.refreshed outcome (arm `.transientFailure, .credentialRejected`"),
    ("m42-preflight-else-not-inert", _CLIENT, _with_preflight(
        "    private func refreshArmedTokenIfExpired(for endpoint: APIEndpoint) async {\n"
        "        let outcome = await self.refreshTokenSingleFlight()\n"
        "        if case .refreshed(let fresh) = outcome {\n"
        "            preflightExemptToken = fresh\n"
        "        } else {\n"
        "            preflightExemptToken = nil\n"
        "        }\n"
        "    }\n"),
     None, _T1B, f"`{_PREFLIGHT}` acts on a non-.refreshed outcome (the `else` of `if case .refreshed`"),
    ("m43-preflight-returns-outcome", _CLIENT, _with_preflight(_preflight_variant(
        _LOG_ONLY, signature="async -> Bool", tail="        return false\n")),
     None, _T1B, f"`{_PREFLIGHT}` must be plain `async`"),
    # 44 (G2): a CONDITIONAL transient arm — false condition = fall through to the dead path.
    ("m44-request<T>-arm-conditional", _CLIENT,
     re.compile(r"if case \.transientFailure = outcome \{(?=[^{}]*\}\n\s*if case \.refreshed = outcome \{"
                r"\n\s*return try await self\.request\(\n)"),
     "if case .transientFailure = outcome, retryCount > 0 {",
     _T2, "request<T>: the `.transientFailure` arm is conditional"),
    # 45: code between the refresh and the arm runs on a transient outcome too.
    ("m45-openStream-acts-before-the-arm", _CLIENT,
     re.compile(r"(let outcome = await refreshTokenSingleFlight\(\)\n)(\s*)"
                r"(?=if case \.transientFailure = outcome \{[^{}]*\}\n\s*if case \.refreshed = outcome \{"
                r"\n\s*return try await openStreamOnce\()"),
     r"\1\2await authFailureHandler?(.credentialRejected)\n\2",
     _T2, "openStream: the `.transientFailure` arm is not the first thing done with the refresh outcome"),
    # 46-48 (G10): the single-flight itself.
    ("m46-single-flight-reports-transient", _CLIENT,
     "            if case .refreshed(let newToken) = outcome { self.authToken = newToken }\n",
     "            if case .refreshed(let newToken) = outcome { self.authToken = newToken }\n"
     "            if case .transientFailure = outcome { await self.authFailureHandler?(.credentialRejected) }\n",
     _T8, "refreshTokenSingleFlight touches the session (`authFailureHandler`)"),
    ("m47-single-flight-clears-token", _CLIENT,
     "            if case .refreshed(let newToken) = outcome { self.authToken = newToken }\n",
     "            if case .refreshed(let newToken) = outcome { self.authToken = newToken }\n"
     "            if case .transientFailure = outcome { self.authToken = nil }\n",
     _T8, "refreshTokenSingleFlight writes authToken other than adopting a refreshed token"),
    ("m48-single-flight-converts-transient", _CLIENT,
     "        refreshInFlight = nil\n        return outcome\n",
     "        refreshInFlight = nil\n"
     "        if case .transientFailure = outcome { return .credentialRejected }\n"
     "        return outcome\n",
     _T8, "refreshTokenSingleFlight answers .credentialRejected other than when no refresher is wired"),
    # 49-51: the session-ending doors, reached through a helper.
    ("m49-helper-ends-session", _CLIENT, _insert_method(
        "    private func endDeadSession(_ error: APIError, endpoint: APIEndpoint) async {\n"
        "        _ = await handleUnrecoverableAuthFailure(error, endpoint: endpoint)\n"
        "    }\n"), None, _T9, "handleUnrecoverableAuthFailure is reached from outside the four guarded transports"),
    ("m50-helper-reports-auth-failure", _CLIENT, _insert_method(
        "    private func reportDeadCredential() async {\n"
        "        await authFailureHandler?(.credentialRejected)\n"
        "    }\n"), None, _T9, "authFailureHandler is called from outside handleUnrecoverableAuthFailure"),
    ("m51-helper-clears-token", _CLIENT, _insert_method(
        "    private func dropToken() {\n"
        "        authToken = nil\n"
        "    }\n"), None, _T9, "authToken is written outside setAuthToken"),
    # 52 (G3): an earlier arm shadows AUTH_UNAVAILABLE (first match wins) → .sessionEnded → signOut.
    ("m52-auth-unavailable-shadowed", _APP_ERROR,
     'case "AUTH_SESSION_EXPIRED", "AUTH_ACCOUNT_NOT_FOUND":',
     'case "AUTH_SESSION_EXPIRED", "AUTH_ACCOUNT_NOT_FOUND", "AUTH_UNAVAILABLE":',
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session, "AUTH_UNAVAILABLE is shadowed"),
    # 53-54 (G8): an early return before the parsed switch decides the answer.
    ("m53-isAuthError-early-return", _APP_ERROR,
     "    var isAuthError: Bool {\n        switch self {\n",
     "    var isAuthError: Bool {\n        if case .authUnavailable = self { return true }\n        switch self {\n",
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session,
     "isAuthError: code outside its `switch self` decides the answer"),
    ("m54-isRetryable-early-return", _APP_ERROR,
     "    var isRetryable: Bool {\n        switch self {\n",
     "    var isRetryable: Bool {\n        if case .authUnavailable = self { return false }\n        switch self {\n",
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session,
     "isRetryable: code outside its `switch self` decides the answer"),
    # 55 (G7): the poll decides before its switch.
    ("m55-poll-early-return", _POLL,
     "    nonisolated static func isTransientPollFailure(_ error: AppError) -> Bool {\n        switch error {\n",
     "    nonisolated static func isTransientPollFailure(_ error: AppError) -> Bool {\n"
     "        if case .tokenExpired = error { return true }\n        switch error {\n",
     test_the_poll_rides_out_auth_unavailable_but_stops_on_a_dead_session,
     "isTransientPollFailure: code outside its `switch error` decides the answer"),
    # 56-57 (G4/G5): AppState funnels act before their switch.
    ("m56-handleError-signs-out-before-switch", _APP_STATE,
     "        switch appError {\n        case .sessionEnded:\n",
     "        if case .authUnavailable = appError { signOut() }\n        switch appError {\n        case .sessionEnded:\n",
     test_app_state_never_signs_out_on_auth_unavailable,
     "AppState.handleError ends the session outside its switch (`signOut(`)"),
    ("m57-reportMutationFailure-routes-before-switch", _APP_STATE,
     "        switch appError {\n        case .signInRequired:\n            requestSignIn(",
     "        if case .authUnavailable = appError { handleError(error); return }\n"
     "        switch appError {\n        case .signInRequired:\n            requestSignIn(",
     test_app_state_never_signs_out_on_auth_unavailable,
     "reportMutationFailure reaches the session logic outside its switch (`handleError(`)"),
    # 58-59 (G6): the refresher answers a rejection before the ternary.
    ("m58-refresher-second-catch", _APP,
     re.compile(r"\} catch \{(?=\s*return AppError\.from\(error\)\.isAuthError)"),
     "} catch is APIError {\n" + " " * 28 + "return .credentialRejected\n" + " " * 24 + "} catch {",
     test_the_refresher_rejects_only_on_a_genuine_auth_failure, "the refresher has 2 `catch` clauses"),
    ("m59-refresher-extra-rejection", _APP,
     re.compile(r"(\n(\s*))(?=return AppError\.from\(error\)\.isAuthError)"),
     r"\1if error is URLError { return .credentialRejected }\1",
     test_the_refresher_rejects_only_on_a_genuine_auth_failure,
     "the refresher answers .credentialRejected (3×) outside the stored-token guard"),
    # 60-62: the pre-flight hands its outcome on WITHOUT a return value (a transport reads it).
    ("m60-preflight-stores-outcome", _CLIENT, _with_preflight(_preflight_variant(
        _LOG_ONLY, tail="        lastPreflightOutcome = outcome\n")),
     None, _T1B, f"`{_PREFLIGHT}` lets a refresh outcome out of a pattern match (['lastPreflightOutcome = outcome'])"),
    ("m61-preflight-stores-in-flight-outcome", _CLIENT, _with_preflight(
        "    private func refreshArmedTokenIfExpired(for endpoint: APIEndpoint) async {\n"
        "        if let inFlight = refreshInFlight {\n"
        "            lastPreflightOutcome = await inFlight.value\n"
        "        }\n"
        "    }\n"),
     None, _T1B,
     f"`{_PREFLIGHT}` lets a refresh outcome out of a pattern match (['lastPreflightOutcome = await inFlight.value'])"),
    ("m62-preflight-passes-outcome-on", _CLIENT, _with_preflight(_preflight_variant(
        _LOG_ONLY, tail="        recordPreflight(outcome)\n")),
     None, _T1B, f"`{_PREFLIGHT}` lets a refresh outcome out of a pattern match (['recordPreflight(outcome)'])"),
    # 63: `guard case .refreshed … else { <acts> }` (the third branch shape T1b reads).
    ("m63-preflight-guard-else-not-inert", _CLIENT, _with_preflight(
        "    private func refreshArmedTokenIfExpired(for endpoint: APIEndpoint) async {\n"
        "        let outcome = await refreshTokenSingleFlight()\n"
        "        guard case .refreshed(let fresh) = outcome else {\n"
        "            preflightExemptToken = nil\n"
        "            return\n"
        "        }\n"
        "        preflightExemptToken = fresh\n"
        "    }\n"),
     None, _T1B, f"`{_PREFLIGHT}` acts on a non-.refreshed outcome (the `else` of `guard case .refreshed`"),
    # 64: the failure arm clears the credential through the setter.
    ("m64-preflight-arm-calls-setAuthToken", _CLIENT, _with_preflight(_preflight_variant("setAuthToken(nil)")),
     None, _T1B, f"`{_PREFLIGHT}` touches the session (`setAuthToken`)"),
    # ── Second review: the gaps it proved (A, E, F, B rows). ──
    # 65-68 (A1/A3/A4/A5): the outcome rewritten on its way out of the single-flight.
    ("m65-single-flight-task-escalates-via-helper", _CLIENT, _replace_each(
        (_TASK_REFRESH, "            let outcome = self.escalateRepeatedTransient(await refresher())\n"),
        (_SF_END, _SF_END + _ESCALATE)),
     None, _T8, "refreshTokenSingleFlight transforms the refresh outcome before handing it back"),
    ("m66-single-flight-follower-escalates-via-helper", _CLIENT, _replace_each(
        ("            return await inFlight.value\n",
         "            return escalateRepeatedTransient(await inFlight.value)\n"),
        (_SF_END, _SF_END + _ESCALATE)),
     None, _T8, "refreshTokenSingleFlight returns something other than the refresher's own outcome"),
    ("m67-single-flight-task-escalates-via-extension", _CLIENT, _replace_each(
        (_TASK_REFRESH, "            let outcome = await refresher().escalated(after: self.refreshFailures)\n"),
        (_SF_END, _SF_END + _ESCALATE_EXT)),
     None, _T8, "refreshTokenSingleFlight transforms the refresh outcome before handing it back"),
    ("m68-setter-wraps-refresher", _CLIENT, _replace_each(
        ("        self.tokenRefresher = refresher\n",
         "        self.tokenRefresher = { [weak self] in\n"
         "            let outcome = await refresher()\n"
         "            return await self?.escalateRepeatedTransient(outcome) ?? outcome\n"
         "        }\n"),
        (_SF_END, _SF_END + _ESCALATE)),
     None, _T8, "setTokenRefresher stores something other than the refresher it was given"),
    # 69-72 (E1/E3): isAuthError's true set widened — a 429/5xx refresh answers .credentialRejected.
    ("m69-isAuthError-gains-rateLimited", _APP_ERROR, _IS_AUTH_TRUE,
     "case .unauthorized, .tokenExpired, .sessionEnded, .rateLimited:\n            return true",
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session,
     "isAuthError's true set is ['.rateLimited', '.sessionEnded', '.tokenExpired', '.unauthorized']"),
    ("m70-isAuthError-gains-serverError", _APP_ERROR, _IS_AUTH_TRUE,
     "case .unauthorized, .tokenExpired, .sessionEnded, .serverError:\n            return true",
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session,
     "isAuthError's true set is ['.serverError', '.sessionEnded', '.tokenExpired', '.unauthorized']"),
    ("m71-isAuthError-computed-arm", _APP_ERROR, _IS_AUTH_TRUE,
     "case .rateLimited(let retryAfter):\n            return retryAfter > 300\n        " + _IS_AUTH_TRUE,
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session,
     "isAuthError arm `.rateLimited(let retryAfter)` decides other than `return true` / `return false`"),
    ("m72-isAuthError-default-true-behind-own-arm", _APP_ERROR,
     "is transient by definition.\n        default:\n            return false",
     "is transient by definition.\n        case .authUnavailable:\n            return false\n"
     "        default:\n            return true",
     test_auth_unavailable_maps_to_a_retryable_case_that_keeps_the_session,
     "isAuthError's true set is ['.sessionEnded', '.tokenExpired', '.unauthorized', 'default']"),
    # 73-76 (F): AuthService.refreshToken converts, swallows or acts on its own refresh failure.
    ("m73-refreshToken-clears-keychain-on-failure", _AUTH_SERVICE, _REFRESH_REQ,
     _refresh_req_caught("            clearToken()\n            throw error\n"),
     test_the_refresher_rejects_only_on_a_genuine_auth_failure,
     "AuthService.refreshToken catches its refresh error"),
    ("m74-refreshToken-normalises-to-unauthorized", _AUTH_SERVICE, _REFRESH_REQ,
     _refresh_req_caught("            throw APIError.unauthorized\n"),
     test_the_refresher_rejects_only_on_a_genuine_auth_failure,
     "AuthService.refreshToken catches its refresh error"),
    ("m75-refreshToken-throws-on-session-change", _AUTH_SERVICE,
     "        guard epoch == sessionEpoch else { return }\n\n        // Store new tokens\n",
     "        guard epoch == sessionEpoch else { throw APIError.unauthorized }\n\n        // Store new tokens\n",
     test_the_refresher_rejects_only_on_a_genuine_auth_failure,
     "AuthService.refreshToken throws ['throw APIError.unauthorized', 'throw APIError.unauthorized'] "
     "beyond the no-refresh-token guard"),
    ("m76-refreshToken-defer-clears-on-failure", _AUTH_SERVICE, _replace_each(
        ("        guard let refreshToken = getStoredRefreshToken() else {\n"
         "            throw APIError.unauthorized\n        }\n",
         "        guard let refreshToken = getStoredRefreshToken() else {\n"
         "            throw APIError.unauthorized\n        }\n"
         "        var refreshed = false\n"
         "        defer { if !refreshed { clearToken() } }\n"),
        (_REFRESH_REQ, _REFRESH_REQ + "        refreshed = true\n")),
     None, test_the_refresher_rejects_only_on_a_genuine_auth_failure,
     "AuthService.refreshToken ends the session itself (['clearToken('])"),
    # 77-79 (B1b/B1): a funnel's own `.authUnavailable` arm ends the session without signOut().
    ("m77-handleError-arm-ends-session", _APP_STATE, _HANDLE_SIGN_IN_ARM,
     "        case .authUnavailable:\n            endSessionForDeadCredential()\n"
     "            currentError = appError\n            return\n" + _HANDLE_SIGN_IN_ARM,
     test_app_state_never_signs_out_on_auth_unavailable,
     "AppState.handleError ends the session on .authUnavailable (arm `.authUnavailable`: "
     "['endSessionForDeadCredential('])"),
    ("m78-handleError-arm-disarms-token", _APP_STATE, _HANDLE_SIGN_IN_ARM,
     "        case .authUnavailable:\n            Task { await apiClient.setAuthToken(nil) }\n"
     "            currentError = appError\n            return\n" + _HANDLE_SIGN_IN_ARM,
     test_app_state_never_signs_out_on_auth_unavailable,
     "AppState.handleError ends the session on .authUnavailable (arm `.authUnavailable`: "
     "['setAuthToken(nil)'])"),
    ("m79-reportMutationFailure-arm-ends-session", _APP_STATE,
     "        case .unauthorized, .tokenExpired, .sessionEnded:\n            // Let the session",
     "        case .authUnavailable:\n            endSessionForDeadCredential()\n"
     "            showToast(\"Couldn't \\(action).\", type: .error)\n"
     "        case .unauthorized, .tokenExpired, .sessionEnded:\n            // Let the session",
     test_app_state_never_signs_out_on_auth_unavailable,
     "reportMutationFailure ends the session on .authUnavailable (arm `.authUnavailable`: "
     "['endSessionForDeadCredential('])"),
]


@pytest.mark.parametrize(
    "path,old,new,test,message",
    [m[1:] for m in _MUTATIONS],
    ids=[m[0] for m in _MUTATIONS],
)
def test_each_mutation_is_killed(monkeypatch, path, old, new, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — other sessions read these Swift files concurrently, so they
    are never rewritten."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    if callable(old):
        assert new is None, "a callable mutation carries no replacement"
        mutated = old(original)
    elif isinstance(old, re.Pattern):
        hits = len(list(old.finditer(original)))
        assert hits == 1, (
            f"mutation regex `{old.pattern[:60]}` matches {hits}× in {path.name} (want exactly 1) "
            "— re-derive this mutation against the new source rather than deleting it")
        mutated = old.sub(new, original, count=1)
    else:
        assert original.count(old) == 1, (
            f"mutation anchor `{old[:60]}` occurs {original.count(old)}× in {path.name} (want "
            "exactly 1) — re-derive this mutation against the new source rather than deleting it")
        mutated = original.replace(old, new, 1)
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    # The unmutated source passes (the plain tests above prove it); mutated, it must fail.
    with pytest.raises(AssertionError, match=re.escape(message)):
        test()


@pytest.mark.parametrize("transform,present", [c[1:] for c in _COMPLIANT], ids=[c[0] for c in _COMPLIANT])
def test_each_compliant_variant_passes(monkeypatch, transform, present):
    """The pre-flight allow-list holds both ways: with the pre-flight absent, or replaced by
    another compliant shape, every APIClient guard stays green (no AssertionError)."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(_CLIENT, encoding="utf-8")
    mutated = transform(original)
    # Anti-vacuity: the variant really is absent / a different pre-flight, not the real source.
    assert mutated != original
    assert (_preflight(_blank_strings(_strip_swift_comments(mutated))) is not None) == present, (
        f"the variant should have the pre-flight {'present' if present else 'absent'}")

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == _CLIENT:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    for test in _CLIENT_TESTS:
        test()
