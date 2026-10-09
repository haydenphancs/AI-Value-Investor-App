"""`FREDClient.get_snapshot` counts its windows BY DATE, never by row position (2026-10-09).

The old math read `obs[12]` / `obs[6]` — the 13th / 7th row of the newest 14 observations
AFTER `get_observations` had dropped every missing ('.') row. One month FRED never published
(October 2025 CPI, the shutdown lapse) therefore turned "year-on-year" into a 13-month change
for a year, and the report's macro module (`_build_macro_risk_factors_from_fred`) graded
inflation on it. The chat's macro leg was fixed first (`chat_market_tools._yoy_by_date`); this
file pins the same rule in the integration every other reader shares.

Hermetic: the real `get_observations` parse runs over a canned FRED JSON body through a
stubbed `httpx.AsyncClient`; no network.
"""

from __future__ import annotations

import math
from datetime import date

import httpx
import pytest

import app.integrations.fred as fred


@pytest.fixture(autouse=True)
def _isolate():
    fred._CACHE.clear()
    fred._FAILED_AT.clear()
    yield
    fred._CACHE.clear()
    fred._FAILED_AT.clear()


def _serve(monkeypatch, observations, calls=None):
    """Answer every FRED request with `observations` (a list of {date, value} dicts)."""

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"observations": observations}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            if calls is not None:
                calls.append(k.get("params", {}))
            return _Resp()

    monkeypatch.setattr(fred.httpx, "AsyncClient", lambda **k: _C())


def _svc():
    svc = fred.FREDClient.__new__(fred.FREDClient)
    svc.api_key, svc.base_url, svc._timeout = "k", "https://fred", 5
    return svc


def _monthly(start_year, start_month, values, *, skip=()):
    """Newest-first monthly rows (FRED's `sort_order=desc`), dated the 1st, from the OLDEST
    month (`start_year`/`start_month`) forward. A month in `skip` ("YYYY-MM") is omitted, as
    FRED omits a month it never published once '.' rows are dropped."""
    rows = []
    y, m = start_year, start_month
    for v in values:
        key = f"{y:04d}-{m:02d}"
        if key not in skip:
            rows.append({"date": f"{key}-01", "value": str(v)})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return list(reversed(rows))


# ── the defect: a missing month ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_missing_month_never_turns_year_on_year_into_thirteen_months(monkeypatch):
    """Oct 2024 … Sep 2025 = 100 … 111, then Oct 2025 never published, Nov 2025 … Sep 2026
    keep climbing. Positional row 12 of the newest 14 would be Aug 2025 (a 13-month base)."""
    values = [100 + i for i in range(24)]          # Oct 2024 → Sep 2026
    rows = _monthly(2024, 10, values, skip={"2025-10"})[:14]
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("CPIAUCSL")
    assert snap.as_of == "2026-09-01" and snap.latest == 123.0
    # Sep 2025 = 111 is the base, never Aug 2025 = 110 (the 13th row).
    assert snap.yoy_pct == pytest.approx((123 - 111) / 111 * 100)
    positional = (123 - float(rows[12]["value"])) / float(rows[12]["value"]) * 100
    assert snap.yoy_pct != pytest.approx(positional), "the test would not catch the old math"


@pytest.mark.asyncio
async def test_a_missing_anniversary_month_is_no_reading_not_a_neighbour(monkeypatch):
    """The anniversary month itself is the one FRED skipped: no year-on-year at all."""
    values = [100 + i for i in range(14)]          # Aug 2025 → Sep 2026
    rows = _monthly(2025, 8, values, skip={"2025-09"})
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("CPIAUCSL")
    assert snap.as_of == "2026-09-01"
    assert snap.yoy_pct is None, "Aug or Oct 2025 is not 'a year ago'"


@pytest.mark.asyncio
async def test_the_six_month_window_is_by_date_too(monkeypatch):
    """Mar 2026 skipped: the positional row 6 would be Feb 2026 (a 7-month window)."""
    values = [4.0 + 0.1 * i for i in range(14)]    # Aug 2025 → Sep 2026
    rows = _monthly(2025, 8, values, skip={"2026-05"})
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("UNRATE")
    latest = 4.0 + 0.1 * 13                       # Sep 2026
    base = 4.0 + 0.1 * 7                          # Mar 2026
    assert snap.change_6mo_pct == pytest.approx(latest - base)
    assert snap.change_6mo_relative_pct == pytest.approx((latest - base) / base * 100)


@pytest.mark.asyncio
async def test_a_missing_six_month_base_is_no_reading(monkeypatch):
    values = [4.0 + 0.1 * i for i in range(14)]
    rows = _monthly(2025, 8, values, skip={"2026-03"})
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("UNRATE")
    assert snap.change_6mo_pct is None and snap.change_6mo_relative_pct is None
    assert snap.yoy_pct is not None, "the other window is independent"


