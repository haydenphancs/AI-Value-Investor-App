"""Profitability and Financial Health snapshot cards, 2026-10-07.

Profitability: margins were the latest FISCAL year's Profit Power figures scored against
TTM peer medians (two different twelve-month windows). They are TTM now (ratios-ttm,
through `_ttm_margin`'s guards); Profit Power fills only a margin TTM cannot give, shown
without a peer comparison or a score. ROE on negative / zero shareholder equity (MCD,
SBUX, BKNG — rated 1/5 on a sign-flipped ROE) reads "N/M", unscored, and the rating is
re-normalised over the scored rows. No label prints a negative multiple.

Financial Health: `peer_level` per compared metric on both paths, the local fallback
omits the liquidity rows for a bank exactly as the Health Check does, and both cards
stamp `computed_at`. Hermetic: stubbed FMP, Profit Power, Health Check and lookup.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.integrations.fmp import FMPNotEntitledException, FMPRateLimitException
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.schemas.stock_overview import SnapshotItemResponse, snapshot_build_time
from app.services import profitability_snapshot_service as ps

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# A negative "x" multiple anywhere in a label: "-14.40x sector avg", "(-0.83x …".
_NEG_MULTIPLE = re.compile(r"-\d+(\.\d+)?x\b")


# ── harness ──────────────────────────────────────────────────────────────────────


class _FakeFMP:
    def __init__(self, **answers: Any) -> None:
        self._answers = answers
        self.calls: List[str] = []

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            self.calls.append(name)
            answer = self._answers.get(name, [])
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        return _call


class _RichLookup:
    def __init__(self, table: Optional[Dict[str, tuple]] = None):
        self.table = table or {}

    def get_current_benchmarks(self, industry, sector, metrics):
        return {
            m: ({"value": self.table[m][0], "level": self.table[m][1],
                 "peer_group_name": "Peers", "n": 60} if m in self.table else None)
            for m in metrics
        }

    def get_current_benchmark_values(self, industry, sector, metrics):
        return {m: (self.table[m][0] if m in self.table else None) for m in metrics}


_PROFILE = {"symbol": "MCD", "sector": "Consumer Cyclical", "industry": "Restaurants",
            "mktCap": 2.0e11}
_BENCH = {"gross_margin": (0.40, "industry"), "operating_margin": (0.15, "industry"),
          "net_margin": (0.08, "sector"), "roe": (0.20, "sector"), "roa": (0.06, "industry")}
_RATIOS = {"revenuePerShareTTM": 36.0, "grossProfitMarginTTM": 0.57,
           "operatingProfitMarginTTM": 0.46, "netProfitMarginTTM": 0.32,
           "debtToEquityRatioTTM": 1.2}
_KM = {"returnOnEquityTTM": 0.30, "returnOnAssetsTTM": 0.14}
_BS = {"totalStockholdersEquity": 5.0e9}


def _pp(**latest) -> ProfitPowerResponse:
    return ProfitPowerResponse(symbol="MCD", quarterly=[], annual=[
        ProfitPowerDataPointSchema(period="2024", gross_margin=50.0, operating_margin=40.0,
                                   net_margin=25.0),
        ProfitPowerDataPointSchema(period="2025", **latest),
    ])


def _prof(monkeypatch, *, pp: Any = None, ratios=None, km=None, bs=None, profile=None,
          bench=None):
    from app.services import profit_power_service

    pp_result = _pp(gross_margin=55.0, operating_margin=44.0, net_margin=30.0) if pp is None else pp

    class _ProfitPower:
        async def get_profit_power(self, ticker):
            if isinstance(pp_result, BaseException):
                raise pp_result
            return pp_result

    # Function-local import in `_compute_with_status`: patch the SOURCE module.
    monkeypatch.setattr(profit_power_service, "get_profit_power_service", lambda: _ProfitPower())
    monkeypatch.setattr(ps, "get_sector_benchmark_lookup",
                        lambda: _RichLookup(_BENCH if bench is None else bench))
    svc = ps.ProfitabilitySnapshotService.__new__(ps.ProfitabilitySnapshotService)
    svc.supabase = None
    svc.fmp = _FakeFMP(
        get_company_profile=dict(_PROFILE) if profile is None else profile,
        get_ratios_ttm=[dict(_RATIOS)] if ratios is None else ratios,
        get_key_metrics_ttm=[dict(_KM)] if km is None else km,
        get_balance_sheet=[dict(_BS)] if bs is None else bs,
    )
    return svc


def _m(snap: SnapshotItemResponse, key: str):
    return next(m for m in snap.metrics if m.metric_key == key)


# ── 1. Margins are TTM against TTM medians ───────────────────────────────────────


@pytest.mark.asyncio
async def test_margins_are_ttm_not_the_latest_fiscal_year(monkeypatch):
    svc = _prof(monkeypatch)
    snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == []
    gross, op, net = _m(snap, "gross_margin"), _m(snap, "operating_margin"), _m(snap, "net_margin")
    assert (gross.value, op.value, net.value) == ("57.00%", "46.00%", "32.00%"), (
        "the Profit Power fiscal-year figures (55 / 44 / 30) won over TTM"
    )
    assert gross.name == "Gross Margin (1.43x sector avg 40.0%)" and gross.peer_level == "industry"
    assert net.name == "Net Margin (4.00x sector avg 8.0%)" and net.peer_level == "sector"
    assert all(m.score is not None for m in snap.metrics)
    assert snap.computed_at and _ISO_Z.match(snap.computed_at)


@pytest.mark.asyncio
async def test_a_margin_ttm_cannot_give_falls_back_to_the_fiscal_year_unscored(monkeypatch):
    """No TTM revenue/share → `_ttm_margin` refuses every TTM margin; Profit Power's latest
    fiscal year is shown — but a fiscal-year figure has no TTM peer to be compared with."""
    ratios = [{k: v for k, v in _RATIOS.items() if k != "revenuePerShareTTM"}]
    svc = _prof(monkeypatch, ratios=ratios)
    snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == []
    for key, shown in (("gross_margin", "55.00%"), ("operating_margin", "44.00%"),
                       ("net_margin", "30.00%")):
        m = _m(snap, key)
        assert m.value == shown
        assert "sector" not in m.name and m.score is None and m.peer_level is None
    # Rating re-normalised over ROE + ROA only (the two scored rows).
    roe, roa = _m(snap, "roe").score, _m(snap, "roa").score
    assert snap.weighted_score == pytest.approx((roe * 0.25 + roa * 0.15) / 0.40, abs=1e-3)


@pytest.mark.asyncio
async def test_profit_power_failure_is_not_degradation_when_every_margin_is_ttm(monkeypatch):
    svc = _prof(monkeypatch, pp=RuntimeError("profit power exploded"))
    _snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == [], "an unused fallback leg must not refuse a complete TTM card"


@pytest.mark.asyncio
async def test_profit_power_failure_is_degradation_when_its_fallback_was_needed(monkeypatch):
    svc = _prof(monkeypatch, pp=RuntimeError("profit power exploded"), ratios=[])
    snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == ["profit_power"]
    assert _m(snap, "net_margin").value == "—"


@pytest.mark.asyncio
async def test_a_failed_ratios_leg_is_degradation(monkeypatch):
    svc = _prof(monkeypatch, ratios=FMPRateLimitException("429"))
    snap, degraded = await svc._compute_with_status("MCD")
    assert "ratios_ttm" in degraded
    # ...and the margins fall back to the fiscal year, unscored
    assert _m(snap, "net_margin").value == "30.00%" and _m(snap, "net_margin").score is None


# ── 2. Loss-makers: no negative multiple, ever ───────────────────────────────────


@pytest.mark.asyncio
async def test_loss_maker_margins_print_the_median_without_a_multiple(monkeypatch):
    ratios = [dict(_RATIOS, operatingProfitMarginTTM=-0.083, netProfitMarginTTM=-0.125)]
    svc = _prof(monkeypatch, ratios=ratios, km=[{"returnOnEquityTTM": -0.144,
                                                 "returnOnAssetsTTM": -0.05}])
    snap, _ = await svc._compute_with_status("MCD")
    net, op = _m(snap, "net_margin"), _m(snap, "operating_margin")
    assert net.value == "-12.50%" and net.name == "Net Margin (sector avg 8.0%)"
    assert op.name == "Operating Margin (sector avg 15.0%)"
    assert net.score == 1 and net.peer_level == "sector"
    roe = _m(snap, "roe")
    assert roe.value == "-14.40%" and roe.name == "Return on Equity (ROE) (sector avg 20.0%)"
    for m in snap.metrics:
        assert not _NEG_MULTIPLE.search(m.name), m.name


@pytest.mark.parametrize("val", [-0.01, -14.4, -9999.0])
def test_label_never_prints_a_negative_multiple(val):
    assert not _NEG_MULTIPLE.search(ps._label_with_sector("ROE", val, 0.2))
    assert ps._label_with_sector("ROE", val, 0.2) == "ROE (sector avg 20.0%)"


@pytest.mark.parametrize("median", [None, 0.0, -0.3, 0.0004, float("nan"), float("inf")])
def test_an_unusable_margin_median_is_no_comparison(median):
    assert ps._label_with_sector("Net Margin", 12.0, median) == "Net Margin"
    assert ps._usable_median(median) is None


# ── 3. Negative equity: ROE is "N/M", unscored, weights re-normalised ────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("label,de,roe,equity", [
    # McDonald's-shaped: profitable, negative equity → FMP ROE comes back NEGATIVE.
    ("mcd_profit_on_negative_equity", -10.0, -2.16, -4.0e9),
    # Boeing-shaped: a loss on negative equity → FMP ROE comes back POSITIVE.
    ("loss_on_negative_equity", -13.0, 3.03, -3.0e9),
    # D/E missing — the balance sheet is the second witness.
    ("de_missing_balance_sheet_negative", None, 3.03, -1.0e9),
    # Ratios-TTM lagging a quarter that turned equity negative.
    ("de_positive_but_balance_sheet_negative", 0.8, 1.10, -2.0e9),
    # Zero equity with no contradicting D/E.
    ("zero_equity", None, 0.8, 0.0),
])
async def test_roe_on_negative_equity_is_not_meaningful(monkeypatch, label, de, roe, equity):
    ratios = dict(_RATIOS)
    if de is None:
        ratios.pop("debtToEquityRatioTTM")
    else:
        ratios["debtToEquityRatioTTM"] = de
    svc = _prof(monkeypatch, ratios=[ratios],
                km=[{"returnOnEquityTTM": roe, "returnOnAssetsTTM": 0.14}],
                bs=[{"totalStockholdersEquity": equity}])
    snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == [], label
    row = _m(snap, "roe")
    assert row.value == "N/M", label
    assert row.name == "Return on Equity (ROE)" and row.score is None and row.peer_level is None
    others = [(_m(snap, k).score, w) for k, w in (("gross_margin", 0.15),
                                                  ("operating_margin", 0.20),
                                                  ("net_margin", 0.25), ("roa", 0.15))]
    expected = sum(s * w for s, w in others) / sum(w for _, w in others)
    assert snap.weighted_score == pytest.approx(expected, abs=1e-3), (
        "ROE must leave the rating entirely — no sentinel 3, no half credit"
    )
    for m in snap.metrics:
        assert not _NEG_MULTIPLE.search(m.name), m.name


@pytest.mark.asyncio
async def test_a_zero_equity_beside_a_positive_de_is_unreported_and_roe_is_judged(monkeypatch):
    svc = _prof(monkeypatch, bs=[{"totalStockholdersEquity": 0.0}])
    snap, _ = await svc._compute_with_status("MCD")
    roe = _m(snap, "roe")
    assert roe.value == "30.00%" and roe.score is not None
    assert roe.name == "Return on Equity (ROE) (1.50x sector avg 20.0%)"


@pytest.mark.asyncio
async def test_positive_equity_roe_is_judged(monkeypatch):
    snap, _ = await _prof(monkeypatch)._compute_with_status("MCD")
    assert _m(snap, "roe").value == "30.00%" and _m(snap, "roe").score == 5   # 1.50x peers


@pytest.mark.asyncio
@pytest.mark.parametrize("de,km,expected", [
    # Review round 3 (R3-CARDS-4): a positive D/E is a witness on its own — the failed
    # second witness is logged, not counted (it was ["balance_sheet"], which dropped the
    # card from almost every report on one 429).
    (1.2, [dict(_KM)], []),
    (None, [dict(_KM)], ["balance_sheet"]),         # ROE shown, no D/E: bs decides
    (0.0, [dict(_KM)], ["balance_sheet"]),          # D/E 0 says nothing about the sign
    (-3.0, [dict(_KM)], []),                        # D/E already ruled: bs irrelevant
    (1.2, [{"returnOnAssetsTTM": 0.14}], []),       # no ROE to judge: bs irrelevant
])
async def test_a_failed_balance_sheet_counts_only_when_it_decides_the_roe(
    monkeypatch, de, km, expected,
):
    svc = _prof(monkeypatch, ratios=[dict(_RATIOS, debtToEquityRatioTTM=de)], km=km,
                bs=FMPRateLimitException("429"))
    _snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == expected


@pytest.mark.asyncio
async def test_an_unentitled_balance_sheet_is_not_degradation(monkeypatch):
    svc = _prof(monkeypatch, bs=FMPNotEntitledException("402"))
    _snap, degraded = await svc._compute_with_status("MCD")
    assert degraded == []


@pytest.mark.asyncio
async def test_an_all_nm_card_is_not_a_measurement(monkeypatch):
    """Only an "N/M" ROE: nothing is scored, so nothing is rated (rating 0, no weighted
    score — it was a sentinel 3.0) — served, not cached."""
    monkeypatch.setattr(ps, "_cache", {})
    monkeypatch.setattr(ps, "_inflight", {})
    svc = _prof(monkeypatch, pp=_pp(), ratios=[{"debtToEquityRatioTTM": -5.0}],
                km=[{"returnOnEquityTTM": -2.0}], bs=[{"totalStockholdersEquity": -1e9}])
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    written: List[str] = []
    monkeypatch.setattr(svc, "_upsert_supabase_cache", lambda t, r: written.append(t))
    snap, degraded = await svc.get_profitability_snapshot_with_status("MCD")
    assert _m(snap, "roe").value == "N/M"
    assert snap.weighted_score is None and snap.rating == 0
    assert degraded == ["no_values"] and written == []


# The equity test is a REPLICA of the Health Check's: pin the two against each other on
# the same inputs, through the Health Check's real build.

_HC_RATIOS = {"debtToEquityRatioTTM": 0.5, "priceToEarningsRatioTTM": 25.0,
              "currentRatioTTM": 1.5, "interestCoverageRatioTTM": 20.0, "quickRatioTTM": 1.2}
_HC_BS = {"totalAssets": 500e9, "totalLiabilities": 250e9, "totalCurrentAssets": 150e9,
          "totalCurrentLiabilities": 100e9, "retainedEarnings": 100e9}
_HC_QUARTER = {"operatingIncome": 30e9, "interestExpense": 1e9, "revenue": 80e9,
               "netIncome": 25e9, "ebitda": 35e9}


@pytest.mark.asyncio
@pytest.mark.parametrize("de,equity", [
    (0.5, 250e9), (-10.0, -4e9), (None, -1e9), (None, 0.0), (0.5, 0.0), (-0.1, 250e9),
    (0.0, 0.0), (None, None), (0.8, -2e9), (None, 250e9),
])
async def test_equity_state_matches_the_health_check(monkeypatch, de, equity):
    from app.services import health_check_service as hc

    ratios = dict(_HC_RATIOS)
    if de is None:
        ratios.pop("debtToEquityRatioTTM")
    else:
        ratios["debtToEquityRatioTTM"] = de
    bs = dict(_HC_BS)
    if equity is not None:
        bs["totalStockholdersEquity"] = equity
    income = [dict(_HC_QUARTER, date=d) for d in
              ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")]
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: _RichLookup())
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = _FakeFMP(
        get_company_profile={"symbol": "TEST", "sector": "Technology",
                             "industry": "Software - Infrastructure", "mktCap": 2.0e12},
        get_ratios_ttm=[ratios], get_key_metrics_ttm=[{"returnOnEquityTTM": 0.30}],
        get_balance_sheet=[bs], get_income_statement=income, get_earning_calendar_full=[],
    )
    response, _next = await svc._build_health_check("TEST")
    roe = next(m for m in response.metrics if m.type == "roe")
    hc_not_meaningful = roe.highlighted_value == hc.NOT_MEANINGFUL
    assert hc_not_meaningful == (ps._equity_state(de, equity) is not None), (de, equity)


# ── 4. The composite ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("parts,expected", [
    ([(None, 0.15), (None, 0.25)], None),   # nothing scored: no composite (was a 3.0)
    ([(5, 0.15), (None, 0.25)], 5.0),
    ([(5, 0.25), (1, 0.25)], 3.0),
    ([(1, 0.15), (1, 0.20), (1, 0.25), (1, 0.25), (1, 0.15)], 1.0),
    ([], None),
    ([(4, 0.0)], None),                     # zero total weight: nothing, no ZeroDivision
])
def test_weighted_rating(parts, expected):
    got = ps._weighted_rating(parts)
    if expected is None:
        assert got is None
        return
    assert got == pytest.approx(expected)
    assert 1.0 <= got <= 5.0


# ── 5. Version bump and computed_at on the profitability row ─────────────────────


class _Table:
    def __init__(self, rows, sink):
        self._rows, self._sink = rows, sink

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def upsert(self, payload, **k):
        self._sink.append(payload)
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _Supabase:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.upserts: List[Dict[str, Any]] = []

    def table(self, name):
        return _Table(self.rows, self.upserts)


def _row(json_data, age=timedelta(hours=2)):
    cached_at = datetime.now(timezone.utc) - age
    return {"response_json": json_data, "cached_at": cached_at.isoformat()}, cached_at


@pytest.mark.parametrize("modname,clsname,category,current", [
    ("app.services.profitability_snapshot_service", "ProfitabilitySnapshotService",
     "Profitability", 3),
    ("app.services.health_snapshot_service", "HealthSnapshotService", "Financial Health", 5),
])
def test_version_bump_rebuilds_old_rows_and_keeps_the_build_time(
    modname, clsname, category, current,
):
    import importlib

    mod = importlib.import_module(modname)
    assert mod._SNAPSHOT_PAYLOAD_VERSION == current
    cls = getattr(mod, clsname)
    body = SnapshotItemResponse(category=category, rating=4, metrics=[],
                                computed_at="2026-10-07T09:15:00Z").model_dump()
    svc = cls.__new__(cls)
    for old in (None, current - 1):
        stale = dict(body) if old is None else {**body, mod._VERSION_KEY: old}
        svc.supabase = _Supabase([_row(stale)[0]])
        assert svc._check_supabase_cache("MCD") is None, f"{category} v{old} row was served"
    svc.supabase = _Supabase([_row({**body, mod._VERSION_KEY: current})[0]])
    got = svc._check_supabase_cache("MCD")
    assert got is not None and got.computed_at == "2026-10-07T09:15:00Z"
    # a current row from a build without the key: its cached_at
    no_key = {k: v for k, v in body.items() if k != "computed_at"}
    row, cached_at = _row({**no_key, mod._VERSION_KEY: current})
    svc.supabase = _Supabase([row])
    assert svc._check_supabase_cache("MCD").computed_at == snapshot_build_time(cached_at)


# ── 6. Financial Health snapshot: peer_level and the bank gate ───────────────────


def _hc_metric(kind, value, comparison=None, status="positive", level=None):
    return HealthCheckMetricSchema(type=kind, value=value, comparison_value=comparison,
                                   gauge_position=0.5, status=status, insight_text="ok",
                                   peer_level=level)


def _health_service(monkeypatch, health, *, profile=None, bs=None, income=None, bench=None):
    from app.services import health_check_service
    from app.services import health_snapshot_service as hs

    class _HealthCheck:
        async def get_health_check(self, ticker):
            if isinstance(health, BaseException):
                raise health
            return health

    monkeypatch.setattr(health_check_service, "get_health_check_service", lambda: _HealthCheck())
    monkeypatch.setattr(hs, "get_sector_benchmark_lookup", lambda: _RichLookup(bench or {}))
    svc = hs.HealthSnapshotService.__new__(hs.HealthSnapshotService)
    svc.supabase = None
    quarters = income if income is not None else [
        dict(_HC_QUARTER, date=d) for d in ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")
    ]
    svc.fmp = _FakeFMP(
        get_company_profile=profile or {"symbol": "TEST", "sector": "Technology",
                                        "industry": "Software - Infrastructure", "mktCap": 2e12},
        get_balance_sheet=[bs or dict(_HC_BS, totalStockholdersEquity=250e9, totalDebt=50e9,
                                      cashAndCashEquivalents=40e9, netReceivables=20e9)],
        get_income_statement=quarters,
    )
    return hs, svc


@pytest.mark.asyncio
async def test_health_main_path_carries_the_health_checks_level(monkeypatch):
    health = HealthCheckResponse(
        symbol="TEST", overall_rating="good", passed_count=3, total_count=3, metrics=[
            _hc_metric("debt_to_equity", 0.4, 0.6, level="industry"),
            _hc_metric("current_ratio", 1.8, 1.4, level="sector"),
            _hc_metric("quick_ratio", 1.2, None, level="sector"),     # no comparison
            _hc_metric("interest_coverage", 20.0, 15.0),              # pre-field HC row
            _hc_metric("altman_z_score", 4.2),
        ])
    _hs, svc = _health_service(monkeypatch, health)
    snap, degraded = await svc._compute_with_status("TEST")
    assert degraded == []
    by = {m.metric_key: m for m in snap.metrics}
    assert by["debt_to_equity"].name == "Debt-to-Equity (vs sector 0.60)"
    assert by["debt_to_equity"].peer_level == "industry"
    assert by["current_ratio"].peer_level == "sector"
    assert by["quick_ratio"].peer_level is None and "sector" not in by["quick_ratio"].name
    assert by["interest_coverage"].peer_level is None    # no level on the HC row → generic
    assert by["altman_z"].peer_level is None
    for m in snap.metrics:
        assert "industry avg" not in m.name.lower()
        assert m.peer_level is None or "vs sector" in m.name
    assert snap.computed_at and _ISO_Z.match(snap.computed_at)


@pytest.mark.asyncio
async def test_health_fallback_reports_each_cells_level(monkeypatch):
    _hs, svc = _health_service(
        monkeypatch, RuntimeError("health check exploded"),
        bench={"debt_to_equity": (0.6, "industry"), "current_ratio": (1.4, "sector"),
               "interest_coverage": (-2.0, "sector"), "quick_ratio": (0.0, "industry")},
    )
    snap, degraded = await svc._compute_with_status("TEST")
    assert degraded == ["health_check"]     # the fallback card is never persisted
    by = {m.metric_key: m for m in snap.metrics}
    assert by["debt_to_equity"].peer_level == "industry"
    assert by["current_ratio"].peer_level == "sector"
    # A non-positive median is no benchmark: no "vs sector -2.00", no level.
    for key in ("interest_coverage", "quick_ratio"):
        assert "sector" not in by[key].name and by[key].peer_level is None, by[key]
    assert snap.computed_at and _ISO_Z.match(snap.computed_at)


@pytest.mark.asyncio
async def test_health_fallback_omits_liquidity_rows_for_a_bank(monkeypatch):
    bank = {"symbol": "C", "sector": "Financial Services", "industry": "Banks - Diversified",
            "mktCap": 1.4e11}
    _hs, svc = _health_service(monkeypatch, RuntimeError("health check exploded"),
                               profile=bank, bench={"debt_to_equity": (1.5, "industry")})
    snap, _ = await svc._compute_with_status("C")
    keys = [m.metric_key for m in snap.metrics]
    assert keys == ["debt_to_equity"], keys          # no Altman Z, CR, IC or QR for a bank
    # control: an operating company keeps all four ratio rows
    _hs, svc = _health_service(monkeypatch, RuntimeError("health check exploded"))
    snap, _ = await svc._compute_with_status("TEST")
    assert {"current_ratio", "interest_coverage", "quick_ratio"} <= {
        m.metric_key for m in snap.metrics}
