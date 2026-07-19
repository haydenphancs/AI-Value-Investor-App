"""Guard: the test suite must NEVER initialize Sentry.

A live Sentry client under pytest ships every test ``logger.error(...)`` to the
PROD project (caydex / python-fastapi) via the LoggingIntegration — that is how
the synthetic ``user-123`` / ``boom IndA`` / ``report r1`` issues appeared in the
triage digest. The root ``conftest.py`` prevents it by forcing ``SENTRY_DSN``
empty before ``app`` is imported. If that regresses, these tests fail loudly.
"""
from __future__ import annotations

import sentry_sdk

from app.config import settings


def test_sentry_dsn_is_neutralized_for_tests():
    # conftest.py forces this empty regardless of what's in backend/.env.
    assert not settings.SENTRY_DSN, (
        "SENTRY_DSN must be empty during tests — see backend/conftest.py. A "
        "non-empty DSN ships every test logger.error to the prod Sentry project."
    )


def test_importing_app_main_does_not_activate_sentry():
    import app.main  # noqa: F401 — importing runs the guarded sentry_sdk.init block

    assert not sentry_sdk.get_client().is_active(), (
        "Sentry was initialized during tests — test logger.error events would be "
        "shipped to prod. Ensure conftest.py neutralizes SENTRY_DSN."
    )
