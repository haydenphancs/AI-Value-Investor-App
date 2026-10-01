"""Financials deep check (2026-09-30) — the main-session integration pieces.

Covers the shared helpers every workstream builds on and the seams between them:

* ``period_labels``: annual fiscal-year labels, the annual benchmark JOIN key, and
  off-calendar quarter detection (the interim hide for the quarterly peer line).
* ``_earnings_common.next_pending_earnings``: today's pending report is the next
  date; a stale reschedule row is not.
* ``sector_benchmark_lookup.BenchmarkLookupFailed``: a swallowed DB error is
  distinguishable from "no peer group", and the health check refuses to persist it.
* ``ticker_data_cache.get_or_collect``: a collection with refused (degraded)
  Financials sections is used but never stored.
* ``_refuse_degraded_financials``: SoC and revenue-breakdown partial builds are
  dropped from a report, while data-lag / company-shape reasons are not.
* The PDF: an exact "met" quarter is not drawn as a miss, and a gross segment stack
  shows its intersegment eliminations.

Hermetic: every FMP / Supabase seam is stubbed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services._earnings_common import (
    next_pending_earnings,
    next_pending_earnings_date,
)
from app.utils.period_labels import (
    annual_benchmark_key,
    annual_fiscal_year,
    calendar_quarter_of,
)


# ── period_labels ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "record, label, join_key",
    [
        # December filer: label and join agree.
        ({"date": "2025-12-31", "fiscalYear": "2025"}, "2025", "2025"),
        # 52/53-week Jan 1-7 year end (Cadence FY2025): both read 2025.
        ({"date": "2026-01-03", "fiscalYear": "2025"}, "2025", "2025"),
        ({"date": "2026-01-03"}, "2025", "2025"),
        # FMP echoing the end-date year on a Jan 1-7 close is corrected for display.
        ({"date": "2026-01-03", "fiscalYear": "2026"}, "2025", "2025"),
        # Start-year-named FY (Home Depot FY2025 ends 2026-02-01): DISPLAY the fiscal
        # year, but JOIN on 2026 — the cell its same-calendar retail peers sit in.
        ({"date": "2026-02-01", "fiscalYear": "2025"}, "2025", "2026"),
        # End-year-named FY (Walmart FY2026 ends 2026-01-31).
        ({"date": "2026-01-31", "fiscalYear": "2026"}, "2026", "2026"),
        # Junk.
        ({"date": None}, "", ""),
        ({"date": "not-a-date", "fiscalYear": "FY25"}, "", ""),
    ],
)
def test_annual_label_and_join_key(record, label, join_key):
    assert annual_fiscal_year(record) == label
    assert annual_benchmark_key(record) == join_key


def test_profit_power_and_growth_join_annual_rows_on_the_same_key():
    """The two cards must agree on which peer cell a given year meets."""
    from app.services.growth_service import _compute_growth_points
    from app.services.profit_power_service import _build_margin_points

    rows = [
        {"date": "2024-02-04", "fiscalYear": "2023", "period": "FY", "revenue": 150e9,
         "grossProfit": 50e9, "operatingIncome": 20e9, "netIncome": 15e9},
        {"date": "2025-02-02", "fiscalYear": "2024", "period": "FY", "revenue": 152e9,
         "grossProfit": 51e9, "operatingIncome": 21e9, "netIncome": 15.5e9},
        {"date": "2026-02-01", "fiscalYear": "2025", "period": "FY", "revenue": 160e9,
         "grossProfit": 53e9, "operatingIncome": 22e9, "netIncome": 16e9},
    ]
    growth = _compute_growth_points(rows, "revenue", is_quarterly=False)
    margins = _build_margin_points(rows, [], is_quarterly=False, ticker="HD")
    g = {p["period"]: p["_match_period"] for p in growth}
    m = {p["period"]: p["_match_period"] for p in margins}
    for period in set(g) & set(m):
        assert g[period] == m[period], (period, g[period], m[period])
    assert m["2025"] == "2026"


@pytest.mark.parametrize(
    "date, quarter",
    [("2026-03-31", 1), ("2026-04-01", 1), ("2026-04-08", 2), ("2026-01-03", 4),
     ("2025-12-27", 4), (None, None), ("garbage", None)],
)
def test_calendar_quarter_of(date, quarter):
    assert calendar_quarter_of(date) == quarter


# ── next pending earnings ───────────────────────────────────────────────────


def test_todays_pending_report_is_the_next_date():
    rows = [{"date": "2026-10-01", "epsActual": None}, {"date": "2026-12-10"}]
    assert next_pending_earnings_date(rows, "2026-10-01") == "2026-10-01"


def test_a_reported_row_dated_today_is_skipped():
    rows = [{"date": "2026-10-01", "epsActual": 1.2}, {"date": "2026-12-10"}]
    assert next_pending_earnings_date(rows, "2026-10-01") == "2026-12-10"


def test_a_stale_reschedule_row_is_not_the_next_report():
    # Reported EARLY (3 days ago); the original date is still listed as pending.
    rows = [
        {"date": "2026-09-27", "epsActual": 1.0},
        {"date": "2026-10-04", "epsActual": None},
        {"date": "2026-12-28", "epsActual": None},
    ]
    assert next_pending_earnings_date(rows, "2026-09-30") == "2026-12-28"


def test_a_normal_quarterly_gap_is_not_mistaken_for_a_reschedule():
    rows = [{"date": "2026-06-03", "epsActual": 2.44}, {"date": "2026-09-02"}]
    assert next_pending_earnings_date(rows, "2026-08-01") == "2026-09-02"


@pytest.mark.parametrize(
    "rows",
    [None, [], "junk", [None, 3, {"date": None}, {"date": "bad"}],
     [{"date": "2026-01-01", "epsActual": 1.0}]],
)
def test_no_pending_row_is_none(rows):
    assert next_pending_earnings(rows, "2026-09-30") is None


def test_a_non_finite_actual_counts_as_not_reported():
    rows = [{"date": "2026-10-01", "epsActual": "NaN"}]
    assert next_pending_earnings_date(rows, "2026-10-01") == "2026-10-01"


def _financials_cache_keys():
    """The function each service stamps its report-day invalidation key with: Health
    Check, Profit Power and Signal of Confidence through `next_earnings_stamp` (P20),
    Revenue Breakdown through its own wrapper."""
    import functools

    from app.services._earnings_common import next_earnings_stamp
    from app.services.revenue_breakdown_service import _find_next_earnings_date_simple

    return [
        pytest.param(functools.partial(next_earnings_stamp, ticker="T", service=s), id=s)
        for s in ("health_check", "signal_of_confidence", "profit_power")
    ] + [pytest.param(_find_next_earnings_date_simple, id="revenue_breakdown")]


@pytest.mark.parametrize("fn", _financials_cache_keys())
def test_the_four_financials_services_keep_todays_pending_report(fn):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert fn([{"date": today, "epsActual": None}, {"date": "2999-01-01"}]) == today


# ── failed benchmark lookups ────────────────────────────────────────────────


def _failing_lookup():
    from app.services.sector_benchmark_lookup import SectorBenchmarkLookup

    lookup = SectorBenchmarkLookup.__new__(SectorBenchmarkLookup)

    def _boom(*_a, **_k):
        raise RuntimeError("supabase down")

    lookup._fetch_rows = _boom
    return lookup


def test_a_failed_lookup_is_flagged_through_every_layer():
    from app.services.sector_benchmark_lookup import lookup_failed

    lookup = _failing_lookup()
    rich = lookup.get_benchmarks("Semiconductors", "Technology", ["roe"], "annual")
    assert lookup_failed(rich) and rich == {"roe": {}}
    current = lookup.get_current_benchmarks("Semiconductors-x", "Technology", ["roe"])
    assert lookup_failed(current)
    values = lookup.get_current_benchmark_values("Semiconductors-y", "Technology", ["roe"])
    assert lookup_failed(values) and values == {"roe": None}


def test_a_plain_dict_is_not_a_failed_lookup():
    from app.services.sector_benchmark_lookup import lookup_failed

    assert not lookup_failed({"roe": 0.15})
    assert not lookup_failed({})
    assert not lookup_failed(None)


@pytest.mark.asyncio
async def test_health_check_marks_a_failed_lookup_degraded(monkeypatch):
    import test_health_check_deepcheck as hcd
    from app.services import health_check_service as hc
    from app.services.sector_benchmark_lookup import BenchmarkLookupFailed

    class _Lookup:
        def get_current_benchmark_values(self, industry, sector, metrics):
            return BenchmarkLookupFailed({m: None for m in metrics})

    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: _Lookup())
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = hcd._FakeFMP(hcd._answers())
    response, _next = await svc._build_health_check("TEST")
    assert "benchmarks" in response.degraded


@pytest.mark.asyncio
async def test_health_check_with_an_empty_peer_group_is_not_degraded(monkeypatch):
    import test_health_check_deepcheck as hcd

    response = await hcd._build(monkeypatch, bench={})
    assert "benchmarks" not in response.degraded


# ── ticker_data_cache: never store a collection with refused sections ──────


@pytest.mark.asyncio
@pytest.mark.parametrize("sections, stored", [(["growth_chart:quarterly_income"], False), ([], True)])
async def test_get_or_collect_skips_storing_degraded_collections(monkeypatch, sections, stored):
    from app.services import ticker_data_cache as tdc

    writes = []

    async def _get_cached(_t):
        return None

    async def _store(ticker, out):
        writes.append(ticker)

    monkeypatch.setattr(tdc, "get_cached_collection", _get_cached)
    monkeypatch.setattr(tdc, "store_collection", _store)
    out = SimpleNamespace(degraded_sections=list(sections))

    async def _fetch():
        return out

    result = await tdc.get_or_collect("ZZZT", _fetch)
    assert result is out
    assert (writes == ["ZZZT"]) is stored


# ── report: refuse partial SoC / revenue-breakdown builds ──────────────────


@pytest.mark.parametrize(
    "attr, reasons, dropped",
    [
        ("signal_of_confidence", ["market_cap"], True),
        ("signal_of_confidence", ["cash_flow", "profile"], True),
        ("signal_of_confidence", ["cash_flow_row"], False),
        ("signal_of_confidence", [], False),
        ("revenue_breakdown", ["segmentation_unavailable"], True),
        ("revenue_breakdown", ["earnings_calendar_unavailable"], False),
        ("revenue_breakdown", ["income_statement_empty"], False),
        ("revenue_breakdown", ["revenue_unreported"], False),
    ],
)
def test_report_refuses_partial_soc_and_breakdown(attr, reasons, dropped):
    from app.services.agents.ticker_report_data_collector import (
        _refuse_degraded_financials,
    )

    resp = SimpleNamespace(degraded=list(reasons))
    out = SimpleNamespace(ticker="T", degraded_sections=[], **{attr: resp})
    for other in ("growth_chart", "profit_power", "earnings", "signal_of_confidence",
                  "revenue_breakdown"):
        if not hasattr(out, other):
            setattr(out, other, None)
    _refuse_degraded_financials(out)
    assert (getattr(out, attr) is None) is dropped
    assert bool(out.degraded_sections) is dropped


# ── PDF ─────────────────────────────────────────────────────────────────────


def _pdf_html(track, engine):
    from app.services.pdf_report_service import build_context, render_html

    data = {
        "revenue_forecast": {"earnings_track_record": track, "beat_summary": "Beat 1 of 2"},
        "revenue_engine": engine,
    }
    return render_html(build_context(data))


def test_pdf_track_record_draws_a_met_quarter_neutral():
    html = _pdf_html(
        [
            {"period": "Q1 '26", "surprise_percent": 0.0, "beat": False, "result": "met"},
            {"period": "Q2 '26", "surprise_percent": -4.0, "beat": False, "result": "miss"},
            {"period": "Q3 '26", "surprise_percent": 3.0, "beat": True},  # old report: no result
        ],
        {},
    )
    assert 'class="met"' in html
    assert 'class="miss"' in html
    assert 'class="beat"' in html


def test_pdf_revenue_engine_shows_eliminations_and_caps_bar_width():
    html = _pdf_html(
        [],
        {
            "total_revenue": 52853.0,
            "intersegment_eliminations": 17683.0,
            "period": "FY 2025",
            "segments": [
                {"name": "Client Computing", "current_revenue": 32228.0},
                {"name": "Foundry", "current_revenue": 70000.0},  # > reported revenue
            ],
        },
    )
    assert "Intersegment eliminations" in html
    assert "−33%" in html
    assert "width:100" in html  # a >100% share never draws past its track
