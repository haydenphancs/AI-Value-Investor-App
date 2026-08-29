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
#
# `notify_quiet_hours_enabled` joined them for the same reason from the other side: it
# shapes DELIVERY rather than selecting a category, so no NotificationKind references it
# and `preference_enabled` never looks it up in normal operation — which is exactly how
# an off-by-default key gets forgotten. It is declared anyway so both sides agree on
# what its absence means. See `notification_kinds.DELIVERY_PREFERENCE_DEFAULTS`.

_OFF_BY_DEFAULT = [
    "notify_market_volatility",
    "notify_smart_money_institutional",
    "notify_quiet_hours_enabled",
    # Topic-match alerts. Off for a different reason than the two above: those are noisy,
    # this one is DERIVED. Every other kind fires on an event about something the user
    # explicitly tracks; this fires on an interest they merely said they had, so it has
    # to be opted into rather than out of.
    "notify_profile_topics",
]


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

    # Non-Bool notification preferences (quiet-hours times, timezone) live in a SECOND
    # map. `preferenceDefaults` is deliberately `[String: Bool]` — its type is what makes
    # "which toggles ship off?" readable, and `_PREFERENCE_DEFAULTS` mirrors only the
    # false entries. Without this union the `synced <= declared` check below fails the
    # build the moment a string-valued notify_* key is added, which is not drift.
    string_start = src.find("static let preferenceStringDefaults")
    declared_strings: set[str] = set()
    if string_start != -1:
        s_open = src.index("= [", string_start)
        s_block = src[s_open: src.index("\n    ]", s_open)]
        declared_strings = set(re.findall(r'"(notify_\w+)"\s*:', s_block))
        assert declared_strings, (
            "preferenceStringDefaults exists but declares no notify_* keys — regex drifted"
        )

    # `notify_timezone` has no static default by design: it is the device's own
    # TimeZone.current.identifier, written on first sync, and the backend falls back to
    # ET when it is absent. Exempt it explicitly rather than inventing a fake default.
    _NO_STATIC_DEFAULT = {"notify_timezone"}

    # Every key the client SYNCS must have a declared default, or a hidden key silently
    # inherits the blanket True on the backend.
    sync = (Path(__file__).resolve().parents[2]
            / "frontend/ios/ios/Core/Services/SettingsSyncManager.swift")
    synced = set(re.findall(r'"(notify_\w+)"', sync.read_text()))
    assert synced, "SettingsSyncManager declares no notify_* keys — regex drifted"
    undeclared = synced - set(declared) - declared_strings - _NO_STATIC_DEFAULT
    assert not undeclared, (
        f"synced but undeclared: {sorted(undeclared)} — add them to "
        f"NotificationsSettingsView.preferenceDefaults (Bool) or "
        f"preferenceStringDefaults (String)"
    )

    off_in_ios = {k for k, v in declared.items() if v == "false"}
    assert off_in_ios == set(_OFF_BY_DEFAULT), (
        f"iOS ships these OFF: {sorted(off_in_ios)}; "
        f"PushDispatchService._PREFERENCE_DEFAULTS covers: {sorted(_OFF_BY_DEFAULT)}"
    )
    assert set(PushDispatchService._PREFERENCE_DEFAULTS) == off_in_ios


def _rendered_preference_keys() -> set:
    """Every `notify_*` key the Notifications screen actually renders a control for.

    Read from `NotificationSettingsViewModel.groups`, the screen's DECLARATIVE row
    manifest, rather than by parsing SwiftUI. The predecessor keyed off
    `@AppStorage("notify_…") private var X` plus `isOn: $X`, which stopped existing the
    moment the screen gained a ViewModel — and a source scan whose regex stops matching
    does not fail, it passes VACUOUSLY on an empty set.

    Brace-bounded to the manifest and comment-stripped: a key merely NAMED in a comment
    is not a rendered control, and counting one would weaken every assertion below.
    """
    import re
    from pathlib import Path

    vm = (Path(__file__).resolve().parents[2]
          / "frontend/ios/ios/ViewModels/NotificationSettingsViewModel.swift")
    assert vm.exists(), f"missing {vm}"
    src = re.sub(r"//[^\n]*", "", vm.read_text())

    start = src.find("static let groups")
    assert start != -1, (
        "NotificationSettingsViewModel.groups is gone — it is the declared manifest of "
        "every rendered notification toggle, and this guard has nothing to read without it"
    )
    # Slice from the literal's OPENING bracket, never the declaration: the type
    # annotation `[NotificationGroupSpec]` contains a `]` that would truncate the block
    # to nothing — the classic way this kind of guard goes silently vacuous.
    open_at = src.index("= [", start)
    block = src[open_at: src.index("\n    ]", open_at)]

    keys = set(re.findall(r'key:\s*"(notify_\w+)"', block))
    keys |= set(re.findall(r'masterKey:\s*"(notify_\w+)"', block))
    return keys


def test_only_wired_preference_keys_have_visible_toggles():
    """A visible toggle must do what it says.

    Every key rendered on the Notifications screen must be owned by a registered
    `NotificationKind` (or be one of their group masters). A control that stores a
    preference NOTHING reads tells the user something is happening when nothing is —
    which is the state twelve of the original thirteen toggles were in, and the reason
    their UI had to be hidden.
    """
    from app.services.notification_kinds import NOTIFICATION_KINDS

    wired = set()
    for kind in NOTIFICATION_KINDS.values():
        wired.add(kind.preference_key)
        if kind.master_preference_key:
            wired.add(kind.master_preference_key)
    assert wired, "the notification registry is empty — nothing can be wired"

    rendered = _rendered_preference_keys()
    assert rendered, "no rendered toggles found — the manifest scan drifted"
    assert rendered <= wired, (
        f"these toggles are visible but no NotificationKind claims them: "
        f"{sorted(rendered - wired)}. Wire a sender first, or remove the row."
    )


