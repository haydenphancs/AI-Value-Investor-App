"""Guard tests for the user-settings service (preference size limit).

Pure function only — no Supabase, no network.
"""

from app.services.user_settings_service import (
    preferences_too_large,
    sanitize_preferences,
    MAX_PREFERENCES_BYTES,
)


def test_sanitize_keeps_scalar_values():
    prefs = {"appearance_mode": "dark", "notify_earnings_alerts": True,
             "count": 3, "ratio": 1.5}
    assert sanitize_preferences(prefs) == prefs


def test_sanitize_drops_nested_object_array_and_null():
    prefs = {"default_currency": "USD", "obj": {"a": 1}, "arr": [1, 2], "nada": None}
    # Only the scalar survives — a nested/null value would break the iOS decode.
    assert sanitize_preferences(prefs) == {"default_currency": "USD"}


def test_sanitize_keeps_false_bool():
    # bool is a subclass of int; False must be kept, not dropped as "falsy".
    assert sanitize_preferences({"show_premarket": False}) == {"show_premarket": False}


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
