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


def test_sentry_init_suppresses_request_bodies():
    """Source-scan, because the init block is inert under tests and cannot be introspected.

    `send_default_pii=False` gates COOKIES ONLY. sentry-sdk 2.20's Starlette integration
    attaches the parsed JSON body to every event unconditionally
    (`request_info["data"] = json`), so without `max_request_body_size="never"` a failed
    sign-up ships the user's plaintext password — and a failed reset ships the 6-digit code —
    to a third-party store. Production-only, so no local run can catch it.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
    init = src[src.index("sentry_sdk.init("):]
    init = init[: init.index("\n    )")]

    assert 'max_request_body_size="never"' in init, (
        "sentry_sdk.init no longer suppresses request bodies — credential-endpoint payloads "
        "(password, reset code, refresh token) will reach Sentry in the clear"
    )
    assert "send_default_pii=False" in init, "send_default_pii must stay False"
