"""`preference_enabled` must TYPE the stored value, not truthiness-cast it.

`bool("false")` is True. Nothing between the client and the JSONB column enforces a
type, so a string-typed toggle — from another client, a hand-edited row, or a future
client bug — silently RE-ENABLED a notification the user had turned off. Unwanted
pushes are the exact class of failure push_dispatch_service exists to prevent.
"""

import pytest

from app.services.push_dispatch_service import PushDispatchService


def _service_returning(preferences):
    svc = PushDispatchService.__new__(PushDispatchService)

    class _Rows:
        data = [{"preferences": preferences}] if preferences is not None else []
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self): return self

    class _Supa:
        def table(self, *_a, **_k): return _Rows()

    svc.supabase = _Supa()
    return svc


@pytest.mark.parametrize("stored", [False, "false", "False", " FALSE ", "0", "no", "off", "", 0, 0.0])
def test_opted_out_values_are_respected(stored):
    """Every one of these means OFF. `bool()` said True for all but the first."""
    svc = _service_returning({"notify_watchlist_changes": stored})
    assert svc.preference_enabled("u1", "notify_watchlist_changes") is False


@pytest.mark.parametrize("stored", [True, "true", "yes", "1", 1, 2.5])
def test_opted_in_values_are_respected(stored):
    svc = _service_returning({"notify_watchlist_changes": stored})
    assert svc.preference_enabled("u1", "notify_watchlist_changes") is True


def test_absent_key_defaults_to_opted_in():
    """Missing means the user never expressed a preference — the documented default."""
    svc = _service_returning({})
    assert svc.preference_enabled("u1", "notify_watchlist_changes") is True


def test_absent_row_defaults_to_opted_in():
    svc = _service_returning(None)
    assert svc.preference_enabled("u1", "notify_watchlist_changes") is True


def test_unreadable_type_defaults_to_opted_in_and_does_not_raise():
    """A non-scalar can't reach here through the API, but it must not crash the sweeper."""
    svc = _service_returning({"notify_watchlist_changes": {"nested": 1}})
    assert svc.preference_enabled("u1", "notify_watchlist_changes") is True


# ── Per-key defaults for an ABSENT preference ────────────────────────────────
#
# A blanket `True` for a missing key was wrong for exactly two toggles, and latent
# rather than harmless: the iOS `currentBlob()` OMITS any key the user never touched,
# so a user who never opened Notification Settings has no row entry for these — and
# both render as OFF in the UI. The first volatility/institutional dispatcher to ship
# would have opted them into something the app shows as off.

_OFF_BY_DEFAULT = ["notify_market_volatility", "notify_smart_money_institutional"]


@pytest.mark.parametrize("key", _OFF_BY_DEFAULT)
@pytest.mark.parametrize("row", [None, {}, {"unrelated_key": True}])
def test_toggles_that_ship_off_default_off_when_absent(key, row):
    """No row, empty blob, and a blob without this key must all read as OFF."""
    assert _service_returning(row).preference_enabled("u", key) is False


@pytest.mark.parametrize("key", _OFF_BY_DEFAULT)
def test_an_explicit_opt_in_still_wins(key):
    """The default only applies when the key is ABSENT — a real True must be honoured."""
    assert _service_returning({key: True}).preference_enabled("u", key) is True


@pytest.mark.parametrize("key", [
    "notify_watchlist_changes", "notify_earnings_alerts", "notify_research_complete",
])
def test_opt_out_style_toggles_still_default_on(key):
    """Anti-vacuity: a map that turned EVERYTHING off would pass the tests above."""
    assert _service_returning({}).preference_enabled("u", key) is True


@pytest.mark.parametrize("key", _OFF_BY_DEFAULT + ["notify_watchlist_changes"])
def test_a_failed_read_falls_back_to_the_same_default(key):
    """A DB blip must not invent an opt-in the user never gave.

    The read-failure path returned a hardcoded True, so a transient error opted an
    off-by-default user IN — the one direction that cannot be taken back.
    """
    from app.services.push_dispatch_service import PushDispatchService

    svc = PushDispatchService.__new__(PushDispatchService)

    class _Boom:
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self): raise RuntimeError("postgrest exploded")

    class _Supa:
        def table(self, *_a, **_k): return _Boom()

    svc.supabase = _Supa()
    expected = key not in _OFF_BY_DEFAULT
    assert svc.preference_enabled("u", key) is expected


@pytest.mark.parametrize("key", _OFF_BY_DEFAULT)
def test_an_unreadable_type_falls_back_to_the_same_default(key):
    """An unreadable value is effectively a missing one, and must answer the same."""
    assert _service_returning({key: {"nested": 1}}).preference_enabled("u", key) is False


def test_the_ios_defaults_this_map_mirrors_still_say_off():
    """Pin the map to its SOURCE, or the two drift silently.

    These defaults exist only to mirror the `@AppStorage` declarations in
    NotificationsSettingsView.swift. If someone flips a toggle to ship ON there, this
    map keeps suppressing it and the bug is invisible from the Python side.
    """
    import re
    from pathlib import Path

    view = (Path(__file__).resolve().parents[2]
            / "frontend/ios/ios/Views/Screens/NotificationsSettingsView.swift")
    assert view.exists(), f"missing {view}"
    src = view.read_text()

    declared = dict(re.findall(
        r'@AppStorage\("(notify_\w+)"\)[^=\n]*=\s*(true|false)', src))
    # Anti-vacuity: the regex must actually be finding the declarations.
    assert len(declared) >= 10, declared

    off_in_ios = {k for k, v in declared.items() if v == "false"}
    assert off_in_ios == set(_OFF_BY_DEFAULT), (
        f"iOS ships these OFF: {sorted(off_in_ios)}; "
        f"PushDispatchService._PREFERENCE_DEFAULTS covers: {sorted(_OFF_BY_DEFAULT)}"
    )
    assert set(PushDispatchService._PREFERENCE_DEFAULTS) == off_in_ios
