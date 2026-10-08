"""Health Check round-2 regressions (review findings R40, R41, R42).

Each test drives the REAL `HealthCheckService._build_health_check` against a stubbed FMP
client and a stubbed peer-benchmark lookup (hermetic — no network, no Supabase), and
asserts the correct DEGRADED reading for the outlier, not just "some number came back".

- R40: the Altman Z status was judged at 2 dp but the insight printed 1 dp, so a 3.03
  read "3.0 Z-Score. Safe zone" beside the card's "1.8 – 3.0" Grey label.
- R41: a barely-positive peer median (ROE 1%) still anchored a percent gap — ROE 5% read
  "5.0x well above sector average. Exceptional capital efficiency."
- R42: a balance-sheet equity of exactly 0 (FMP zero-fill) beside a positive D/E marked
  ROE "N/M … shareholder equity is negative" next to "Healthy debt position".
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

import pytest

from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse

# ── fixtures: an ordinary technology company (self-contained, no shared conftest) ──

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


def _income() -> List[Dict[str, Any]]:
    return [dict(_QUARTER, date=d)
            for d in ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")]


class _FakeFMP:
    def __init__(self, answers: Dict[str, Any]) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            answer = self._answers.get(name, [])
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        return _call


def _answers(*, ratios=None, key_metrics=None, bs=None) -> Dict[str, Any]:
    return {
        "get_company_profile": dict(_PROFILE),
        "get_ratios_ttm": [dict(_RATIOS)] if ratios is None else ratios,
        "get_key_metrics_ttm": [dict(_KEY_METRICS)] if key_metrics is None else key_metrics,
        "get_balance_sheet": [dict(_BS)] if bs is None else bs,
        "get_income_statement": _income(),
        "get_earning_calendar_full": [],
    }


def _service(monkeypatch, answers: Dict[str, Any], *, bench: Optional[Dict] = None):
    from app.services import health_check_service as hc

    class _Lookup:
        # The health check reads the RICH cells since 2026-10-07 (it reports the level).
        def get_current_benchmarks(self, industry, sector, metrics):
            table = _BENCH if bench is None else bench
            return {m: (None if table.get(m) is None
                        else {"value": table[m], "level": "sector", "n": 50})
                    for m in metrics}

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


def _assert_scoring_is_consistent(resp: HealthCheckResponse, unscored: set) -> None:
    from app.services.health_check_service import _overall_rating

    scored = [m for m in resp.metrics if m.type not in unscored]
    passed = sum(1 for m in scored if m.status == "positive")
    neutrals = sum(1 for m in scored if m.status == "neutral")
    assert resp.total_count == len(scored)
    assert resp.passed_count == passed
    assert resp.overall_rating == _overall_rating(passed + 0.5 * neutrals, len(scored))


# ── R40: the printed Z agrees with its status at the printed precision ─────────

# Words the insight uses for each status's zone ("Fortress" is the top of Safe).
_ZONE_WORDS = {"positive": ("Safe", "Fortress"), "neutral": ("Grey",),
               "negative": ("istress",)}


def _names_zone(text: str, status: str) -> bool:
    return any(w in text for w in _ZONE_WORDS[status])


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    3.03, 3.04, 3.0449, 3.0499, 3.05,     # Safe, but rounds to "3.0" at 1 dp
    1.83, 1.84, 1.8449,                   # Grey, but rounds to "1.8" at 1 dp
    2.9951, 3.0, 1.8, 1.805, 1.7951,      # boundary values themselves
    4.504, 0.95, 12.37,                   # ordinary readings (negative controls)
])
async def test_z_insight_prints_the_precision_its_status_is_judged_at(monkeypatch, raw):
    from app.services.health_check_service import _zscore_status

    hc, svc = _service(monkeypatch, _answers())
    monkeypatch.setattr(
        hc, "_compute_z_score",
        lambda bs, inc, mcap, *, ndigits=1, sector=None, industry=None: round(raw, ndigits),
    )
    resp, _ = await svc._build_health_check("TEST")
    z = _metric(resp, "altman_z_score")
    assert z is not None

    shown = float(z.highlighted_value)
    assert _zscore_status(shown) == z.status, (
        f"raw {raw}: printed {z.highlighted_value!r} sits in a different zone from the "
        f"status {z.status!r} — the card contradicts its own zone labels"
    )
    assert _zscore_status(z.value) == z.status
    assert shown == z.value, "the printed score is the value the gauge marker is placed by"
    assert _names_zone(z.insight_text, z.status), (raw, z.status, z.insight_text)
    # The two contradictions the review reproduced, stated literally.
    if z.status == "positive":
        assert z.highlighted_value not in ("3.0", "3.00")
    if z.status == "neutral":
        assert z.highlighted_value not in ("1.8", "1.80")


@pytest.mark.parametrize("raw", [3.004, 3.0051, 1.8049, 1.8051, 3.03, 1.83])
def test_z_insight_is_self_consistent_for_an_unrounded_score(raw):
    """A caller that passes the raw score still gets text and number from ONE value."""
    from app.services.health_check_service import _generate_zscore_insight, _zscore_status

    text, shown, label = _generate_zscore_insight(raw)
    assert label == "Z-Score."
    assert _names_zone(text, _zscore_status(float(shown))), (raw, shown, text)


def test_rows_from_the_previous_payload_version_are_rebuilt():
    """The printed precision and the median floor change what a stored row MEANS, so a
    row written under the first fix pass (version 2) must not be served for 24h."""
    from datetime import datetime, timezone

    from app.services import health_check_service as hc

    good = HealthCheckResponse(symbol="TEST", overall_rating="good", passed_count=1,
                               total_count=1, metrics=[]).model_dump()

    class _Q:
        def __init__(self, rows):
            self._rows = rows

        def __getattr__(self, _name):
            return lambda *a, **k: self

        def execute(self):
            return type("R", (), {"data": self._rows})()

    class _DB:
        def __init__(self, rows):
            self._q = _Q(rows)

        def table(self, _name):
            return self._q

    def _row(version):
        return [{"response_json": {**good, "payload_version": version},
                 "cached_at": datetime.now(timezone.utc).isoformat(),
                 "next_earnings_date": None}]

    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = _DB(_row(2))
    assert svc._check_supabase_cache("TEST") is None, "a v2 row was served"
    svc.supabase = _DB(_row(hc._HC_PAYLOAD_VERSION))
    assert svc._check_supabase_cache("TEST") is not None, "the current version must be served"


# ── R41: a barely-positive peer median is no anchor ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("median", [0.01, 0.02, 1e-6])
async def test_tiny_positive_roe_median_falls_back_to_absolute(monkeypatch, median):
    from app.services.health_check_service import _absolute_gauge, _absolute_status

    resp = await _build(
        monkeypatch, _answers(key_metrics=[{"returnOnEquityTTM": 0.05}]),
        bench=dict(_BENCH, roe=median),
    )
    roe = _metric(resp, "roe")
    assert roe is not None
    assert roe.comparison_value is None and roe.percent_difference is None, (
        f"median {median}: a near-zero peer median was still used as the anchor"
    )
    assert roe.status == _absolute_status("roe", 5.0) == "negative", (
        "a mediocre 5% ROE became the card's top pass"
    )
    assert roe.gauge_position == round(_absolute_gauge("roe", 5.0), 2)
    text = _rendered(roe)
    assert "Exceptional" not in text and "sector average" not in text, text
    assert not (roe.highlighted_value or "").endswith("x"), text
    _assert_scoring_is_consistent(resp, unscored=set())


@pytest.mark.asyncio
@pytest.mark.parametrize("median,company,expect_status", [
    (0.15, 0.05, "negative"),     # the ordinary case: compared, -67% below
    (0.025, 0.05, "positive"),    # just above the floor: still a real comparison
    (0.12, 0.60, "positive"),     # a big gap against a normal median keeps its multiplier
])
async def test_normal_roe_median_is_still_compared(monkeypatch, median, company, expect_status):
    resp = await _build(
        monkeypatch, _answers(key_metrics=[{"returnOnEquityTTM": company}]),
        bench=dict(_BENCH, roe=median),
    )
    roe = _metric(resp, "roe")
    assert roe is not None
    assert roe.comparison_value == round(median * 100, 2)
    assert roe.percent_difference is not None
    assert roe.status == expect_status
    assert "sector average" in _rendered(roe)


@pytest.mark.asyncio
async def test_tiny_interest_coverage_median_falls_back_to_absolute(monkeypatch):
    from app.services.health_check_service import _absolute_status

    resp = await _build(
        monkeypatch, _answers(ratios=[dict(_RATIOS, interestCoverageRatioTTM=3.0)]),
        bench=dict(_BENCH, interest_coverage=0.5),
    )
    ic = _metric(resp, "interest_coverage")
    assert ic is not None
    assert ic.comparison_value is None and ic.percent_difference is None
    assert ic.status == _absolute_status("interest_coverage", 3.0) == "neutral"
    assert "Outsized" not in _rendered(ic) and "x" not in (ic.highlighted_value or "")


@pytest.mark.asyncio
async def test_normal_interest_coverage_median_is_still_compared(monkeypatch):
    resp = await _build(
        monkeypatch, _answers(ratios=[dict(_RATIOS, interestCoverageRatioTTM=3.0)]),
        bench=dict(_BENCH, interest_coverage=1.5),
    )
    ic = _metric(resp, "interest_coverage")
    assert ic is not None and ic.comparison_value == 1.5 and ic.percent_difference == 100.0


@pytest.mark.asyncio
async def test_near_debt_free_de_median_falls_back_to_absolute(monkeypatch):
    from app.services.health_check_service import _absolute_status

    resp = await _build(
        monkeypatch, _answers(ratios=[dict(_RATIOS, debtToEquityRatioTTM=0.5)]),
        bench=dict(_BENCH, debt_to_equity=0.04),
    )
    de = _metric(resp, "debt_to_equity")
    assert de is not None
    assert de.comparison_value is None and de.percent_difference is None
    assert de.status == _absolute_status("debt_to_equity", 0.5) == "neutral"
    assert "Significantly leveraged" not in _rendered(de)


@pytest.mark.asyncio
async def test_normal_de_median_is_still_compared(monkeypatch):
    resp = await _build(monkeypatch)        # D/E 0.5 vs a 0.6 median
    de = _metric(resp, "debt_to_equity")
    assert de is not None and de.comparison_value == 0.6 and de.percent_difference is not None


def test_floors_only_cover_metrics_whose_median_can_sit_near_zero():
    """P/E, current and quick ratio keep the bare non-positive rule; every floor is a
    positive number (a zero/negative floor would silently re-open R41)."""
    from app.services.health_check_service import (
        METRIC_DEFS, _MIN_USABLE_MEDIAN, _min_usable_median,
    )

    types = {m["type"] for m in METRIC_DEFS}
    assert set(_MIN_USABLE_MEDIAN) <= types, "a floor keyed on a metric that does not exist"
    assert all(v > 0 for v in _MIN_USABLE_MEDIAN.values())
    for t in ("pe_ratio", "current_ratio", "quick_ratio"):
        assert 0 < _min_usable_median(t) < 1e-6


# ── R42: a zero-filled balance-sheet equity does not override a positive D/E ──


@pytest.mark.asyncio
async def test_zero_equity_beside_a_positive_de_keeps_roe_judged(monkeypatch):
    resp = await _build(monkeypatch, _answers(
        ratios=[dict(_RATIOS, debtToEquityRatioTTM=0.5)],
        bs=[dict(_BS, totalStockholdersEquity=0.0)],
    ))
    roe = _metric(resp, "roe")
    de = _metric(resp, "debt_to_equity")
    assert de is not None and de.status == "positive"
    assert roe is not None and roe.highlighted_value != "N/M", (
        "ROE 30% was replaced by 'N/M … equity is negative' beside a healthy D/E"
    )
    assert roe.status == "positive" and roe.comparison_value == 15.0
    assert "Not meaningful" not in roe.insight_text
    _assert_scoring_is_consistent(resp, unscored=set())
    assert resp.total_count == len(resp.metrics), "the ROE pass must count"


@pytest.mark.asyncio
@pytest.mark.parametrize("label,de,equity,word", [
    # Equity reported as 0 and nothing contradicts it: N/M, but never called "negative".
    ("zero_no_de", None, 0.0, "reported as zero"),
    ("zero_zero_de", 0.0, 0.0, "reported as zero"),
    # A strictly negative witness always wins — incl. ratios-TTM lagging a new quarter.
    ("negative_bs_positive_de", 0.5, -1.0e9, "negative"),
    ("negative_de_zero_bs", -13.0, 0.0, "negative"),
    ("negative_de_no_bs", -13.0, None, "negative"),
])
async def test_not_meaningful_roe_names_the_equity_state_it_saw(
    monkeypatch, label, de, equity, word,
):
    from app.services.health_check_service import NOT_MEANINGFUL

    ratios = dict(_RATIOS)
    if de is None:
        ratios.pop("debtToEquityRatioTTM")
    else:
        ratios["debtToEquityRatioTTM"] = de
    bs = dict(_BS)
    if equity is None:
        bs.pop("totalStockholdersEquity")
    else:
        bs["totalStockholdersEquity"] = equity
    resp = await _build(monkeypatch, _answers(ratios=[ratios], bs=[bs]))

    roe = _metric(resp, "roe")
    assert roe is not None and roe.highlighted_value == NOT_MEANINGFUL, label
    assert roe.insight_text == f"Not meaningful: shareholder equity is {word}.", label
    if word != "negative":
        assert "negative" not in roe.insight_text, f"{label}: a zero was called negative"
    _assert_scoring_is_consistent(resp, unscored={"roe"})
