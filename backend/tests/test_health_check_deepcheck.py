"""Health Check deep-check fixes (Financials tab + the snapshot that feeds the report).

Each test drives the REAL `_build_health_check` / `get_health_check` /
`HealthSnapshotService._compute_with_status` against a stubbed FMP client and a stubbed
peer-benchmark lookup, and asserts the CORRECT DEGRADED behaviour for an outlier input —
an omitted or "N/M" row, a non-persisted build — never just "some number came back".

Findings covered (survivors.json): #5 ROE on negative equity, #35 IC=0 / P/E<=0,
#36 partial builds persisted for 24h, #38 non-positive peer medians, #39 snapshot
pass_rating ignored neutrals, #74 Z status judged at 2 dp, #75 "Nx below" labels,
#57 (backend half) the `degraded` field on HealthCheckResponse.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any, Dict, List, Optional

import pytest

from app.integrations.fmp import FMPNotEntitledException, FMPRateLimitException
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse

# ── fixtures: a healthy, ordinary technology company ─────────────────────────

_PROFILE = {"symbol": "TEST", "sector": "Technology", "industry": "Software - Infrastructure",
            "mktCap": 2.0e12}
_RATIOS = {"debtToEquityRatioTTM": 0.5, "priceToEarningsRatioTTM": 25.0,
           "currentRatioTTM": 1.5, "interestCoverageRatioTTM": 20.0, "quickRatioTTM": 1.2}
_KEY_METRICS = {"returnOnEquityTTM": 0.30}
_BS = {"totalAssets": 500e9, "totalLiabilities": 250e9, "totalCurrentAssets": 150e9,
       "totalCurrentLiabilities": 100e9, "retainedEarnings": 100e9,
       "totalStockholdersEquity": 250e9}
_QUARTER = {"operatingIncome": 30e9, "interestExpense": 1e9, "revenue": 80e9,
            "netIncome": 25e9, "ebitda": 35e9}
_BENCH = {"debt_to_equity": 0.6, "pe_ratio": 28.0, "roe": 0.15, "current_ratio": 1.4,
          "interest_coverage": 15.0, "quick_ratio": 1.0}


def _income(overrides: Optional[Dict[int, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Four TTM quarters, newest first; `overrides` patches quarter i."""
    overrides = overrides or {}
    rows = []
    for i, date in enumerate(("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")):
        row = dict(_QUARTER, date=date)
        row.update(overrides.get(i, {}))
        rows.append(row)
    return rows


class _FakeFMP:
    """Per-method answers; an answer that is an Exception is RAISED, like a failed leg."""

    def __init__(self, answers: Dict[str, Any]) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            answer = self._answers.get(name, [])
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        return _call


def _answers(*, profile=None, ratios=None, key_metrics=None, bs=None, income=None,
             calendar=None) -> Dict[str, Any]:
    return {
        "get_company_profile": dict(_PROFILE) if profile is None else profile,
        "get_ratios_ttm": [dict(_RATIOS)] if ratios is None else ratios,
        "get_key_metrics_ttm": [dict(_KEY_METRICS)] if key_metrics is None else key_metrics,
        "get_balance_sheet": [dict(_BS)] if bs is None else bs,
        "get_income_statement": _income() if income is None else income,
        "get_earning_calendar_full": [] if calendar is None else calendar,
    }


def _service(monkeypatch, answers: Dict[str, Any], *, bench: Optional[Dict] = None,
             bench_error: Optional[BaseException] = None):
    from app.services import health_check_service as hc

    class _Lookup:
        def get_current_benchmark_values(self, industry, sector, metrics):
            if bench_error is not None:
                raise bench_error
            table = _BENCH if bench is None else bench
            return {m: table.get(m) for m in metrics}

    # Module-level `from … import get_sector_benchmark_lookup`: patch the CALLER's binding.
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: _Lookup())
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = _FakeFMP(answers)
    return hc, svc


async def _build(monkeypatch, answers=None, **kw) -> HealthCheckResponse:
    _hc, svc = _service(monkeypatch, answers or _answers(), **kw)
    response, _next = await svc._build_health_check("TEST")
    return response


def _metric(resp: HealthCheckResponse, kind: str) -> Optional[HealthCheckMetricSchema]:
    return next((m for m in resp.metrics if m.type == kind), None)