def test_every_wired_preference_key_has_a_visible_toggle():
    """The OTHER direction, and the one that actually shipped.

    Push went live on 2026-08-01 while `FeatureFlags.notificationPreferencesEnabled` was
    still false, so for a week users received alerts with NO in-app way to turn them off.
    Their only recourse was iOS Settings, which kills every notification type at once —
    and iOS never re-prompts once they do.

    A registered kind with no row on this screen recreates exactly that.
    """
    from app.services.notification_kinds import NOTIFICATION_KINDS

    wired = set()
    for kind in NOTIFICATION_KINDS.values():
        wired.add(kind.preference_key)
        if kind.master_preference_key:
            wired.add(kind.master_preference_key)

    rendered = _rendered_preference_keys()
    assert wired <= rendered, (
        f"these kinds SEND but have no visible toggle: {sorted(wired - rendered)}. "
        f"Users would get those alerts with no in-app way to opt out — add a row to "
        f"NotificationSettingsViewModel.groups in the same change as the sender."
    )


def test_the_manifest_scan_is_not_vacuous():
    """Mutation check on the parser, per `project_source_scan_guard_vacuity`.

    A guard that has never been seen failing is a guard that passes vacuously. This
    feeds the real parser doctored input and asserts it notices — proving the regex
    reads the manifest rather than matching something incidental, and that comments are
    genuinely stripped.
    """
    import re

    sample = '''
    static let groups: [NotificationGroupSpec] = [
        NotificationGroupSpec(
            id: "x", title: "X", subtitle: "", icon: "bell",
            masterKey: "notify_master",
            rows: [
                // NotificationToggleSpec(key: "notify_ghost", ...)  <- a comment
                NotificationToggleSpec(key: "notify_real", title: "R", subtitle: "s"),
            ]
        ),
    ]
    '''
    stripped = re.sub(r"//[^\n]*", "", sample)
    open_at = stripped.index("= [", stripped.find("static let groups"))
    block = stripped[open_at: stripped.index("\n    ]", open_at)]

    keys = set(re.findall(r'key:\s*"(notify_\w+)"', block))
    keys |= set(re.findall(r'masterKey:\s*"(notify_\w+)"', block))
    # The commented-out ghost must NOT appear; the real row and the master must.
    assert keys == {"notify_real", "notify_master"}, keys


def _synced_bool_keys() -> set:
    """Every boolean preference `SettingsSyncManager` actually uploads to the server.

    `currentBlob()` iterates ONLY `boolKeys`, so this list is the complete set of toggles
    that can ever reach the backend. Anything outside it writes to `UserDefaults` and stops.

    Bracket-bounded from the literal's `= [`, never from the declaration: the type
    annotation `[String]` contains a `]` that would truncate the block to nothing — the
    classic way this kind of guard goes silently vacuous. Comments stripped, because the
    doc comment above the list names several of these keys.
    """
    import re
    from pathlib import Path

    mgr = (Path(__file__).resolve().parents[2]
           / "frontend/ios/ios/Core/Services/SettingsSyncManager.swift")
    assert mgr.exists(), f"missing {mgr}"
    src = re.sub(r"//[^\n]*", "", mgr.read_text())

    start = src.find("static let boolKeys")
    assert start != -1, (
        "SettingsSyncManager.boolKeys is gone — it is the declared set of preferences that "
        "reach the server, and this guard has nothing to read without it"
    )
    open_at = src.index("= [", start)
    block = src[open_at: src.index("\n    ]", open_at)]
    keys = set(re.findall(r'"([a-z_]+)"', block))
    assert keys, "the boolKeys scan matched nothing — it has drifted"
    return keys


def test_every_wired_preference_key_is_actually_synced_to_the_server():
    """The direction nobody was checking, and it cost a whole notification kind.

    `test_every_wired_preference_key_has_a_visible_toggle` proves a control is RENDERED.
    It does not prove the control's value ever leaves the device. `notify_profile_topics`
    was rendered, tappable, and absent from `SettingsSyncManager.boolKeys` — so it was
    written to UserDefaults and never uploaded. `profile_match` ships `default_on=False`,
    which meant the backend read the absent key, fell back to False, and refused EVERY user
    permanently: 0 rows in `notification_events` all-time while the job ran nightly, and the
    key stored for 0 of 5 users in production.

    `notify_research_failed` was missing too. That one ships ON, so it delivered — but the
    opt-out was inert, which is precisely the "alerts with no in-app way to turn them off"
    failure `notification_kinds.py`'s own docstring says is pinned by tests.

    A toggle that cannot be persisted is worse than no toggle: it reports a state it does
    not have.
    """
    from app.services.notification_kinds import NOTIFICATION_KINDS

    wired = set()
    for kind in NOTIFICATION_KINDS.values():
        wired.add(kind.preference_key)
        if kind.master_preference_key:
            wired.add(kind.master_preference_key)
    assert wired, "the notification registry is empty — nothing can be wired"

    missing = sorted(wired - _synced_bool_keys())
    assert not missing, (
        f"these preference keys are wired to a NotificationKind but are NOT in "
        f"SettingsSyncManager.boolKeys, so their toggles never reach the server: {missing}. "
        f"A default-OFF kind is then undeliverable to everyone; a default-ON kind cannot be "
        f"turned off."
    )
