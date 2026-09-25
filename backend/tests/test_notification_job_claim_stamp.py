"""Two ledger bugs in `app/services/notification_jobs.py` (2026-09-25).

1. **A run that crosses midnight marked TOMORROW as done.** `finish_notification_job` /
   `finish_scheduled_job` derive `run_day` from `p_now`, and the release sent the FINISH
   time. A run claimed at 23:50 that succeeded at 00:10 recorded the next day, and the
   claim then refused that whole day's run (theme insights retry until midnight ET; a
   sender after a late deploy; a whale sweep claimed at 23:5x UTC). The release now
   carries the claim's own timestamp.
2. **A failed cursor read re-baselined the smart-money whale pass.** `last_cursor`
   returned None ("no baseline") on a read error; the sender then scanned only the last
   24 h and the successful run overwrote the real cursor, so every row between the two was
   never evaluated. A failed read now raises `JobStateUnreadable`, which fails the claimed
   run; the next hourly wake retries.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import notification_jobs as nj

ET = ZoneInfo("America/New_York")
CLAIM_AT = datetime(2026, 10, 1, 23, 50, tzinfo=ET)
FINISH_AT = datetime(2026, 10, 2, 0, 10, tzinfo=ET)


class _Clock(datetime):
    """`datetime` whose now() walks a script: the claim reads 23:50 ET, anything later
    reads 00:10 ET the next day."""

    ticks = []

    @classmethod
    def now(cls, tz=None):
        t = cls.ticks.pop(0) if len(cls.ticks) > 1 else cls.ticks[0]
        return t.astimezone(tz) if tz else t


class _RpcRecorder:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=True))


def _stamp_day(params, tz):
    return datetime.fromisoformat(params["p_now"]).astimezone(ZoneInfo(tz)).date()


@pytest.fixture
def ledger(monkeypatch):
    rec = _RpcRecorder()
    _Clock.ticks = [CLAIM_AT, FINISH_AT]
    monkeypatch.setattr(nj, "datetime", _Clock)
    monkeypatch.setattr(nj, "_sb", lambda: rec)
    return rec


@pytest.mark.asyncio
async def test_a_notification_run_crossing_midnight_is_recorded_on_its_claim_day(ledger):
    async with nj.claimed_job(nj.JOB_EARNINGS) as run:
        assert run is not None
        run.success = True
    (claim_name, claim), (finish_name, finish) = ledger.calls
    assert (claim_name, finish_name) == ("claim_notification_job", "finish_notification_job")
    assert finish["p_now"] == claim["p_now"], "the release must carry the claim's stamp"
    assert _stamp_day(finish, "America/New_York").isoformat() == "2026-10-01", (
        "run_day would be Oct 2 and Oct 2's run would be refused all day"
    )


@pytest.mark.asyncio
async def test_a_scheduled_run_crossing_midnight_is_recorded_on_its_claim_day(ledger):
    async with nj.claimed_scheduled_job("theme_insights_daily", timezone_name="America/New_York") as run:
        assert run is not None
        run.success = True
    (claim_name, claim), (finish_name, finish) = ledger.calls
    assert (claim_name, finish_name) == ("claim_scheduled_job", "finish_scheduled_job")
    assert finish["p_now"] == claim["p_now"]
    assert finish["p_timezone"] == "America/New_York"
    assert _stamp_day(finish, "America/New_York").isoformat() == "2026-10-01"


@pytest.mark.asyncio
async def test_a_failed_run_still_releases_with_the_claim_stamp(ledger):
    with pytest.raises(RuntimeError):
        async with nj.claimed_scheduled_job(nj.JOB_WHALE_HYDRATION_FULL) as run:
            raise RuntimeError("FMP down")
    (_, claim), (_, finish) = ledger.calls
    assert finish["p_success"] is False and finish["p_now"] == claim["p_now"]


# ── last_cursor: a failed read is not "no baseline" ──────────────────────────────────


class _Rows:
    def __init__(self, rows=None, boom=None):
        self.rows, self.boom = rows, boom

    def table(self, name):
        if self.boom:
            raise self.boom
        return self

    def select(self, *_):
        return self

    def eq(self, *_):
        return self

    def limit(self, *_):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def test_a_failed_cursor_read_raises(monkeypatch):
    monkeypatch.setattr(nj, "_sb", lambda: _Rows(boom=RuntimeError("520 Origin Error")))
    with pytest.raises(nj.JobStateUnreadable):
        nj.last_cursor(nj.JOB_SMART_MONEY)


def test_no_row_or_no_cursor_is_still_no_baseline(monkeypatch):
    monkeypatch.setattr(nj, "_sb", lambda: _Rows(rows=[]))
    assert nj.last_cursor(nj.JOB_SMART_MONEY) is None
    monkeypatch.setattr(nj, "_sb", lambda: _Rows(rows=[{"last_cursor": None}]))
    assert nj.last_cursor(nj.JOB_SMART_MONEY) is None
    monkeypatch.setattr(nj, "_sb", lambda: _Rows(rows=[{"last_cursor": "2026-09-24T22:00:00Z"}]))
    assert nj.last_cursor(nj.JOB_SMART_MONEY) == datetime(2026, 9, 24, 22, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_smart_money_fails_its_run_when_the_cursor_is_unreadable(monkeypatch):
    from app.services.notification_senders import smart_money_sender as sm

    outcomes, whale_calls = [], []

    @contextlib.asynccontextmanager
    async def _claimed(job):
        run = nj.NotificationJobResult()
        try:
            yield run
        except Exception:
            run.success = False
            raise
        finally:
            outcomes.append((run.success, run.cursor))

    def _unreadable(job):
        raise nj.JobStateUnreadable("520")

    async def _insider(now):
        return 0

    async def _whale(now, cursor):
        whale_calls.append(cursor)
        return 0, None

    monkeypatch.setattr(sm, "claimed_job", _claimed)
    monkeypatch.setattr(sm, "last_cursor", _unreadable)
    monkeypatch.setattr(sm, "_run_insider_phase", _insider)
    monkeypatch.setattr(sm, "_run_whale_phase", _whale)

    with pytest.raises(nj.JobStateUnreadable):
        await sm.run_smart_money_notifications()
    assert whale_calls == [], "must not re-baseline the whale pass to the last 24 h"
    assert outcomes == [(False, None)], "the day stays open and the stored cursor is untouched"


# ── Smart money: a second filing on the same trade date is not a duplicate ──────────


def test_whale_dedup_key_separates_filings_that_share_a_trade_date():
    from app.services.notification_senders.smart_money_sender import whale_dedup_key

    monday = whale_dedup_key("w1", "bought", "2026-09-12", ["NVDA"])
    wednesday = whale_dedup_key("w1", "bought", "2026-09-12", ["AAPL"])
    assert monday != wednesday, "the AAPL filing was dropped as a duplicate of NVDA's"
    # A retry of the SAME roll-up is still the same key (order/duplicates don't matter).
    assert whale_dedup_key("w1", "bought", "2026-09-12", ["MSFT", "NVDA", "MSFT"]) == \
        whale_dedup_key("w1", "bought", "2026-09-12", ["NVDA", "MSFT"])
    assert whale_dedup_key("w1", "bought", "", ["NVDA"]).startswith("whale:w1:bought:nodate:")


def test_the_whale_phase_uses_the_ticker_aware_key():
    import inspect
    import re
    from app.services.notification_senders import smart_money_sender as sm

    src = inspect.getsource(sm._run_whale_phase)
    src = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert re.search(r"dedup_key=whale_dedup_key\(", src), "the whale pass bypasses whale_dedup_key"