def _rendered(m: HealthCheckMetricSchema) -> str:
    """What iOS shows: `{highlighted_value} {highlighted_label} {insight_text}`."""
    return f"{m.highlighted_value} {m.highlighted_label} {m.insight_text}"


def _expected_rating_inputs(resp: HealthCheckResponse, unscored: set) -> tuple:
    scored = [m for m in resp.metrics if m.type not in unscored]
    passed = sum(1 for m in scored if m.status == "positive")
    neutrals = sum(1 for m in scored if m.status == "neutral")
    return passed, neutrals, len(scored)


def _spy_persist(monkeypatch, svc, method: str) -> List[str]:
    """Run the fire-and-forget Supabase write inline so the assertion cannot race it."""
    persisted: List[str] = []

    def _record(ticker, *rest):
        persisted.append(ticker)

    monkeypatch.setattr(svc, method, _record)
    loop = asyncio.get_running_loop()
    real = loop.run_in_executor

    def _run_in_executor(executor, fn, *args):
        if fn is _record:
            fn(*args)
            done = loop.create_future()
            done.set_result(None)
            return done
        return real(executor, fn, *args)

    monkeypatch.setattr(loop, "run_in_executor", _run_in_executor)
    return persisted


# ── #5 ROE on negative shareholder equity ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("label,de,roe,equity", [
    # Loss-maker on negative equity (Boeing-shaped): FMP ROE comes back POSITIVE.
    ("loss_on_negative_equity", -13.0, 3.03, -3.0e9),
    # Profitable buyback-heavy company (McDonald's-shaped): FMP ROE comes back NEGATIVE.
    ("profit_on_negative_equity", -10.0, -2.16, -4.0e9),
    # D/E missing (ratios leg had no field) — the balance sheet is the second witness.
    ("de_missing_balance_sheet_negative", None, 3.03, -1.0e9),
    # Zero equity: the ratio is undefined, never a verdict.
    ("zero_equity", None, 0.8, 0.0),
])
async def test_roe_on_negative_equity_is_not_meaningful_and_unscored(
    monkeypatch, label, de, roe, equity,
):
    ratios = dict(_RATIOS)
    if de is None:
        ratios.pop("debtToEquityRatioTTM")
    else:
        ratios["debtToEquityRatioTTM"] = de
    resp = await _build(monkeypatch, _answers(
        ratios=[ratios], key_metrics=[{"returnOnEquityTTM": roe}],
        bs=[dict(_BS, totalStockholdersEquity=equity)],
    ))

    from app.services.health_check_service import NOT_MEANINGFUL, _overall_rating

    m = _metric(resp, "roe")
    assert m is not None, f"{label}: the ROE row should still be SHOWN"
    assert m.highlighted_value == NOT_MEANINGFUL == "N/M"
    assert m.status == "neutral"
    assert m.comparison_value is None and m.percent_difference is None
    text = _rendered(m)
    for banned in ("Exceptional", "well below", "well above", "underperforming", "x "):
        assert banned not in text, f"{label}: a verdict leaked into an N/M row: {text!r}"
    assert "Not meaningful" in m.insight_text

    passed, neutrals, total = _expected_rating_inputs(resp, unscored={"roe"})
    assert resp.total_count == total == len(resp.metrics) - 1, "ROE must be outside total"
    assert resp.passed_count == passed
    assert resp.overall_rating == _overall_rating(passed + 0.5 * neutrals, total), (
        "the N/M row must not move the rating, not even as half credit"
    )


@pytest.mark.asyncio
async def test_negative_de_text_is_not_duplicated(monkeypatch):
    """iOS renders "{value} {label} {text}": it read "Negative shareholder equity.
    Negative equity. Liabilities exceed total assets." """
    resp = await _build(monkeypatch, _answers(
        ratios=[dict(_RATIOS, debtToEquityRatioTTM=-13.0)],
        bs=[dict(_BS, totalStockholdersEquity=-3e9)],
    ))
    de = _metric(resp, "debt_to_equity")
    assert de is not None and de.status == "negative"
    assert _rendered(de) == "Negative shareholder equity. Liabilities exceed total assets."


@pytest.mark.asyncio
async def test_positive_equity_roe_is_still_judged(monkeypatch):
    """Negative control: an ordinary company's ROE is compared and scored as before."""
    resp = await _build(monkeypatch)
    roe = _metric(resp, "roe")
    assert roe is not None and roe.highlighted_value != "N/M"
    assert roe.status == "positive" and roe.comparison_value == 15.0
    assert resp.total_count == len(resp.metrics)


