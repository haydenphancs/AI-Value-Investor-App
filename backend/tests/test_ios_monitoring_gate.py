"""A Debug iOS build must never report to the production Sentry project.

The iOS client DSN is committed (it is public/write-only by design) and points at the
PRODUCTION `caydex-apple-ios` project. Sentry's Discord alert is "A new issue is created" on
ALL environments, so an `environment=development` tag was never enough — simulator runs still
filed production issues, still pinged Discord, still burned quota, and still had to be triaged
by a human who did not yet know they were noise.

Measured 2026-08-07: 23 unresolved issues in 24h, essentially all local. Two that can ONLY ever
be Debug artifacts:

  * "Fatal App Hang" whose main thread sits in `_swift_getGenericMetadata` under
    `LockingConcurrentMap` inside `ViewLayoutEngine.explicitAlignment` — Debug builds do not
    pre-specialize generics, so the first SwiftUI layout instantiates metadata at runtime, which
    on a simulator alone exceeds the 2s watchdog.
  * `ThemeContrastAudit.swift:172` — `assertionFailure("Theme contrast regression: …")`. That
    audit is `#if DEBUG` only, and `assertionFailure` traps under `-Onone` while being compiled
    out at `-O`, so a user cannot reach it twice over. It filed 5 production issues. (A trapped
    `assertionFailure` prints "Fatal error:" at runtime, which is why the crash report reads
    that way while the source says `assertionFailure`.)

Gating on the build CONFIGURATION (not the backend's `ENVIRONMENT == "production"`) is
deliberate: it keeps BOTH TestFlight and App Store builds reporting, since those are
Release-configured. Note the resulting asymmetry is intentional — backend *staging* does not
report, iOS TestFlight does.

The backend has NO equivalent hole: `app/main.py` gates on
`settings.SENTRY_DSN and settings.ENVIRONMENT == "production"`, and `ENVIRONMENT` defaults to
`"development"` (`app/config.py`), so a local dev server does not ship to the production
project. `tests/test_sentry_inert_in_tests.py` guards that side. (An older note in the
`project_error_monitoring` memory claims the local server does leak — that predates the gate.)

## Why these assertions are shaped the way they are

The gate is a runtime `guard !isDebugBuild` rather than an `#if DEBUG … return #else … #endif`
wrapped around the whole body. Both suppress reporting identically, but the `#else` form also
excludes the Sentry integration from Debug COMPILATION, so a sentry-cocoa API change would
first surface at archive time. `test_the_sentry_block_is_still_compiled_in_debug` pins that.

Every assertion below is bounded to the smallest window that can contain the thing it checks.
An earlier revision of this file sliced from `#if DEBUG` all the way to `SentrySDK.start`,
which swallowed the DSN guard's own `return` — so the assertion passed even with the gate's
`return` deleted, i.e. it could never have failed for the reason it names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SRC = (
    Path(__file__).resolve().parents[2]
    / "frontend/ios/ios/Core/Monitoring/MonitoringConfig.swift"
)


def _source() -> str:
    if not _SRC.exists():
        pytest.skip(f"{_SRC} not present")
    return _SRC.read_text()


def _code_only(src: str) -> str:
    """`src` with whole-line comments blanked out (line numbering preserved).

    Every structural assertion below runs against this rather than the raw source. This file's
    comments *discuss* `#if DEBUG` and `SentrySDK.start` at length — deliberately, since the
    reasoning is the point — so a naive `src.index("#if DEBUG")` matches the prose, not the
    directive. Blanking rather than deleting keeps line numbers aligned.
    """
    return "\n".join(
        "" if line.strip().startswith("//") else line
        for line in src.splitlines()
    )


def _start_fn(src: str) -> str:
    return src[src.index("func startErrorMonitoring()"):]


def _braced_block(src: str, opener: str) -> str:
    """The `{ … }` block introduced by `opener`, located by brace matching.

    Deliberately NOT `src[start:src.index(token, start)]`. Bounding a window with the very
    token you are asserting is present is circular: delete the token and the window simply
    grows until it finds the next one somewhere else. That is precisely how the previous
    revision of this file passed with the DEBUG gate's `return` deleted — it stretched into
    the DSN guard below and found *its* `return` — and the first attempt at this revision
    reproduced the same bug. Brace matching cannot do that.
    """
    start = src.index(opener) + len(opener) - 1
    assert src[start] == "{", f"{opener!r} must end at its opening brace"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {opener!r}")


def _enclosing_directives(src: str, needle: str) -> list[str]:
    """The `#if` conditions in effect at the line containing `needle`, outermost first."""
    target = src[:src.index(needle)].count("\n")
    stack: list[str] = []
    for lineno, line in enumerate(src.splitlines()):
        if lineno >= target:
            break
        stripped = line.strip()
        if stripped.startswith("#if "):
            stack.append(stripped[4:].strip())
        elif stripped.startswith("#else"):
            if stack:
                stack[-1] = f"!({stack[-1]})"
        elif stripped.startswith("#endif"):
            if stack:
                stack.pop()
    return stack


def test_debug_builds_return_before_starting_sentry():
    fn = _start_fn(_code_only(_source()))
    gate_at = fn.index("guard !isDebugBuild else {")
    assert gate_at < fn.index("SentrySDK.start"), "the gate must precede SentrySDK.start"

    # Brace-matched to the guard's OWN body, so deleting the `return` cannot be masked by the
    # DSN guard's `return` further down. See `_braced_block`.
    body = _braced_block(fn, "guard !isDebugBuild else {")
    assert re.search(r"^\s*return\s*$", body, re.M), (
        "the gate must return — logging alone still starts Sentry below"
    )


def test_the_gate_flag_is_not_inverted():
    """`isDebugBuild = false` under `#if DEBUG` would silently invert the whole feature:
    Sentry off in Release (no production reporting at all) and on in Debug (the original
    noise). Nothing else in this file would look wrong."""
    src = _code_only(_source())
    decl = src[src.index("#if DEBUG"):src.index("func startErrorMonitoring()")]
    debug_arm, _, release_arm = decl.partition("#else")
    assert "isDebugBuild = true" in debug_arm, "#if DEBUG arm must set isDebugBuild = true"
    assert "isDebugBuild = false" in release_arm, "#else arm must set isDebugBuild = false"


def test_the_sentry_block_is_still_compiled_in_debug():
    """The regression this file's own fix was reacting to.

    `sentry-cocoa` is a linked SPM dependency, so `SentrySDK.start`, the `beforeSend` closure
    and `event.exceptions` are real code. Wrapping them in the `#else` of an `#if DEBUG`
    removes them from every dev build's type-checking, and no CI job compiles Release Swift —
    so an SDK API break would first appear at archive/TestFlight time.
    """
    enclosing = _enclosing_directives(_code_only(_source()), "SentrySDK.start")
    assert enclosing == ["canImport(Sentry)"], (
        f"SentrySDK.start must be guarded ONLY by canImport(Sentry); found {enclosing}. "
        "A DEBUG-conditional around it stops the integration being type-checked in Debug."
    )


def test_the_dsn_is_still_present_so_release_builds_do_report():
    """Anti-vacuity. Emptying the DSN would also stop the noise — and would silently stop
    production reporting, which is the opposite of what we want."""
    src = _source()
    m = re.search(r'static let sentryDSN = "([^"]*)"', src)
    assert m, "sentryDSN declaration not found"
    assert m.group(1).startswith("https://"), (
        "the DSN was blanked — Release/TestFlight builds would stop reporting entirely"
    )


def test_redaction_and_pii_posture_are_untouched():
    """The gate must not have disturbed the surrounding privacy guarantees."""
    src = _source()
    assert "options.sendDefaultPii = false" in src
    assert "options.beforeSend" in src
    assert "MonitoringConfig.redact" in src


def test_no_unconditional_print_ships_in_release():
    """`.claude/rules/backend-python.md` and the iOS rules both ban `print()` in production
    code. The gate's own log must stay inside `#if DEBUG` even though the branch is only
    reached in Debug — otherwise the call is still compiled into the Release binary."""
    src = _code_only(_source())
    for match in re.finditer(r'^\s*print\(', src, re.M):
        enclosing = _enclosing_directives(src, src[match.start():match.end()])
        assert any("DEBUG" in cond and not cond.startswith("!") for cond in enclosing), (
            f"a print() at offset {match.start()} is not inside #if DEBUG"
        )
