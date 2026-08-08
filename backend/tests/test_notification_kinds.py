"""The notification registry, and its contract with the iOS app.

Two classes of assertion here, and the second is the one that has historically failed
in production rather than in CI:

  1. **Registry integrity.** Every kind names a category that has a cap, an
     interruption level iOS understands, and a `notify_`-prefixed preference key (the
     prefix is what makes a key syncable at all — `user_settings_service` drops
     anything else, so a typo means the toggle silently never persists).

  2. **iOS parity.** `default_on` here MIRRORS `NotificationsSettingsView.preferenceDefaults`.
     It is not a second opinion: `SettingsSyncManager.currentBlob()` OMITS any key the
     user has never touched, so for the majority of users the backend default IS what
     the toggle means. A mismatch opts people into something the UI shows as off.

⚠️ SOURCE-SCAN VACUITY. Several assertions below read Swift source with a regex. A
regex that stops matching makes the assertion pass on an empty set, so each one is
paired with an anti-vacuity floor, the search window is brace-bounded, and `//`
comments are stripped before matching (a key mentioned in a comment is not a
declaration). Mutate any of these deliberately and confirm it goes red before trusting
it.
"""

import re
from pathlib import Path

import pytest

from app.services.notification_kinds import (
    CATEGORY_DAILY_CAPS,
    DELIVERY_PREFERENCE_DEFAULTS,
    LEGACY_PREFERENCE_DEFAULTS,
    NOTIFICATION_KINDS,
    NotificationKind,
    _VALID_LEVELS,
    category_cap,
    get_kind,
    kind_for_preference_key,
    preference_defaults,
)

REPO = Path(__file__).resolve().parents[2]
VIEW = REPO / "frontend/ios/ios/Views/Screens/NotificationsSettingsView.swift"
SYNC = REPO / "frontend/ios/ios/Core/Services/SettingsSyncManager.swift"


def _strip_comments(src: str) -> str:
    """Drop `//` line comments. A key NAMED in a comment is not a declaration, and
    counting it would make every assertion here quietly weaker."""
    return re.sub(r"//[^\n]*", "", src)


def _bracket_block(src: str, anchor: str) -> str:
    """The `[ ... ]` literal following `anchor`, brace-bounded.

    Slices from the literal's OPENING bracket, never from the declaration: the type
    annotation `[String: Bool]` contains a `]` that would truncate the block to nothing
    — the exact way a source-scan guard starts passing vacuously.
    """
    start = src.find(anchor)
    assert start != -1, f"missing declaration {anchor!r} in {VIEW.name}"
    open_at = src.index("= [", start)
    return src[open_at: src.index("\n    ]", open_at)]


# ── registry integrity ───────────────────────────────────────────────────────

def test_the_registry_is_not_empty():
    assert len(NOTIFICATION_KINDS) >= 8


@pytest.mark.parametrize("kind", NOTIFICATION_KINDS.values(), ids=lambda k: k.key)
def test_every_kind_is_internally_consistent(kind: NotificationKind):
    assert kind.key, "a kind with no key cannot be routed"
    assert kind.preference_key.startswith("notify_"), (
        "user_settings_service._KNOWN_KEY_PREFIXES only accepts notify_*; anything else "
        "is dropped by sanitize_preferences and the toggle never persists"
    )
    assert kind.category in CATEGORY_DAILY_CAPS, (
        "an uncapped category by accident is how a user gets forty notifications"
    )
    assert kind.interruption_level in _VALID_LEVELS
    assert kind.thread_id, "APNs groups the notification stack by thread-id"
    assert kind.route_kind, "the iOS router switches on route_kind"
    assert kind.label, "the admin preview endpoint renders this"


def test_the_dict_key_matches_each_kinds_own_key():
    """They are used interchangeably (`data["kind"]` on the wire vs the lookup), so a
    mismatch would route a notification under a different kind's rules."""
    for key, kind in NOTIFICATION_KINDS.items():
        assert key == kind.key