# ── #35 IC = 0 with no interest expense; P/E <= 0 ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("income", [
    _income({i: {"interestExpense": 0.0} for i in range(4)}),   # reported zero
    _income({2: {"interestExpense": None}}),                      # missing in one quarter
])
async def test_zero_interest_coverage_without_interest_expense_is_omitted(monkeypatch, income):
    resp = await _build(monkeypatch, _answers(
        ratios=[dict(_RATIOS, interestCoverageRatioTTM=0.0)], income=income,
    ))
    assert _metric(resp, "interest_coverage") is None, (
        "a debt-free 0 coverage was scored as 'Vulnerable to interest expense pressure'"
    )
    full = await _build(monkeypatch)
    assert resp.total_count == full.total_count - 1


@pytest.mark.asyncio
async def test_zero_interest_coverage_with_interest_expense_stays_negative(monkeypatch):
    resp = await _build(monkeypatch, _answers(
        ratios=[dict(_RATIOS, interestCoverageRatioTTM=0.0)],
    ))
    ic = _metric(resp, "interest_coverage")
    assert ic is not None and ic.status == "negative"
    assert "Vulnerable" not in ic.insight_text


@pytest.mark.asyncio
async def test_negative_interest_coverage_is_negative_without_a_percent_gap(monkeypatch):
    resp = await _build(monkeypatch, _answers(
        ratios=[dict(_RATIOS, interestCoverageRatioTTM=-3.0)],
        income=_income({i: {"interestExpense": 7e8} for i in range(4)}),
    ))
    ic = _metric(resp, "interest_coverage")
    assert ic is not None and ic.status == "negative"
    assert ic.highlighted_value == "Negative"
    assert ic.percent_difference is None, "IC -3 vs 20 is not a '115% below' gap"
    assert ic.comparison_value == 15.0, "the peer median is still real context"
    assert "%" not in (ic.highlighted_value or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("pe", [0.0, -12.0])
async def test_non_positive_pe_is_omitted(monkeypatch, pe):
    resp = await _build(monkeypatch, _answers(
        ratios=[dict(_RATIOS, priceToEarningsRatioTTM=pe)],
    ))
    assert _metric(resp, "pe_ratio") is None, "P/E 0 read 'Deep value opportunity, 100% below'"


# ── #38 a non-positive peer median is no benchmark ───────────────────────────


@pytest.mark.asyncio
async def test_negative_roe_median_is_no_benchmark(monkeypatch):
    resp = await _build(
        monkeypatch, _answers(key_metrics=[{"returnOnEquityTTM": -0.10}]),
        bench=dict(_BENCH, roe=-0.30),
    )
    from app.services.health_check_service import _absolute_status

    roe = _metric(resp, "roe")
    assert roe is not None
    assert roe.status != "positive", "a money-loser read 'Strong capital efficiency'"
    assert roe.status == _absolute_status("roe", -10.0)
    assert roe.comparison_value is None and roe.percent_difference is None
    assert "above sector" not in _rendered(roe)


@pytest.mark.asyncio
async def test_negative_de_median_falls_back_to_absolute(monkeypatch):
    resp = await _build(
        monkeypatch, _answers(ratios=[dict(_RATIOS, debtToEquityRatioTTM=1.2)]),
        bench=dict(_BENCH, debt_to_equity=-1.5),
    )
    from app.services.health_check_service import _absolute_gauge, _absolute_status

    de = _metric(resp, "debt_to_equity")
    assert de is not None
    assert de.status == _absolute_status("debt_to_equity", 1.2) == "neutral"
    assert de.gauge_position == round(_absolute_gauge("debt_to_equity", 1.2), 2)
    assert de.comparison_value is None


@pytest.mark.asyncio
async def test_zero_median_is_no_benchmark(monkeypatch):
    resp = await _build(monkeypatch, bench=dict(_BENCH, current_ratio=0.0))
    cr = _metric(resp, "current_ratio")
    assert cr is not None and cr.comparison_value is None and cr.percent_difference is None


@pytest.mark.asyncio
async def test_negative_company_against_positive_median_is_still_negative(monkeypatch):
    """The comparison a same-sign rule would have hidden: a loss-maker vs a positive
    median is a valid, NEGATIVE reading, with the median kept as context."""
    resp = await _build(monkeypatch, _answers(key_metrics=[{"returnOnEquityTTM": -0.10}]))
    roe = _metric(resp, "roe")
    assert roe is not None and roe.status == "negative"
    assert roe.comparison_value == 15.0


# ── #75 sign-aware labels ─────────────────────────────────────────────────────


def test_format_diff_label_is_sign_aware():
    from app.services.health_check_service import (
        _format_diff_label, _generate_ic_insight, _generate_roe_insight,
    )

    # Above the median a multiplier is right…
    assert _generate_roe_insight(400.0, 60.0, 12.0)[1:] == ("5.0x", "well above")
    assert _format_diff_label(200.0) == "3.0x"
    assert _format_diff_label(150.0) == "150%"
    # …below it, never a multiplier and never past 100%.
    for pct in (-308.3, -115.0, -1540.0, -100.0, -99.6):
        label = _format_diff_label(pct)
        assert not label.endswith("x"), f"{pct} → {label!r}"
        assert int(label.rstrip("%")) <= 100, f"{pct} → {label!r}"
    assert _generate_roe_insight(-308.3, -25.0, 12.0)[1] == "100%"
    assert _generate_ic_insight(-115.0, -3.0, 20.0)[1] == "100%"
    assert _format_diff_label(-43.4) == "43%"
    assert _format_diff_label(0.0) == "0%"


@pytest.mark.asyncio
async def test_crossed_zero_roe_reads_negative_not_n_times_below(monkeypatch):
    resp = await _build(
        monkeypatch, _answers(key_metrics=[{"returnOnEquityTTM": -0.25}]),
        bench=dict(_BENCH, roe=0.12),
    )
    roe = _metric(resp, "roe")
    assert roe is not None and roe.status == "negative"
    assert roe.highlighted_value == "Negative"
    assert "positive sector average" in (roe.highlighted_label or "")
    assert roe.percent_difference is None
    assert "x" not in (roe.highlighted_value or "") and "well below" not in _rendered(roe)


# ── #74 the Z status is judged at 2 dp ───────────────────────────────────────


def test_zscore_status_boundaries_follow_one_convention():
    from app.services.health_check_service import _zscore_status

    assert _zscore_status(1.8) == "negative"     # Distress <= 1.8
    assert _zscore_status(1.81) == "neutral"
    assert _zscore_status(3.0) == "neutral"      # Grey (1.8, 3.0]
    assert _zscore_status(3.01) == "positive"    # Safe > 3.0


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,status", [(3.0449, "positive"), (1.8449, "neutral"),
                                        (2.9951, "neutral")])
