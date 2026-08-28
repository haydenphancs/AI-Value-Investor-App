"""Root pytest configuration for the Caydex backend suite.

Neutralize Sentry for the ENTIRE test session, before any ``app.*`` module is
imported.

Why this exists: ``SENTRY_DSN`` is set in the local ``backend/.env`` (so the dev
server and prod capture work). But importing ``app.main`` calls
``sentry_sdk.init()`` whenever a DSN is present, and its ``LoggingIntegration``
turns every ``logger.error(...)`` — including the ones tests trigger ON PURPOSE
(``user-123`` / ``report r1`` / ``boom IndA`` / ``test_industry_benchmark_*`` …) —
into a real event shipped to the PROD Sentry project (caydex / python-fastapi).
That was polluting the triage digest with dozens of synthetic issues.

pytest imports this rootdir ``conftest.py`` before collecting any test module, so
forcing ``SENTRY_DSN`` empty here guarantees ``settings.SENTRY_DSN`` is falsy →
the guarded init block in ``app.main`` is a complete no-op during tests. An
explicit empty string (not ``pop``) is required: environment variables win over
the ``.env`` file in pydantic-settings precedence, but only when the key is
actually present, so we must set it — deleting it would let ``.env`` win.

The guard is verified by ``tests/test_sentry_inert_in_tests.py`` (fails loudly if
Sentry ever activates under pytest).
"""

import gc
import os
import socket

import pytest

# Force Sentry inert for tests regardless of what backend/.env contains.
os.environ["SENTRY_DSN"] = ""

# ---------------------------------------------------------------------------
# Block outbound network for the whole session.
#
# `.claude/rules/testing.md` forbids hitting live FMP / Gemini / CoinGecko / FRED /
# Supabase from the suite, but nothing ENFORCED it — and the suite was quietly ignoring
# it. Measured by denying `getaddrinfo` and counting attempts: **214 live calls to the
# production Supabase project per run**, from three places:
#
#   * 204 from test_ticker_report_schema_parity.py, whose fixtures drive the real
#     collector into `sector_benchmark_lookup` — so the backend↔iOS contract guard, the
#     one whose failure means a decode crash in production, was asserting against
#     whatever rows prod happened to hold, and retrying each call through
#     `retry_idempotent_sync` (roughly half the suite's wall clock).
#   * 6 from test_chat_credits.py reaching the real `claim_free_followup` — a
#     SECURITY DEFINER function with `row_security = off` that UPDATEs `chat_sessions`.
#     It was harmless ONLY because `session_id="sess-1"` fails the UUID cast, so the RPC
#     errored and the code fell back to "charge". One `"sess-1"` → real-UUID edit away
#     from mutating production, and meanwhile those money-path assertions were only ever
#     exercising the error branch.
#   * a handful from a home_dashboard_service teardown path.
#
# Every one of those call sites is fail-safe, so the tests passed either way. That is
# what made this invisible: a live call that succeeds and a live call that is blocked
# both produce a green suite, and only the FIRST one couples the result to prod data.
#
# Loopback stays open (nothing needs it today, but blocking it would break any future
# local fixture server for no benefit). The error names the host so a new offender is
# immediately diagnosable rather than appearing as a generic DNS failure.
#
# Verified by `tests/test_no_network_in_tests.py`. Proven safe BEFORE it was written:
# the full suite already passed with all outbound sockets denied (7,917 passed).
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "", None}

# Reserved-for-documentation addresses (RFC 5737 / RFC 2606) that `tests/
# test_no_network_in_tests.py` probes on purpose to prove the block still bites. They
# must still RAISE — that is what the guard's own tests assert — but must not be counted,
# or the guard would fail every run because of its own verification.
_SELFTEST_HOSTS = {"guard-selftest.invalid", "192.0.2.1"}
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect


class NetworkCallInTests(RuntimeError):
    """Raised when a test tries to reach the network.

    It is a `RuntimeError`, so the fail-safe `except Exception` blocks that wrap most
    upstream calls DO still swallow it. That is deliberate — the point is to stop the
    packet, not to break a degradation path — but it means raising ALONE is not enough
    to make a new offender visible: swallowed is exactly how the original 214 hid. So
    every block is also RECORDED, and `pytest_sessionfinish` fails the run on any
    non-empty record. Raise for the packet, count for the visibility.
    """


#: (host, test-id) of every blocked attempt. Read by pytest_sessionfinish.
#: The test id comes from PYTEST_CURRENT_TEST, which pytest maintains per test — without
#: it a violation reports only a hostname, and the offender can be in any of 330 files
#: (worse, it is often ORDER-DEPENDENT: a singleton built by one file, used by another).
BLOCKED_NETWORK_CALLS: list[tuple[str, str]] = []


def _current_test() -> str:
    return os.environ.get("PYTEST_CURRENT_TEST", "(outside a test)").split(" (")[0]