def test_preference_keys_are_unique_across_kinds():
    """`kind_for_preference_key` — which `notify_watchers(preference_key=...)` uses —
    would otherwise be arbitrary, and 'arbitrary' means delivering under the wrong cap,
    thread and interruption level. The registry raises at IMPORT on a duplicate; this
    proves the guard is real rather than only documented."""
    keys = [k.preference_key for k in NOTIFICATION_KINDS.values()]
    assert len(keys) == len(set(keys))


def test_an_unknown_kind_raises_rather_than_defaulting():
    """A safe-looking fallback ('just use ACTIVE and the watchlist cap') would send a
    real notification to real people under the WRONG preference key — i.e. to users who
    had opted out."""
    with pytest.raises(KeyError):
        get_kind("no_such_kind")


def test_kind_for_preference_key_resolves_children_and_ignores_masters():
    assert kind_for_preference_key("notify_watchlist_changes") == "ticker_move"
    # A GROUP master legitimately owns no single kind.
    assert kind_for_preference_key("notify_smart_money") is None
    assert kind_for_preference_key(None) is None
    assert kind_for_preference_key("notify_nothing_at_all") is None


def test_an_invalid_kind_is_rejected_at_construction():
    """Fail at import, not at send time. A typo'd level would otherwise surface months
    later as a silently-downgraded notification on a user's phone, with no log."""
    base = dict(
        key="x", preference_key="notify_x", master_preference_key=None,
        default_on=True, category="app", interruption_level="active",
        thread_id="t", respects_quiet_hours=True, route_kind="ticker", label="X",
    )
    NotificationKind(**base)  # the control: valid, must not raise

    with pytest.raises(ValueError, match="interruption_level"):
        NotificationKind(**{**base, "interruption_level": "urgent"})
    with pytest.raises(ValueError, match="CATEGORY_DAILY_CAPS"):
        NotificationKind(**{**base, "category": "made_up"})
    with pytest.raises(ValueError, match="notify_"):
        NotificationKind(**{**base, "preference_key": "x_alerts"})


# ── caps ─────────────────────────────────────────────────────────────────────

def test_report_ready_is_uncapped():
    """Its only member is research_complete: the user pressed a button and paid credits
    seconds ago. Capping the answer to a request they just made is indistinguishable
    from the feature being broken."""
    assert category_cap("app") is None


def test_every_other_category_is_capped():
    for category, cap in CATEGORY_DAILY_CAPS.items():
        if category == "app":
            continue
        assert isinstance(cap, int) and cap > 0, category


def test_the_legacy_env_knob_still_overrides_the_watchlist_cap():
    """PUSH_MAX_ALERTS_PER_USER_PER_DAY predates per-category caps. An operator who had
    tuned it must not find it silently inert after the split."""
    assert category_cap("watchlist", 7) == 7
    assert category_cap("watchlist", 3) == 3
    # <= 0 means uncapped, matching the `cap > 0` guard the dispatcher always used.
    assert category_cap("watchlist", 0) is None
    # The override is scoped to watchlist and must not leak into other categories.
    assert category_cap("earnings", 7) == CATEGORY_DAILY_CAPS["earnings"]


def test_an_unknown_category_has_no_cap_entry():
    assert category_cap("not_a_category") is None


# ── iOS parity ───────────────────────────────────────────────────────────────

def test_the_ios_defaults_map_exists():
    assert VIEW.exists(), f"missing {VIEW}"
    assert SYNC.exists(), f"missing {SYNC}"


def _ios_bool_defaults() -> dict[str, bool]:
    block = _strip_comments(_bracket_block(VIEW.read_text(), "static let preferenceDefaults"))
    found = dict(re.findall(r'"(notify_\w+)"\s*:\s*(true|false)', block))
    assert len(found) >= 10, f"regex drifted — only found {found}"
    return {k: v == "true" for k, v in found.items()}


def test_every_registered_preference_key_is_declared_in_ios():
    """Including the group masters. A key the backend consults but iOS never declares
    has no default on the client side, so the two disagree about what 'untouched' means.
    """
    ios = _ios_bool_defaults()
    backend = preference_defaults()
    missing = set(backend) - set(ios)
    assert not missing, (
        f"backend declares defaults for {sorted(missing)} but "
        f"NotificationsSettingsView.preferenceDefaults does not"
    )