async def test_z_status_uses_the_two_decimal_score(monkeypatch, raw, status):
    """A raw 3.04 rounded to 3.0 first read Grey on this card and Safe in the report."""
    hc, svc = _service(monkeypatch, _answers())
    seen: Dict[str, Any] = {}

    def _z(bs, inc, mcap, *, ndigits=1, sector=None, industry=None):
        seen.update(ndigits=ndigits, sector=sector, industry=industry)
        return round(raw, ndigits)

    monkeypatch.setattr(hc, "_compute_z_score", _z)
    resp, _ = await svc._build_health_check("TEST")
    z = _metric(resp, "altman_z_score")
    assert seen["ndigits"] == 2
    assert seen["sector"] == "Technology" and seen["industry"] == "Software - Infrastructure"
    assert z is not None and z.status == status
    assert z.value == round(raw, 2), "the 2-dp value is what iOS places the zone marker by"


# ── #36 / #57 a partial build is never persisted, and says so ────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("answers,bench_error,degraded,should_persist", [
    # /ratios-ttm 429s alone: D/E, P/E, CR, IC, QR vanish, ROE + Z read "Excellent [2/2]".
    (_answers(ratios=FMPRateLimitException("429")), None, ["ratios"], False),
    (_answers(key_metrics=FMPRateLimitException("429")), None, ["key_metrics"], False),
    (_answers(bs=FMPRateLimitException("429")), None, ["balance_sheet"], False),
    (_answers(income=FMPRateLimitException("429")), None, ["income"], False),
    (_answers(profile=FMPRateLimitException("429")), None, ["profile"], False),
    # The peer lookup RAISED: every metric silently fell back to absolute heuristics.
    (_answers(), RuntimeError("supabase 520"), ["benchmarks"], False),
    # A permanent entitlement refusal is not transient: the build without it IS the answer.
    (_answers(ratios=FMPNotEntitledException("402")), None, [], True),
    # The earnings calendar only stamps the cache's invalidation date.
    (_answers(calendar=FMPRateLimitException("429")), None, [], True),
    # Negative control.
    (_answers(), None, [], True),
])
async def test_degraded_builds_are_served_but_not_persisted(
    monkeypatch, answers, bench_error, degraded, should_persist,
):
    hc, svc = _service(monkeypatch, answers, bench_error=bench_error)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    persisted = _spy_persist(monkeypatch, svc, "_upsert_supabase_cache_safe")

    resp = await svc.get_health_check("TEST")

    assert resp.degraded == degraded
    assert resp.model_dump()["degraded"] == degraded, "the wire field reflects what is served"
    assert persisted == (["TEST"] if should_persist else [])
    assert "health_check:TEST" in hc._cache, "the 5-min memory tier still absorbs retries"
    # Tier-1 hit hands back the same flagged object.
    assert (await svc.get_health_check("TEST")).degraded == degraded
    hc._cache.clear()


