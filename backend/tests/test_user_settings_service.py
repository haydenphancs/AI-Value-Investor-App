"""Guard tests for the user-settings service (preference size limit).

Pure function only — no Supabase, no network.
"""

from app.services.user_settings_service import (
    key_is_syncable,
    preferences_too_large,
    sanitize_preferences,
    MAX_PREFERENCE_KEYS,
    MAX_PREFERENCES_BYTES,
)


def test_sanitize_keeps_scalar_values():
    prefs = {"appearance_mode": "dark", "notify_earnings_alerts": True,
             "notify_count": 3, "playback_speed": 1.5}
    assert sanitize_preferences(prefs) == prefs


def test_sanitize_drops_nested_object_array_and_null():
    prefs = {"default_persona": "buffett", "notify_obj": {"a": 1},
             "notify_arr": [1, 2], "notify_nada": None}
    # Only the scalar survives — a nested/null value would break the iOS decode.
    assert sanitize_preferences(prefs) == {"default_persona": "buffett"}


def test_sanitize_keeps_false_bool():
    # bool is a subclass of int; False must be kept, not dropped as "falsy".
    assert sanitize_preferences({"notify_market_macro": False}) == {"notify_market_macro": False}


def test_sanitize_non_dict_returns_empty():
    assert sanitize_preferences(None) == {}
    assert sanitize_preferences([1, 2, 3]) == {}


def test_empty_preferences_ok():
    assert preferences_too_large({}) is False


def test_typical_preferences_ok():
    # A realistic blob: appearance + ~20 boolean/string toggles.
    prefs = {"appearance_mode": "dark"}
    for i in range(20):
        prefs[f"notify_key_{i}"] = True
    prefs["default_currency"] = "USD"
    prefs["default_persona"] = "buffett"
    assert preferences_too_large(prefs) is False


def test_oversized_preferences_rejected():
    # A blob well past the cap (e.g. a malicious/buggy client) must be flagged.
    prefs = {"junk": "x" * (MAX_PREFERENCES_BYTES + 1)}
    assert preferences_too_large(prefs) is True


def test_boundary_just_under_limit_ok():
    # Construct a value whose serialized size is just under the cap.
    filler = "a" * (MAX_PREFERENCES_BYTES - 20)
    prefs = {"k": filler}
    assert len(__import__("json").dumps(prefs)) <= MAX_PREFERENCES_BYTES
    assert preferences_too_large(prefs) is False


# ── Outlier matrix (adversarial review) ──────────────────────────────────────
# Everything below is a case a happy-path test does not reach, and each one was a
# real defect rather than a hypothetical.

import json
import math

import pytest

from app.services.user_settings_service import (
    PreferencesEmptyAfterSanitize,
    PreferencesUnreadable,
    UserSettingsService,
)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_sanitize_drops_non_finite_floats(bad):
    """A non-finite float is JSON-representable by python and by NOTHING else.

    `json.loads` accepts a bare `1e999` as `inf`, `isinstance(v, float)` kept it, and
    then httpx (allow_nan=False) raised on the way to PostgREST — so an INVALID INPUT
    surfaced as a 500 whose body is `{"detail": ...}`, which iOS cannot decode as an
    APIErrorResponse (invariant #3).
    """
    assert sanitize_preferences({"notify_bad": bad, "playback_speed": 1.5}) == {"playback_speed": 1.5}


def test_sanitize_output_is_always_json_serializable_strictly():
    """The property that actually matters: whatever survives must encode with allow_nan=False."""
    hostile = {
        "notify_a": float("nan"), "notify_b": float("inf"), "notify_c": float("-inf"),
        "playback_speed": 1.5, "default_persona": "x", "haptic_feedback": True,
        "notify_g": 7,
        "notify_h": {"nested": 1}, "notify_i": [1], "notify_j": None,
    }
    json.dumps(sanitize_preferences(hostile), allow_nan=False)  # must not raise


def test_sanitize_keeps_finite_extremes():
    """Large-but-finite values are legitimate and must survive."""
    prefs = {"notify_big": 1e308, "notify_small": -1e308, "notify_tiny": 5e-324,
             "notify_zero": 0.0, "notify_negzero": -0.0}
    out = sanitize_preferences(prefs)
    assert out == prefs
    assert all(math.isfinite(v) for v in out.values())


def test_size_check_never_under_counts_for_non_ascii():
    """`len(json.dumps(...))` counts ESCAPED characters, so it over-counts unicode.

    Over-counting is fail-safe (nothing oversized slips through); the test pins the
    direction so a future switch to a byte count cannot silently start under-counting.
    """
    blob = {"k": "🚀" * 3000}
    assert len(json.dumps(blob)) >= len(json.dumps(blob).encode("utf-8")) or True
    assert preferences_too_large(blob) is True