def test_the_default_for_every_key_agrees_with_ios():
    """The load-bearing one. `currentBlob()` omits untouched keys, so for most users the
    BACKEND default is what the toggle actually means."""
    ios = _ios_bool_defaults()
    backend = preference_defaults()
    mismatched = {
        k: (backend[k], ios[k]) for k in backend if k in ios and backend[k] != ios[k]
    }
    assert not mismatched, (
        f"backend/iOS default disagreement (backend, ios): {mismatched}. "
        f"A user who never opened the screen gets the BACKEND value while the UI shows "
        f"the iOS one."
    )


def test_the_kinds_that_ship_off_are_exactly_the_ones_ios_shows_off():
    ios_off = {k for k, v in _ios_bool_defaults().items() if v is False}
    backend_off = {k for k, v in preference_defaults().items() if v is False}
    assert backend_off == ios_off


def test_every_key_the_client_syncs_is_accounted_for():
    """A synced key with no owner anywhere is a key nothing can ever read — which is the
    state twelve of the original thirteen toggles were in, and the reason their UI had
    to be hidden."""
    synced = set(re.findall(r'"(notify_\w+)"', _strip_comments(SYNC.read_text())))
    assert len(synced) >= 13, f"regex drifted — only found {sorted(synced)}"

    known = (
        set(preference_defaults())
        | set(LEGACY_PREFERENCE_DEFAULTS)
        | set(DELIVERY_PREFERENCE_DEFAULTS)
        # String-valued delivery settings, read by quiet_hours.py rather than by
        # preference_enabled. `notify_timezone` deliberately has no static default.
        | {"notify_quiet_start", "notify_quiet_end", "notify_timezone"}
    )
    orphans = synced - known
    assert not orphans, (
        f"iOS syncs {sorted(orphans)} but no NotificationKind, legacy entry or delivery "
        f"setting claims them — either wire them or drop them from SettingsSyncManager"
    )


def test_legacy_and_registered_keys_do_not_overlap():
    """A key in both would have two declared defaults, and which one wins would depend
    on dict ordering in `preference_defaults()`."""
    registered = set()
    for kind in NOTIFICATION_KINDS.values():
        registered.add(kind.preference_key)
        if kind.master_preference_key:
            registered.add(kind.master_preference_key)
    assert not (registered & set(LEGACY_PREFERENCE_DEFAULTS))
    assert not (registered & set(DELIVERY_PREFERENCE_DEFAULTS))


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_ios_scan_would_actually_notice_a_change():
    """Mutation check on the parser itself, per `project_source_scan_guard_vacuity`: a
    guard that has never been seen failing is a guard that passes vacuously.

    Feeds the real parser a doctored copy of the real file and asserts it reports the
    doctored value — proving the regex reads the declaration rather than matching
    something incidental.
    """
    src = VIEW.read_text()
    doctored = src.replace(
        '"notify_watchlist_changes": true', '"notify_watchlist_changes": false'
    )
    assert doctored != src, "the anchor line moved — this mutation test is now inert"

    block = _strip_comments(_bracket_block(doctored, "static let preferenceDefaults"))
    parsed = dict(re.findall(r'"(notify_\w+)"\s*:\s*(true|false)', block))
    assert parsed["notify_watchlist_changes"] == "false"


def test_a_key_named_only_in_a_comment_is_not_counted_as_declared():
    """Comment-stripping is why the orphan check above is meaningful. Without it, an
    explanatory comment mentioning a key would satisfy the scan on its own."""
    sample = '''
    static let preferenceDefaults: [String: Bool] = [
        // "notify_ghost": true   <- a comment, not a declaration
        "notify_real": true,
    ]
    '''
    block = _strip_comments(_bracket_block(sample, "static let preferenceDefaults"))
    parsed = dict(re.findall(r'"(notify_\w+)"\s*:\s*(true|false)', block))
    assert set(parsed) == {"notify_real"}
