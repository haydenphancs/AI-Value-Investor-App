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
