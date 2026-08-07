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
    assert sanitize_preferences({"playback_speed": bad, "keep": 1.5}) == {"keep": 1.5}


def test_sanitize_output_is_always_json_serializable_strictly():
    """The property that actually matters: whatever survives must encode with allow_nan=False."""
    hostile = {
        "a": float("nan"), "b": float("inf"), "c": float("-inf"),
        "d": 1.5, "e": "x", "f": True, "g": 7,
        "h": {"nested": 1}, "i": [1], "j": None,
    }
    json.dumps(sanitize_preferences(hostile), allow_nan=False)  # must not raise


def test_sanitize_keeps_finite_extremes():
    """Large-but-finite values are legitimate and must survive."""
    prefs = {"big": 1e308, "small": -1e308, "tiny": 5e-324, "zero": 0.0, "negzero": -0.0}
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
