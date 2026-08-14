"""Every `settings.X` read ANYWHERE under `app/` must be defined on `Settings`.

WHY THIS FILE EXISTS — it has now happened twice, the same way both times.

`Settings` sets `extra="ignore"`, so an undeclared name is not a config error: the env var
is silently dropped and `settings.X` raises `AttributeError` at call time. When that read
sits in a background loop BEFORE its `while True` (or outside any `try`), the task dies
seconds after boot, nothing restarts it, and the API stays healthy — so the feature is
simply absent in production with no failing request to point at.

1. `_run_scheduled_notification_senders` read `EARNINGS_NOTIFY_HOUR_ET` /
   `SMART_MONEY_NOTIFY_HOUR_ET`, neither defined. Both daily senders never ran.
   `test_notification_scheduler_settings.py` was written to stop exactly this.
2. It did not. That guard is scoped to ONE function in ONE module, so it never saw
   `run_price_alert_loop` reading four undefined `PRICE_ALERT_*` names — killing the loop
   at `price_alert_service.py`'s interval read and 503-ing all four /alerts/price routes
   for every user, indefinitely.

A guard narrower than the bug class does not close it. This one sweeps the whole package.

An import check cannot catch any of this: the names are read at call time inside async
tasks, so the module imports cleanly and the failure only exists at runtime.
"""

import ast
from pathlib import Path

import pytest

import app as app_pkg
from app.config import settings

APP_ROOT = Path(app_pkg.__file__).resolve().parent

# `settings` is a module-level singleton everywhere in this codebase. If a function ever
# shadows the name with a local, add it here with a comment rather than weakening the scan.
_SHADOWED: set[str] = set()


def _iter_settings_reads():
    """Yield (attribute_name, relative_path, lineno) for every `settings.X` under app/."""
    for path in sorted(APP_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:  # a broken file is its own failure; don't mask it here
            pytest.fail(f"{path.relative_to(APP_ROOT.parent)} does not parse: {e}")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"
                and node.attr not in _SHADOWED
            ):
                yield node.attr, str(path.relative_to(APP_ROOT.parent)), node.lineno


def test_every_settings_attribute_read_under_app_is_defined():
    missing: dict[str, list[str]] = {}
    for attr, rel_path, lineno in _iter_settings_reads():
        if not hasattr(settings, attr):
            missing.setdefault(attr, []).append(f"{rel_path}:{lineno}")

    assert not missing, (
        "These `settings.X` names are read but NOT defined on Settings:\n"
        + "\n".join(
            f"  settings.{attr} — {', '.join(sites)}" for attr, sites in sorted(missing.items())
        )
        + '\n\n`extra="ignore"` means a Railway variable CANNOT supply them: the read raises '
        "AttributeError. If the read sits outside a try (or before a loop's `while True`), "
        "the whole task dies silently at boot and the feature is absent in production."
    )


# ── Guards against the guard ────────────────────────────────────────────────────
# A source scan that silently stops matching passes forever. These pin that it is
# actually looking at the codebase, and that it would still fail on a real regression.


def test_the_sweep_is_not_vacuous():
    reads = list(_iter_settings_reads())
    files = {rel for _, rel, _ in reads}
    attrs = {attr for attr, _, _ in reads}

    assert len(files) >= 20, f"only {len(files)} files contain settings reads — scan drifted"
    assert len(attrs) >= 50, f"only {len(attrs)} distinct settings read — scan drifted"


def test_the_sweep_would_catch_a_missing_name():
    """Mutation test: the assertion must actually fire on an undefined name."""
    assert not hasattr(settings, "DEFINITELY_NOT_A_REAL_SETTING")
    bogus = [
        attr for attr in ("DEFINITELY_NOT_A_REAL_SETTING",) if not hasattr(settings, attr)
    ]
    assert bogus, "hasattr-based detection is broken — the real test would pass vacuously"


def test_the_regressions_this_file_exists_for_stay_fixed():
    """The two specific outages, pinned by name so a revert is a red test."""
    for name in (
        # Outage 1 — daily notification senders
        "EARNINGS_NOTIFY_HOUR_ET",
        "SMART_MONEY_NOTIFY_HOUR_ET",
        "PROFILE_MATCH_NOTIFY_HOUR_ET",
        # Outage 2 — price alerts
        "PRICE_ALERT_INTERVAL_SECONDS",
        "PRICE_ALERT_MAX_PER_USER",
        "PRICE_ALERT_MAX_PER_TICKER_PER_USER",
        "PRICE_ALERT_REARM_PCT",
    ):
        assert hasattr(settings, name), f"Settings no longer defines {name}"


def test_price_alert_settings_are_sane():
    """Values a bad default would make dangerous rather than merely wrong."""
    # The loop floors this at 15s; a 0 or negative default would spin the sweeper.
    assert settings.PRICE_ALERT_INTERVAL_SECONDS >= 15
    assert settings.PRICE_ALERT_MAX_PER_USER > 0
    assert settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER > 0
    # Per-ticker cap must not exceed the per-user cap, or it can never bind.
    assert settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER <= settings.PRICE_ALERT_MAX_PER_USER
    # A 0 re-arm band notifies every cycle while price oscillates across the threshold;
    # a band >= 1.0 (100%) can never re-arm.
    assert 0 < settings.PRICE_ALERT_REARM_PCT < 1.0


def test_price_alert_caps_match_the_schema_defaults_ios_falls_back_to():
    """iOS shows the schema defaults until its first successful fetch. If these drift, a
    user sees one cap in the UI and hits a different one on POST."""
    from app.schemas.price_alerts import PriceAlertListResponse

    defaults = PriceAlertListResponse()
    assert defaults.max_per_user == settings.PRICE_ALERT_MAX_PER_USER
    assert defaults.max_per_ticker == settings.PRICE_ALERT_MAX_PER_TICKER_PER_USER
