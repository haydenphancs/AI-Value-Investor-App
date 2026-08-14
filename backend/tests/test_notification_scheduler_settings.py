"""Every setting the notification scheduler reads must actually exist.

WHY THIS FILE EXISTS. `_run_scheduled_notification_senders` built its `senders` tuple
from `settings.EARNINGS_NOTIFY_HOUR_ET` / `SMART_MONEY_NOTIFY_HOUR_ET` — neither of which
was ever DEFINED on `Settings`, which uses `extra="ignore"`. The tuple is constructed
BEFORE the `while True` and outside any `try`, so the task raised AttributeError ~90
seconds after every startup and both daily senders never ran. Nothing failed loudly: the
API was healthy, the endpoints worked, and the only trace was one task-crash log.

An import check would not have caught it (the names are read at call time, inside an
async task), and no test exercised the loop. So this asserts the contract directly:
whatever attribute names that function reads off `settings`, `Settings` must define.
"""

import ast
import inspect
import textwrap

import app.main as main_mod
from app.config import settings

_SCHEDULER = "_run_scheduled_notification_senders"


def _settings_attrs_read_by(func_name: str) -> set[str]:
    """Every `settings.X` attribute referenced inside a function, via AST."""
    src = textwrap.dedent(inspect.getsource(getattr(main_mod, func_name)))
    found: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"):
            found.add(node.attr)
    return found


def test_the_scan_found_the_scheduler():
    """Guard against the guard: a renamed function would make this pass vacuously."""
    assert hasattr(main_mod, _SCHEDULER), f"{_SCHEDULER} not found in app.main"
    assert _settings_attrs_read_by(_SCHEDULER), "no settings.* reads found — scan drifted"


def test_every_setting_the_scheduler_reads_is_defined():
    missing = sorted(
        name for name in _settings_attrs_read_by(_SCHEDULER)
        if not hasattr(settings, name)
    )
    assert not missing, (
        f"{_SCHEDULER} reads settings that Settings does not define: {missing}. "
        f"`extra=\"ignore\"` means this raises AttributeError at runtime, and the tuple "
        f"is built outside any try — the whole sender task dies silently."
    )


def test_notify_hours_are_plausible_et_hours():
    for name in ("EARNINGS_NOTIFY_HOUR_ET", "SMART_MONEY_NOTIFY_HOUR_ET",
                 "PROFILE_MATCH_NOTIFY_HOUR_ET"):
        value = getattr(settings, name)
        assert isinstance(value, int) and 0 <= value <= 23, f"{name}={value!r}"


def test_earnings_runs_after_the_close_and_derived_alerts_run_last():
    """Ordering is a product decision: the specific alert should land before the one
    derived from it, so the per-category caps suppress the derived one rather than the
    other way round."""
    assert settings.EARNINGS_NOTIFY_HOUR_ET >= 16, "earnings should run after the close"
    assert settings.PROFILE_MATCH_NOTIFY_HOUR_ET >= settings.SMART_MONEY_NOTIFY_HOUR_ET
