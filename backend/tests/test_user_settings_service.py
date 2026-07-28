"""Guard tests for the user-settings service (preference size limit).

Pure function only — no Supabase, no network.
"""

from app.services.user_settings_service import (
    preferences_too_large,
    MAX_PREFERENCES_BYTES,
)


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
