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

import pytest

# Force Sentry inert for tests regardless of what backend/.env contains.
os.environ["SENTRY_DSN"] = ""

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

