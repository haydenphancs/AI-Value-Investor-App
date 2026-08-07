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

    These defaults exist only to mirror what iOS declares. If someone flips a key to ship ON
    there, this map keeps suppressing it and the bug is invisible from the Python side.

    The source moved on 2026-08-07. It used to be the `@AppStorage` declarations, but twelve of
    the thirteen toggles were hidden (only `notify_watchlist_changes` has a sender behind it —
    see `FeatureFlags.notificationPreferencesEnabled`) and their declarations went with the UI.
    The keys did NOT stop existing: SettingsSyncManager still syncs them and
    `preference_enabled` still reads them, so the defaults are now declared explicitly in
    `NotificationsSettingsView.preferenceDefaults`.
    """
    import re
    from pathlib import Path

    view = (Path(__file__).resolve().parents[2]
            / "frontend/ios/ios/Views/Screens/NotificationsSettingsView.swift")
    assert view.exists(), f"missing {view}"
    src = view.read_text()

    block_start = src.find("static let preferenceDefaults")
    assert block_start != -1, (
        "NotificationsSettingsView.preferenceDefaults is gone — it is the declared source of "
        "truth for every notification preference default, including the hidden ones"
    )
    # Slice from the literal's OPENING bracket, not from the declaration: the type annotation
    # `[String: Bool]` contains a `]` that would truncate the block to nothing.
    open_at = src.index("= [", block_start)
    block = src[open_at: src.index("\n    ]", open_at)]

    declared = dict(re.findall(r'"(notify_\w+)"\s*:\s*(true|false)', block))
    # Anti-vacuity: the regex must actually be finding the declarations.
    assert len(declared) >= 10, declared

    # Every key the client SYNCS must have a declared default, or a hidden key silently
    # inherits the blanket True on the backend.
    sync = (Path(__file__).resolve().parents[2]
            / "frontend/ios/ios/Core/Services/SettingsSyncManager.swift")
    synced = set(re.findall(r'"(notify_\w+)"', sync.read_text()))
    assert synced, "SettingsSyncManager declares no notify_* keys — regex drifted"
    assert synced <= set(declared), (
        f"synced but undeclared: {sorted(synced - set(declared))} — add them to "
        f"NotificationsSettingsView.preferenceDefaults"
    )

    off_in_ios = {k for k, v in declared.items() if v == "false"}
    assert off_in_ios == set(_OFF_BY_DEFAULT), (
        f"iOS ships these OFF: {sorted(off_in_ios)}; "
        f"PushDispatchService._PREFERENCE_DEFAULTS covers: {sorted(_OFF_BY_DEFAULT)}"
    )
    assert set(PushDispatchService._PREFERENCE_DEFAULTS) == off_in_ios


def test_only_wired_preference_keys_have_visible_toggles():
    """A visible toggle must do what it says. `notify_watchlist_changes` is the only key passed
    as a `preference_key=` to a real dispatcher, so it is the only one that may be rendered.

    This is the invariant `FeatureFlags.notificationPreferencesEnabled` was created to protect,
    and it was violated in the other direction for a week: push shipped on 2026-08-01, the flag
    stayed false, and users got alerts with no in-app way to opt out.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    backend = (repo / "backend/app/services").rglob("*.py")
    wired = set()
    for path in backend:
        wired |= set(re.findall(r'preference_key\s*=\s*"(notify_\w+)"', path.read_text()))
    assert wired, "no preference_key= call sites found — regex drifted"

    view = (repo / "frontend/ios/ios/Views/Screens/NotificationsSettingsView.swift").read_text()
    body = view[view.index("var body: some View"):]
    rendered = set(re.findall(r'isOn:\s*\$(\w+)', body))
    storage = dict(re.findall(r'@AppStorage\("(notify_\w+)"\)\s*private var (\w+)', view))
    rendered_keys = {k for k, var in storage.items() if var in rendered}

    assert rendered_keys, "no rendered toggles found — regex drifted"
    assert rendered_keys <= wired, (
        f"these toggles are visible but nothing sends them: {sorted(rendered_keys - wired)}. "
        f"A control that stores a preference nothing reads tells the user something is "
        f"happening when nothing is — wire a sender first, or hide the row."
    )
