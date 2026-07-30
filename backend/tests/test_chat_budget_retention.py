"""Retention sweep for `chat_usage_budget`.

Migration 096 documented this sweep ("a per-day cleanup sweep deletes by budget_day") and
added `idx_chat_usage_budget_day` to support it, but the sweep was never written. The table
therefore accumulated one row per user per active day forever — an unbounded, user-keyed
record of which days each person used chat, with no purpose once the day has passed and no
retention bound to disclose.

Only TODAY's row is ever read (`_budget_day()`), so anything past the window is pure
accumulation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.chat_budget_service import ChatBudgetService

_TZ = ZoneInfo("America/New_York")


class _FakeDelete:
    def __init__(self, log, rows, fail):
        self._log, self._rows, self._fail = log, rows, fail
        self._cutoff = None

    def lt(self, col, val):
        self._log.append(("lt", col, val))
        self._cutoff = val
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("supabase down")
        self._log.append(("execute", self._cutoff))
        return type("R", (), {"data": self._rows})()


class _FakeTable:
    def __init__(self, log, rows, fail):
        self._log, self._rows, self._fail = log, rows, fail

    def delete(self):
        self._log.append(("delete",))
        return _FakeDelete(self._log, self._rows, self._fail)


class FakeSupabase:
    def __init__(self, rows=None, fail=False):
        self.log: list[tuple] = []
        self._rows = rows if rows is not None else []
        self._fail = fail

    def table(self, name):
        self.log.append(("table", name))
        return _FakeTable(self.log, self._rows, self._fail)


def _service(rows=None, fail=False) -> tuple[ChatBudgetService, FakeSupabase]:
    svc = ChatBudgetService.__new__(ChatBudgetService)  # skip get_supabase() in __init__
    sb = FakeSupabase(rows=rows, fail=fail)
    svc.supabase = sb
    return svc, sb


def test_sweep_targets_the_right_table_and_column():
    svc, sb = _service(rows=[{"user_id": "u"}])
    svc.cleanup_old_budget_rows()
    assert ("table", "chat_usage_budget") in sb.log
    lt = [e for e in sb.log if e[0] == "lt"]
    assert lt and lt[0][1] == "budget_day", "must filter on budget_day (the indexed column)"


def test_cutoff_is_retention_days_ago_in_eastern_time():
    svc, sb = _service()
    svc.cleanup_old_budget_rows()
    cutoff = [e for e in sb.log if e[0] == "lt"][0][2]
    expected = (
        datetime.now(_TZ).date() - timedelta(days=ChatBudgetService.RETENTION_DAYS)
    ).isoformat()
    assert cutoff == expected


def test_retention_window_is_wider_than_a_single_day():
    """Today's row is live. A 0- or 1-day window risks a timezone edge or clock skew
    deleting the row the limiter is actively using."""
    assert ChatBudgetService.RETENTION_DAYS >= 2


def test_cutoff_never_includes_today():
    svc, sb = _service()
    svc.cleanup_old_budget_rows()
    cutoff = [e for e in sb.log if e[0] == "lt"][0][2]
    today = datetime.now(_TZ).date().isoformat()
    assert cutoff < today, "the sweep must never be able to delete today's live row"


def test_returns_deleted_count():
    svc, _ = _service(rows=[{"user_id": "a"}, {"user_id": "b"}, {"user_id": "c"}])
    assert svc.cleanup_old_budget_rows() == 3


def test_zero_deletions_is_fine():
    svc, _ = _service(rows=[])
    assert svc.cleanup_old_budget_rows() == 0


def test_none_data_is_treated_as_zero():
    svc, _ = _service(rows=None)
    assert svc.cleanup_old_budget_rows() == 0


def test_failure_is_swallowed_and_reported_as_zero():
    """The sweep runs inside the shared news pre-warmer loop. Raising would kill that
    loop and stop news pre-warming too, which is a much worse outcome than a skipped
    sweep — so it degrades instead."""
    svc, _ = _service(fail=True)
    assert svc.cleanup_old_budget_rows() == 0