# ── the happy path is unchanged ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_complete_monthly_series_reads_as_before(monkeypatch):
    values = [300.0 + i for i in range(14)]        # Aug 2025 → Sep 2026
    rows = _monthly(2025, 8, values)
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("CPIAUCSL")
    assert snap.series_id == "CPIAUCSL" and snap.latest == 313.0
    assert snap.yoy_pct == pytest.approx((313 - 301) / 301 * 100)
    assert snap.change_6mo_pct == pytest.approx(313 - 307)
    # The same numbers the old positional math gave when nothing was missing.
    assert snap.yoy_pct == pytest.approx((313 - float(rows[12]["value"])) / float(rows[12]["value"]) * 100)


# ── order, duplicates, junk ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_row_order_is_never_trusted(monkeypatch):
    values = [200.0 + i for i in range(14)]
    rows = _monthly(2025, 8, values)
    shuffled = rows[7:] + rows[:7]
    _serve(monkeypatch, shuffled)
    snap = await _svc().get_snapshot("CPIAUCSL")
    assert snap.as_of == "2026-09-01" and snap.latest == 213.0
    assert snap.yoy_pct == pytest.approx((213 - 201) / 201 * 100)


@pytest.mark.asyncio
async def test_a_duplicated_date_with_one_value_is_one_row(monkeypatch):
    values = [200.0 + i for i in range(14)]
    rows = _monthly(2025, 8, values)
    rows.insert(3, dict(rows[2]))                  # an exact duplicate row
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("CPIAUCSL")
    assert snap.yoy_pct == pytest.approx((213 - 201) / 201 * 100)


@pytest.mark.asyncio
async def test_a_date_with_two_different_values_is_dropped_as_ambiguous(monkeypatch):
    values = [200.0 + i for i in range(14)]
    rows = _monthly(2025, 8, values)
    rows.append({"date": "2025-09-01", "value": "999"})   # the anniversary, contradicted
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("CPIAUCSL")
    assert snap.latest == 213.0
    assert snap.yoy_pct is None, "neither value of a contradicted base is trusted"


@pytest.mark.asyncio
async def test_a_contradicted_latest_date_falls_back_to_the_last_clean_one(monkeypatch):
    values = [200.0 + i for i in range(14)]
    rows = _monthly(2025, 8, values)
    rows.insert(0, {"date": "2026-09-01", "value": "7"})  # the newest date, contradicted
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("CPIAUCSL")
    assert snap.as_of == "2026-08-01" and snap.latest == 212.0, "dated by the row it reads"
    assert snap.yoy_pct == pytest.approx((212 - 200) / 200 * 100)


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", [
    {"date": "", "value": "1"}, {"date": "not-a-date", "value": "1"},
    {"date": "2026-13-01", "value": "1"}, {"date": None, "value": "1"},
    {"date": "2026-10-01", "value": "NaN"}, {"date": "2026-10-01", "value": "inf"},
    {"date": "2026-10-01", "value": "."}, {"date": "2026-10-01", "value": ""},
    {"date": "2026-10-01", "value": None}, {"date": "2026-10-01", "value": "abc"},
])
async def test_junk_rows_never_become_the_latest_or_a_base(monkeypatch, junk):
    values = [200.0 + i for i in range(14)]
    rows = [junk] + _monthly(2025, 8, values)
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("CPIAUCSL")
    assert snap.as_of == "2026-09-01" and snap.latest == 213.0
    assert snap.yoy_pct == pytest.approx((213 - 201) / 201 * 100)
    assert all(v is None or math.isfinite(v) for v in
               (snap.latest, snap.yoy_pct, snap.change_6mo_pct, snap.change_6mo_relative_pct))


@pytest.mark.asyncio
async def test_rows_that_are_all_junk_give_no_snapshot_and_are_not_memoised(monkeypatch, caplog):
    calls = []
    _serve(monkeypatch, [{"date": "", "value": "1"}, {"date": "x", "value": "2"}], calls)
    with caplog.at_level("WARNING"):
        assert await _svc().get_snapshot("CPIAUCSL") is None
    assert ("CPIAUCSL", "snapshot") not in fred._CACHE
    assert any("none dated and finite" in r.getMessage() for r in caplog.records)


# ── bases and magnitudes ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("base", ["0", "-5"])
async def test_no_percentage_from_a_non_positive_base(monkeypatch, base):
    values = [1.0 + i for i in range(14)]
    rows = _monthly(2025, 8, values)
    for r in rows:
        if r["date"] == "2025-09-01":
            r["value"] = base
        if r["date"] == "2026-03-01":
            r["value"] = "0"
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("X")
    assert snap.yoy_pct is None
    assert snap.change_6mo_pct == pytest.approx(14.0 - 0.0), "an absolute delta is still real"
    assert snap.change_6mo_relative_pct is None, "no relative change from a zero base"