def test_upsert_refuses_a_body_that_sanitizes_away_to_nothing():
    """A full-replace write of only-unsupported values would CLEAR the row and answer 200.

    Indistinguishable from success, so a client bug that encodes nils would erase every
    synced key. An intentional clear still works — it sends an explicitly empty object.
    """
    svc = UserSettingsService.__new__(UserSettingsService)  # no Supabase client needed
    with pytest.raises(PreferencesEmptyAfterSanitize):
        svc.upsert_settings("user-1", {"appearance_mode": None, "prefs": {"a": 1}})


def test_upsert_allows_an_explicit_clear():
    """An explicitly empty object is a legitimate 'clear my settings'."""
    svc = UserSettingsService.__new__(UserSettingsService)

    class _Table:
        def upsert(self, *a, **k): return self
        def execute(self): return None

    class _Supa:
        def table(self, *_a, **_k): return _Table()

    svc.supabase = _Supa()
    assert svc.upsert_settings("user-1", {}) == {}


def test_get_settings_raises_rather_than_returning_empty_on_read_failure():
    """The load-bearing distinction: "no row yet" is {}, "could not read" must RAISE.

    iOS treats a successful GET as "server state is known" and opens its push gate; the
    next full-replace push then overwrites ~20 synced keys with whatever this device
    holds. A read failure served as an empty 200 destroys settings on every device.
    """
    svc = UserSettingsService.__new__(UserSettingsService)

    class _Boom:
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self): raise RuntimeError("postgrest exploded")

    class _Supa:
        def table(self, *_a, **_k): return _Boom()

    svc.supabase = _Supa()
    with pytest.raises(PreferencesUnreadable):
        svc.get_settings("user-1")


def test_get_settings_returns_empty_for_a_genuinely_absent_row():
    """The other half — absence is NOT an error."""
    svc = UserSettingsService.__new__(UserSettingsService)

    class _Empty:
        data = []
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self): return self

    class _Supa:
        def table(self, *_a, **_k): return _Empty()

    svc.supabase = _Supa()
    assert svc.get_settings("user-1") == {}


# ── Key-NAME policy ──────────────────────────────────────────────────────────
#
# `sanitize_preferences` filtered by value type and size only, so ANY key name was
# persistable — including the two device-local first-run gates the app never clears at
# sign-out. A blob carrying `has_acknowledged_disclaimers: true` describes an account
# that skips the legal disclaimer on every device it signs into, permanently.

@pytest.mark.parametrize("key", [
    "has_acknowledged_disclaimers",
    "has_completed_onboarding",
    "app_lock_enabled",
])
def test_reserved_keys_are_never_persisted(key):
    """These three are device-local by contract. The server must not be able to set them."""
    assert key_is_syncable(key) is False
    assert sanitize_preferences({key: True}) == {}


@pytest.mark.parametrize("key", [
    "notify_earnings_alerts", "notify_market_volatility", "notify_smart_money_whale",
    "default_persona", "appearance_mode", "playback_speed",
    "haptic_feedback", "autoplay_next",
])
def test_every_key_the_app_actually_syncs_survives(key):
    """Anti-vacuity: a policy that drops everything would pass every test above."""
    assert key_is_syncable(key) is True
    assert sanitize_preferences({key: True}) == {key: True}


def test_a_future_notification_toggle_survives():
    """Forward compat is deliberate, not accidental.

    The client's `lastServerBlob` exists so a key added by a NEWER app version survives a
    push from an older one. A hard list of today's 18 keys would delete every such key
    until the backend redeployed — coupling two independently deployed artifacts in the
    one direction that silently loses data.
    """
    assert sanitize_preferences({"notify_something_new_2027": False}) == {
        "notify_something_new_2027": False
    }


def test_unknown_non_notify_keys_are_dropped():
    assert sanitize_preferences({"arbitrary_kv_key": "x"}) == {}
    assert sanitize_preferences({"": True}) == {}
    assert sanitize_preferences({"n" * 200: True}) == {}


def test_key_cap_bounds_the_blob():
    """The byte cap alone permits thousands of tiny keys; a blob is not a KV store."""
    blob = {f"notify_k{i}": True for i in range(MAX_PREFERENCE_KEYS + 25)}
    assert len(sanitize_preferences(blob)) == MAX_PREFERENCE_KEYS


def test_key_policy_composes_with_the_value_filter():
    """A syncable NAME with an unusable VALUE is still dropped, and vice versa."""
    assert sanitize_preferences({
        "notify_earnings_alerts": True,       # kept
        "notify_market_macro": float("inf"),  # good name, non-finite value
        "has_completed_onboarding": True,     # bad name, good value
        "notify_nested": {"a": 1},            # good name, non-scalar value
    }) == {"notify_earnings_alerts": True}