def _blocked_getaddrinfo(host, port, *args, **kwargs):
    if host in _ALLOWED_HOSTS:
        return _real_getaddrinfo(host, port, *args, **kwargs)
    if str(host) not in _SELFTEST_HOSTS:
        BLOCKED_NETWORK_CALLS.append((str(host), _current_test()))
    raise NetworkCallInTests(
        f"outbound network call to {host!r}:{port} from the test suite. "
        f"Stub the client/service instead — see .claude/rules/testing.md."
    )


def _blocked_connect(self, address, *args, **kwargs):
    host = address[0] if isinstance(address, tuple) else address
    if host in _ALLOWED_HOSTS:
        return _real_connect(self, address, *args, **kwargs)
    if str(host) not in _SELFTEST_HOSTS:
        BLOCKED_NETWORK_CALLS.append((str(host), "connect"))
    raise NetworkCallInTests(
        f"outbound socket connect to {host!r} from the test suite. "
        f"Stub the client/service instead — see .claude/rules/testing.md."
    )


socket.getaddrinfo = _blocked_getaddrinfo
socket.socket.connect = _blocked_connect

# ---------------------------------------------------------------------------
# Stop the cyclic collector for the session.
#
# 26 test files are SOURCE SCANS built on `ast.parse` — the auth-policy matrix, the
# undefined-globals detector, the iOS parity guards, the integration-teardown check. CPython
# tracks AST recursion depth in a counter that is not reentrancy-safe, so a collection landing
# inside the C-level AST construction raises
#
#     SystemError: AST constructor recursion depth mismatch (before=123, after=173)
#
# and fails whichever scan happened to be parsing at that instant. It is a TIMING artefact of
# total allocation across the session, not a defect in the module under test:
#
#   * Adding one unrelated test file elsewhere in the suite was enough to make it appear — that
#     is exactly how it was found (tests/test_money_moves_catalog_parity.py, six-and-a-half
#     thousand tests later in collection order than the test it broke).
#   * Guarding a single call site only moved it. With `_needs_teardown()` in
#     test_integration_client_teardown.py protected, the very next run failed in
#     test_notification_scheduler_settings.py instead.
#   * It is intermittent — roughly one run in three — so a green run proves nothing. It has been
#     mistaken for a real failure twice, and the project memory records it as "a symptom, not the
#     bug".
#
# Reference counting still frees everything acyclic, which is nearly all of it, and the process
# is short-lived, so only reference cycles accumulate. Measured across the full suite:
# peak RSS 435 MB → 529 MB (+21%) and wall clock 39.7s → 35.1s. Nothing in `app/` or `tests/`
# defines `__del__`, uses `weakref`, or calls `gc.collect()`, so no test depends on collection
# happening.
#
# Verified by `tests/test_gc_is_disabled_for_the_session.py`. If that ever needs to be reverted,
# the alternative is wrapping EVERY `ast.parse` in the suite, not just the one that broke last.
gc.disable()


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook signature
    """Collect the cycles we deferred, BEFORE the terminal summary is written.

    `gc.disable()` above means reference cycles survive the run. A handful of them are asyncio
    Futures carrying an exception nothing awaited (deliberate failure-path tests in
    test_widget_inflight_behaviour.py and friends). Left to interpreter shutdown, their
    "Future exception was never retrieved" tracebacks print AFTER pytest's summary line — so
    `pytest -q | tail -1`, which is how CLAUDE.md and .claude/rules/testing.md tell you to read
    a run, showed `RuntimeError: x` instead of the pass/fail count.

    Running the collection here puts those messages back in their normal place, above the
    summary, and keeps the tail meaningful. `tryfirst` so this beats the terminal reporter's
    own sessionfinish.
    """
    gc.enable()
    gc.collect()

    # A blocked call is swallowed by app-level `except Exception`, so the run can be
    # GREEN while a test still tried to reach production. Counting is the only thing
    # that makes a new offender visible — raising is not.
    if BLOCKED_NETWORK_CALLS:
        hosts = sorted({host for host, _ in BLOCKED_NETWORK_CALLS})
        culprits = sorted({test for _, test in BLOCKED_NETWORK_CALLS})
        print(
            "\n"
            f"NETWORK: {len(BLOCKED_NETWORK_CALLS)} outbound call(s) were attempted and "
            f"blocked during this run: {', '.join(hosts)}.\n"
            "  from: " + "\n        ".join(culprits) + "\n"
            "The suite must be hermetic (.claude/rules/testing.md). These were stopped, "
            "so nothing reached production — but the test that made them is exercising a "
            "degraded path, not the one it means to. Stub the service at the binding the "
            "caller actually uses.\n"
            "If a test above is clean when run ALONE, the cause is a module SINGLETON "
            "that captured a real client in __init__ during an earlier test — reset the "
            "singleton, not just the factory."
        )
        session.exitstatus = 1