@pytest.mark.asyncio
async def test_a_negative_latest_is_kept(monkeypatch):
    """The 10Y-2Y spread goes negative; a negative LEVEL is data, not junk."""
    values = [0.5 - 0.1 * i for i in range(14)]
    rows = _monthly(2025, 8, values)
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("T10Y2Y")
    assert snap.latest == pytest.approx(0.5 - 1.3)
    assert snap.change_6mo_pct == pytest.approx(-0.6)


@pytest.mark.asyncio
async def test_huge_magnitudes_stay_finite(monkeypatch):
    values = [1e15 + i * 1e13 for i in range(14)]
    rows = _monthly(2025, 8, values)
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("X")
    assert math.isfinite(snap.yoy_pct) and snap.yoy_pct > 0


@pytest.mark.asyncio
async def test_a_daily_series_has_no_windows_rather_than_a_two_week_one(monkeypatch):
    """14 daily rows reach two weeks back. The old math called that "year-on-year"."""
    rows = [{"date": f"2026-09-{d:02d}", "value": str(4.0 + d / 100)} for d in range(30, 16, -1)]
    _serve(monkeypatch, rows)
    snap = await _svc().get_snapshot("DGS10")
    assert snap.as_of == "2026-09-30" and snap.latest == pytest.approx(4.30)
    assert snap.yoy_pct is None and snap.change_6mo_pct is None


@pytest.mark.asyncio
async def test_a_single_observation_is_a_level_with_no_windows(monkeypatch):
    _serve(monkeypatch, [{"date": "2026-09-01", "value": "4.33"}])
    snap = await _svc().get_snapshot("FEDFUNDS")
    assert snap.latest == 4.33 and snap.as_of == "2026-09-01"
    assert snap.yoy_pct is None and snap.change_6mo_pct is None


# ── the date helpers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("day,months,expected", [
    (date(2026, 9, 1), 12, date(2025, 9, 1)),
    (date(2026, 3, 1), 6, date(2025, 9, 1)),
    (date(2026, 1, 1), 1, date(2025, 12, 1)),
    (date(2026, 12, 1), 12, date(2025, 12, 1)),
    (date(2026, 8, 31), 6, None),                 # 31 February
    (date(2024, 2, 29), 12, None),                # 29 February, non-leap
    (date(2028, 2, 29), 48, date(2024, 2, 29)),
])
def test_months_before(day, months, expected):
    assert fred._months_before(day, months) == expected


def test_dated_rows_rejects_bools_and_garbage_containers():
    obs = [fred.FREDObservation(date="2026-09-01", value=True),  # type: ignore[arg-type]
           fred.FREDObservation(date="2026-08-01", value=1.5)]
    assert fred._dated_rows(obs) == {date(2026, 8, 1): 1.5}
    assert fred._dated_rows(None) == {} and fred._dated_rows("abc") == {}
    assert fred._dated_rows([object(), 7]) == {}


# ── the report's macro module reads it ────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_reports_macro_rows_carry_the_by_date_reading(monkeypatch):
    """`_fetch_fred_indicators` (the report's macro module) passes the snapshot through; with
    October 2025 missing, CPI's year-on-year is the true 12-month change."""
    from app.services.agents import ticker_report_data_collector as C

    values = [100 + i for i in range(24)]
    cpi_rows = _monthly(2024, 10, values, skip={"2025-10"})[:14]
    _serve(monkeypatch, cpi_rows)
    client = _svc()
    monkeypatch.setattr(fred, "get_fred_client", lambda: client)
    monkeypatch.setattr(fred, "MACRO_SERIES", {"CPIAUCSL": fred.MACRO_SERIES["CPIAUCSL"]})
    collector = object.__new__(C.TickerReportDataCollector)
    rows = await collector._fetch_fred_indicators()
    assert len(rows) == 1 and rows[0]["series_id"] == "CPIAUCSL"
    assert rows[0]["yoy_pct"] == pytest.approx((123 - 111) / 111 * 100)
    assert rows[0]["as_of"] == "2026-09-01"


@pytest.mark.asyncio
async def test_a_failed_read_still_never_pins_none(monkeypatch):
    """Unchanged contract: a failed observations read is not 'no observations'."""
    state = {"n": 0}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"observations": [{"date": "2026-09-01", "value": "4.33"}]}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            state["n"] += 1
            if state["n"] == 1:
                raise httpx.ReadTimeout("")
            return _Resp()

    monkeypatch.setattr(fred.httpx, "AsyncClient", lambda **k: _C())
    svc = _svc()
    assert await svc.get_snapshot("FEDFUNDS") is None
    assert ("FEDFUNDS", "snapshot") not in fred._CACHE