@pytest.mark.asyncio
async def test_benchmarks_that_are_merely_absent_still_persist(monkeypatch):
    """A real peer group with no rows answers all-None WITHOUT an error. That is a real
    answer: treating it as degraded would re-run the 6-call fan-out every 5 minutes."""
    hc, svc = _service(monkeypatch, _answers(), bench={})
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    persisted = _spy_persist(monkeypatch, svc, "_upsert_supabase_cache_safe")
    resp = await svc.get_health_check("TEST")
    assert resp.degraded == [] and persisted == ["TEST"]
    assert all(m.comparison_value is None for m in resp.metrics)
    hc._cache.clear()


@pytest.mark.asyncio
async def test_every_leg_failing_is_degraded_with_no_metrics(monkeypatch):
    boom = FMPRateLimitException("429")
    resp = await _build(monkeypatch, _answers(
        profile=boom, ratios=boom, key_metrics=boom, bs=boom, income=boom,
    ))
    assert resp.metrics == [] and resp.total_count == 0
    assert set(resp.degraded) == {"profile", "ratios", "key_metrics", "balance_sheet",
                                  "income", "no_metrics"}


class _FakeTable:
    def __init__(self, rows):
        self.rows, self.upserts = rows, []

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def upsert(self, row, **k):
        self.upserts.append(row)
        return self

    def execute(self):
        class _R:
            pass

        r = _R()
        r.data = self.rows
        return r


class _FakeSupabase:
    def __init__(self, rows):
        self.tbl = _FakeTable(rows)

    def table(self, name):
        return self.tbl


def _row(payload: Dict[str, Any]) -> Dict[str, Any]:
    from datetime import datetime, timezone

    return {"response_json": payload, "cached_at": datetime.now(timezone.utc).isoformat(),
            "next_earnings_date": None}


def test_health_check_cache_rows_are_versioned():
    from app.services import health_check_service as hc

    good = HealthCheckResponse(symbol="TEST", overall_rating="good", passed_count=1,
                               total_count=1, metrics=[]).model_dump()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)

    # A row written before this change (no version) is rebuilt, not served for 24h.
    svc.supabase = _FakeSupabase([_row(dict(good))])
    assert svc._check_supabase_cache("TEST") is None
    # The current version is served, with the version key stripped.
    svc.supabase = _FakeSupabase([_row({**good, "payload_version": hc._HC_PAYLOAD_VERSION})])
    hit = svc._check_supabase_cache("TEST")
    assert hit is not None and hit.symbol == "TEST" and hit.degraded == []
    # A row that somehow carries a degradation is never served.
    svc.supabase = _FakeSupabase([_row({**good, "degraded": ["ratios"],
                                        "payload_version": hc._HC_PAYLOAD_VERSION})])
    assert svc._check_supabase_cache("TEST") is None

    # The writer stamps the version.
    svc.supabase = _FakeSupabase([])
    svc._upsert_supabase_cache_safe("TEST", HealthCheckResponse(**good), None)
    assert svc.supabase.tbl.upserts[0]["response_json"]["payload_version"] == hc._HC_PAYLOAD_VERSION


# ── snapshot: degradation propagates, neutrals get half credit (#36, #39) ────


