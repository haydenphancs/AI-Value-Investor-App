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

import os

# Force Sentry inert for tests regardless of what backend/.env contains.
os.environ["SENTRY_DSN"] = ""
