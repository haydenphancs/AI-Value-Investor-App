"""R1 (2026-10-23): a FRED outage must not blank the Caydex Fair Value Estimate.

On 2026-10-21 FRED failed and no estimate appeared anywhere in the app: `_compute` needs the
5-year 10-year-Treasury average and raised without it. The average moves by basis points a week,
so when FRED cannot supply it the service now takes the newest one stored in
`dcf_fair_value_history` — only while persistence is on (DCF_ENABLED or DCF_SHADOW), only when its
SOURCE date is at most RF_FALLBACK_MAX_AGE_DAYS old, and only for a sane rate. A fallback row
records the original source date, so a fallback can never be copied forward past the limit.

Hermetic: FMP, FRED and the Supabase read (`_read_rf_history`) are stubbed.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import date, timedelta
from typing import Any, Dict, List

import pytest

import app.services.dcf_fair_value_service as dcf
from tests.test_dcf_fair_value import _FakeFMP, _FakeFRED, _payloads


class _DownFRED:
    """What `get_observations(..., min_observations=…)` returns on an outage or a short answer."""

    def __init__(self) -> None:
        self.kwargs: List[Dict[str, Any]] = []

    async def get_observations(self, series, limit=13, **kw):
        self.kwargs.append({"series": series, "limit": limit, **kw})
        return []


@pytest.fixture
def svc(monkeypatch):
    for d in (dcf._cache, dcf._inflight, dcf._failed_at, dcf._rf_fallback_memo):
        d.clear()

    def _no_supabase():
        raise AssertionError("Supabase client touched; the stubs should have answered")

    monkeypatch.setattr(dcf, "get_supabase", _no_supabase)
    monkeypatch.setattr(dcf.settings, "DCF_ENABLED", True)
    monkeypatch.setattr(dcf.settings, "DCF_SHADOW", False)
    s = dcf.DcfFairValueService.__new__(dcf.DcfFairValueService)
    s.fmp = _FakeFMP(_payloads())
    s.fred = _DownFRED()
    s._supabase = None
    s._read_stored = lambda *a, **k: None
    s.written = []
    s._write_stored = lambda *a, **k: s.written.append(("cache", a))
    s._append_history = lambda *a, **k: s.written.append(("history", a))
    s.reads = []
    s.rows = []

    def _read(as_of):
        s.reads.append(as_of)
        return copy.deepcopy(s.rows)

    s._read_rf_history = _read
    yield s
    for d in (dcf._cache, dcf._inflight, dcf._failed_at, dcf._rf_fallback_memo):
        d.clear()


def _today() -> date:
    return dcf._today_et()


def _row(as_of: date, rf: Any, src: Any = None) -> Dict[str, Any]:
    return {"as_of_date": as_of.isoformat(), "rf": rf, "src": src}


async def _settle(s) -> None:
    for _ in range(50):
        if len(s.written) >= 2:
            return
        await asyncio.sleep(0.01)


# ── the pure row filter ──

def test_the_newest_fresh_sane_row_wins():
    today = date(2026, 10, 23)
    rows = [_row(today, 3.87), _row(today - timedelta(days=2), 3.91),
            _row(today, 3.80, (today - timedelta(days=3)).isoformat())]
    assert dcf._rf_fallback_from_rows(rows, today) == (3.87, today)


def test_a_row_is_dated_by_its_source_not_by_when_it_was_copied():
    today = date(2026, 10, 23)
    chained = _row(today - timedelta(days=1), 3.9, (today - timedelta(days=8)).isoformat())
    assert dcf._rf_fallback_from_rows([chained], today) is None
    six = _row(today - timedelta(days=1), 3.9, (today - timedelta(days=6)).isoformat())
    assert dcf._rf_fallback_from_rows([six], today) == (3.9, today - timedelta(days=6))


def test_the_age_limit_is_inclusive_at_seven_days():
    today = date(2026, 10, 23)
    limit = today - timedelta(days=dcf.RF_FALLBACK_MAX_AGE_DAYS)
    assert dcf._rf_fallback_from_rows([_row(limit, 3.9)], today) == (3.9, limit)
    assert dcf._rf_fallback_from_rows([_row(limit - timedelta(days=1), 3.9)], today) is None


@pytest.mark.parametrize("rf", [float("nan"), float("inf"), 0, -1.0, 20, 25.0, True, "abc", None,
                                {"x": 1}])
def test_an_insane_or_non_numeric_rate_is_rejected(rf):
    today = date(2026, 10, 23)
    assert dcf._rf_fallback_from_rows([_row(today, rf)], today) is None


def test_malformed_rows_and_dates_are_skipped():
    today = date(2026, 10, 23)
    rows = ["junk", None, {"rf": 3.9}, _row(today + timedelta(days=1), 3.9),
            {"as_of_date": "not a date", "rf": 3.9, "src": None}, _row(today, "3.85")]
    assert dcf._rf_fallback_from_rows(rows, today) == (3.85, today)
    assert dcf._rf_fallback_from_rows(None, today) is None


# ── the service ──

@pytest.mark.asyncio
async def test_a_fred_outage_is_valued_on_the_stored_average(svc):
    yesterday = _today() - timedelta(days=1)
    svc.rows = [_row(yesterday, 3.9)]
    res = await svc.get_fair_value("ACME")
    assert res.status == "ok", res.refusal_code
    assert res.terminal_growth_pct == pytest.approx(3.9)          # g = rf_avg (below the caps)
    assert any("stored data dated " + yesterday.isoformat() in n for n in res.notes or [])
    await _settle(svc)
    log = next(a for kind, a in svc.written if kind == "history")[1]
    assert log["rf_source"] == "history_fallback"
    assert log["rf_source_date"] == yesterday.isoformat()
    assert log["rf_avg_pct"] == 3.9 and log["rf_recent_pct"] is None
    # the FRED read asked for a full 5-year window
    assert svc.fred.kwargs[0]["min_observations"] == int(0.8 * 252 * dcf.RF_AVERAGE_YEARS) == 1008


@pytest.mark.asyncio
async def test_shadow_mode_alone_may_use_the_fallback(svc, monkeypatch):
    monkeypatch.setattr(dcf.settings, "DCF_ENABLED", False)
    monkeypatch.setattr(dcf.settings, "DCF_SHADOW", True)
    svc.rows = [_row(_today(), 3.9)]
    assert (await svc.get_fair_value("ACME")).status == "ok"


@pytest.mark.asyncio
async def test_a_stale_stored_average_raises(svc):
    svc.rows = [_row(_today() - timedelta(days=dcf.RF_FALLBACK_MAX_AGE_DAYS + 1), 3.9)]
    with pytest.raises(dcf.DcfInputsUnavailableError, match="no stored average within 7 days"):
        await svc.get_fair_value("ACME")
    assert svc.written == [] and svc.fmp.calls == 0


@pytest.mark.asyncio
async def test_a_chained_fallback_cannot_outlive_the_limit(svc):
    svc.rows = [_row(_today() - timedelta(days=1), 3.9,
                     (_today() - timedelta(days=dcf.RF_FALLBACK_MAX_AGE_DAYS + 2)).isoformat())]
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await svc.get_fair_value("ACME")


@pytest.mark.asyncio
async def test_with_persistence_off_nothing_is_read_and_it_raises(svc, monkeypatch):
    monkeypatch.setattr(dcf.settings, "DCF_ENABLED", False)
    monkeypatch.setattr(dcf.settings, "DCF_SHADOW", False)
    svc.rows = [_row(_today(), 3.9)]
    with pytest.raises(dcf.DcfInputsUnavailableError, match="fallback is off"):
        await svc.get_fair_value("ACME")
    assert svc.reads == [] and svc.fmp.calls == 0


@pytest.mark.asyncio
async def test_a_failing_history_read_raises_unavailable(svc, caplog):
    def _boom(as_of):
        raise RuntimeError("supabase down")

    svc._read_rf_history = _boom
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await svc.get_fair_value("ACME")
    assert any("[dcf-rf-fallback]" in r.getMessage() and "history read failed" in r.getMessage()
               for r in caplog.records)
    assert dcf._rf_fallback_memo[_today()][1] is None                # memoised as a miss


@pytest.mark.asyncio
async def test_the_fallback_is_memoised_and_logged(svc, caplog):
    svc.rows = [_row(_today(), 3.9)]
    assert (await svc.get_fair_value("ACME")).status == "ok"
    assert (await svc.get_fair_value("ACMF")).status == "ok"
    assert len(svc.reads) == 1, "one history read per memo window, not per ticker"
    msgs = [r.getMessage() for r in caplog.records if "[dcf-rf-fallback]" in r.getMessage()]
    assert any("ACME" in m and _today().isoformat() in m for m in msgs)
    assert any("ACMF" in m for m in msgs)
    # a hit lives ~10 min
    read_at, value = dcf._rf_fallback_memo[_today()]
    dcf._rf_fallback_memo[_today()] = (read_at - dcf._RF_FALLBACK_HIT_TTL - 1, value)
    dcf._cache.clear()
    await svc.get_fair_value("ACME")
    assert len(svc.reads) == 2


@pytest.mark.asyncio
async def test_a_miss_is_retried_after_two_minutes_not_ten(svc):
    svc.rows = []
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await svc.get_fair_value("ACME")
    with pytest.raises(dcf.DcfInputsUnavailableError):
        await svc.get_fair_value("ACMF")
    assert len(svc.reads) == 1
    read_at, value = dcf._rf_fallback_memo[_today()]
    assert value is None
    dcf._rf_fallback_memo[_today()] = (read_at - dcf._RF_FALLBACK_MISS_TTL - 1, None)
    svc.rows = [_row(_today(), 3.9)]
    assert (await svc.get_fair_value("ACMG")).status == "ok"
    assert len(svc.reads) == 2
    assert dcf._RF_FALLBACK_MISS_TTL < dcf._RF_FALLBACK_HIT_TTL


@pytest.mark.asyncio
async def test_a_healthy_fred_never_reads_the_history(svc):
    svc.fred = _FakeFRED()
    svc.rows = [_row(_today(), 9.9)]
    res = await svc.get_fair_value("ACME")
    assert res.status == "ok" and res.terminal_growth_pct == pytest.approx(3.8)
    assert svc.reads == []
    await _settle(svc)
    log = next(a for kind, a in svc.written if kind == "history")[1]
    assert log["rf_source"] == "fred"
    # The newest DGS10 observation on or before today — the fake, like FRED, has no weekend
    # rows, so on a Saturday or Sunday (ET) that is the Friday, never "today" (review F1).
    newest = dcf.rf_latest_date(await _FakeFRED().get_observations("DGS10"), _today())
    assert newest is not None and 0 <= (_today() - newest).days <= 2
    assert log["rf_source_date"] == newest.isoformat()


def test_the_fallback_select_reads_only_the_window():
    """The live select (verified read-only against dcf_fair_value_history, 2026-10-23): a
    JSON-path projection, bounded to the age window, newest first, 10 rows."""
    calls: List[tuple] = []

    class _Q:
        def __getattr__(self, name):
            def _f(*a, **k):
                calls.append((name, a, k))
                return self
            return _f

        def execute(self):
            return type("R", (), {"data": None})()

    class _SB:
        def table(self, name):
            calls.append(("table", (name,), {}))
            return _Q()

    s = dcf.DcfFairValueService.__new__(dcf.DcfFairValueService)
    s._supabase = _SB()
    as_of = date(2026, 10, 23)
    assert s._read_rf_history(as_of) == []
    assert ("table", ("dcf_fair_value_history",), {}) in calls
    assert ("select", ("as_of_date, rf:inputs->rf_avg_pct, src:inputs->>rf_source_date",), {}) in calls
    assert ("gte", ("as_of_date", "2026-10-16"), {}) in calls
    assert ("order", ("as_of_date",), {"desc": True}) in calls
    assert ("limit", (10,), {}) in calls