def _hc_metric(kind: str, status: str, value: float = 1.0) -> HealthCheckMetricSchema:
    return HealthCheckMetricSchema(type=kind, value=value, comparison_value=1.0,
                                   gauge_position=0.5, status=status, insight_text="x")


def _snapshot_service(monkeypatch, health: Any):
    from app.services import health_check_service
    from app.services import health_snapshot_service as hs

    class _HealthCheck:
        async def get_health_check(self, ticker):
            if isinstance(health, BaseException):
                raise health
            return health

    # Function-local import inside `_compute_with_status`: patch the SOURCE module.
    monkeypatch.setattr(health_check_service, "get_health_check_service", lambda: _HealthCheck())
    hs._cache.clear()
    hs._inflight.clear()
    svc = hs.HealthSnapshotService.__new__(hs.HealthSnapshotService)
    svc.supabase = None
    svc.fmp = _FakeFMP({"get_company_profile": dict(_PROFILE), "get_balance_sheet": [dict(_BS)],
                        "get_income_statement": _income()})
    return hs, svc


@pytest.mark.asyncio
async def test_snapshot_refuses_to_persist_a_degraded_health_check(monkeypatch):
    partial = HealthCheckResponse(
        symbol="TEST", overall_rating="excellent", passed_count=2, total_count=2,
        metrics=[_hc_metric("roe", "positive", 30.0),
                 _hc_metric("altman_z_score", "positive", 4.2)],
        degraded=["ratios"],
    )
    hs, svc = _snapshot_service(monkeypatch, partial)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    persisted = _spy_persist(monkeypatch, svc, "_upsert_supabase_cache")

    snap = await svc.get_health_snapshot("TEST")
    assert snap.category == "Financial Health", "still served"
    assert persisted == [], "a partial health check was frozen into snapshot_cache for 24h"

    _, degraded = await svc._compute_with_status("TEST")
    assert degraded == ["health_check:ratios"]
    hs._cache.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("statuses,rating,weighted", [
    # Four in-line ratios + a grey Z of 2.4: the tab says "Mix"; the snapshot said 2/5.
    (["neutral"] * 4, 3, 3.0),
    # Four genuine misses still score the floor (0.4*3 + 0.6*1 = 1.8 → 2).
    (["negative"] * 4, 2, 1.8),
    # Band edge: 2 passes + 2 neutrals = 0.75 → pass_rating 4.
    (["positive", "positive", "neutral", "neutral"], 4, 3.6),
    # 1 pass + 1 neutral + 2 misses = 0.375 → pass_rating 2.
    (["positive", "neutral", "negative", "negative"], 2, 2.4),
])
async def test_snapshot_pass_rating_gives_neutrals_half_credit(monkeypatch, statuses, rating,
                                                                weighted):
    kinds = ["debt_to_equity", "current_ratio", "interest_coverage", "quick_ratio"]
    metrics = [_hc_metric(k, s) for k, s in zip(kinds, statuses)]
    metrics.append(_hc_metric("altman_z_score", "neutral", 2.4))
    health = HealthCheckResponse(symbol="TEST", overall_rating="mix", passed_count=0,
                                 total_count=5, metrics=metrics)
    _hs, svc = _snapshot_service(monkeypatch, health)
    snap, degraded = await svc._compute_with_status("TEST")
    assert degraded == []
    assert snap.rating == rating
    assert snap.weighted_score == pytest.approx(weighted)


def test_snapshot_cache_rows_are_versioned():
    from app.schemas.stock_overview import SnapshotItemResponse
    from app.services import health_snapshot_service as hs

    good = SnapshotItemResponse(category="Financial Health", rating=3, metrics=[],
                                full_report_available=True, weighted_score=3.0).model_dump()
    svc = hs.HealthSnapshotService.__new__(hs.HealthSnapshotService)
    svc.supabase = _FakeSupabase([_row(dict(good))])
    assert svc._check_supabase_cache("TEST") is None, "a pre-change row (old pass_rating) served"
    svc.supabase = _FakeSupabase([_row({**good, "_schema_v": hs._SNAPSHOT_PAYLOAD_VERSION})])
    assert svc._check_supabase_cache("TEST") is not None
    svc.supabase = _FakeSupabase([])
    svc._upsert_supabase_cache("TEST", SnapshotItemResponse(**good))
    assert svc.supabase.tbl.upserts[0]["response_json"]["_schema_v"] == hs._SNAPSHOT_PAYLOAD_VERSION
