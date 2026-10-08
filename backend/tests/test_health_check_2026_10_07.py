"""Health Check, 2026-10-07: financial balance sheets, the peer level, the payload version.

1. Banks, insurers and capital-markets firms get NO interest coverage, current ratio or
   quick ratio. Those ratios assume an operating company; production rows showed C with
   all three red and BAC's 0.54 current ratio as "positive". The rows are omitted (not
   shown, not scored), the way Altman Z already is — so `passed_count` / `total_count` /
   `overall_rating` must still agree with the rows that remain.
2. Every compared metric reports `peer_level` — the level of the median it was compared
   with — and is None exactly when nothing was compared.
3. The insight text names that level: it said "sector average" even when the median was
   the INDUSTRY's. iOS renders the text verbatim and parses none of it.
4. `_HC_PAYLOAD_VERSION` 6 (5 on the first pass): rows written under the old rules are
   rebuilt.

Each test drives the REAL `_build_health_check` against a stubbed FMP client and a stubbed
peer lookup (hermetic), reusing the deep-check fixtures of `test_health_check_deepcheck`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import test_health_check_deepcheck as hcd
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
from app.services import financials_metric_gate as gate
from app.services import health_check_service as hc

_LIQUIDITY = {"interest_coverage", "current_ratio", "quick_ratio"}
_DATA = Path(__file__).resolve().parents[1] / "data"


# ── harness ──────────────────────────────────────────────────────────────────────────


class _RecordingLookup:
    """`get_current_benchmarks` answering `cells` ({metric: cell | None}); every call's
    metric list is recorded so a test can see what the build ASKED for."""

    def __init__(self, cells: Dict[str, Any]) -> None:
        self.cells = cells
        self.asked: List[List[str]] = []

    def get_current_benchmarks(self, industry, sector, metrics):
        self.asked.append(list(metrics))
        return {m: self.cells.get(m) for m in metrics}


def _cells(level: str = "sector", bench: Optional[Dict[str, float]] = None,
           levels: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Rich cells from the deep-check medians; `levels` overrides one metric's level."""
    table = hcd._BENCH if bench is None else bench
    levels = levels or {}
    return {
        m: {"value": v, "level": levels.get(m, level), "peer_group_name": "Peers", "n": 50}
        for m, v in table.items()
    }


async def _build(monkeypatch, answers=None, *, cells=None,
                 lookup: Optional[_RecordingLookup] = None) -> HealthCheckResponse:
    lookup = lookup or _RecordingLookup(_cells() if cells is None else cells)
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: lookup)
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = hcd._FakeFMP(answers or hcd._answers())
    response, _next = await svc._build_health_check("TEST")
    return response


def _profile(sector: str, industry: Any) -> Dict[str, Any]:
    return dict(hcd._PROFILE, sector=sector, industry=industry)


# C-shaped: a bank's "current" figures and its funding-cost interest expense read red on
# every one of the three rows against an operating-company median.
_BANK_RATIOS = dict(hcd._RATIOS, currentRatioTTM=0.30, quickRatioTTM=0.25,
                    interestCoverageRatioTTM=0.40, debtToEquityRatioTTM=1.6,
                    priceToEarningsRatioTTM=11.0)
_BANK_KM = {"returnOnEquityTTM": 0.07}


def _assert_counts_consistent(resp: HealthCheckResponse, unscored: frozenset = frozenset()):
    scored = [m for m in resp.metrics if m.type not in unscored]
    passed = sum(1 for m in scored if m.status == "positive")
    neutrals = sum(1 for m in scored if m.status == "neutral")
    assert resp.passed_count == passed
    assert resp.total_count == len(scored)
    assert resp.overall_rating == hc._overall_rating(passed + 0.5 * neutrals, len(scored))


# ── 1. financial balance sheets ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("sector,industry", [
    ("Financial Services", "Banks - Diversified"),           # C, BAC, JPM
    ("Financial Services", "Banks - Regional"),
    ("Financial Services", "Banks"),
    ("Financial Services", "Insurance - Life"),              # MET
    ("Financial Services", "Insurance - Property & Casualty"),
    ("Financial Services", "Insurance - Diversified"),       # BRK-B
    # ("Insurance - Brokers" keeps interest coverage since review round 2: see
    #  tests/test_cards_round2_2026_10_07.py)
    ("Financial Services", "Financial - Capital Markets"),   # GS, MS, SCHW
    ("Financial Services", "Investment - Banking & Investment Services"),
    ("Financial Services", "Asset Management"),              # BLK, KKR
    ("Financial Services", "Financial - Credit Services"),   # AXP, COF
    ("Financial Services", "Financial - Mortgages"),
    ("Real Estate", "REIT - Mortgage"),                       # AGNC, NLY: leveraged lenders
    # Spelling drift: the older feed's em dash, odd case and spacing.
    ("Financial Services", "Banks—Regional"),
    ("Financial Services", "  banks  -  diversified "),
    ("Financial Services", "INSURANCE – LIFE"),
])
async def test_financials_omit_the_three_liquidity_rows(monkeypatch, sector, industry):
    lookup = _RecordingLookup(_cells())
    resp = await _build(monkeypatch, hcd._answers(
        profile=_profile(sector, industry), ratios=[dict(_BANK_RATIOS)],
        key_metrics=[dict(_BANK_KM)],
    ), lookup=lookup)

    types = {m.type for m in resp.metrics}
    assert not (types & _LIQUIDITY), f"{industry!r}: {sorted(types & _LIQUIDITY)} still shown"
    assert {"debt_to_equity", "pe_ratio", "roe"} <= types, "the meaningful rows must stay"
    assert "altman_z_score" not in types, "the existing Altman gate still applies"
    assert resp.degraded == [], "not applicable is an answer, not a degradation"
    _assert_counts_consistent(resp)
    assert resp.total_count == 3
    # Nothing is asked of the lookup for rows that are never shown.
    assert lookup.asked and all(not (set(a) & _LIQUIDITY) for a in lookup.asked), lookup.asked


@pytest.mark.asyncio
async def test_a_c_shaped_bank_no_longer_reads_poor_from_three_meaningless_reds(monkeypatch):
    """C: CR 0.30 / QR 0.25 / IC 0.40 against operating-company medians were three fails
    that dragged the rating. With them omitted, the rating comes from D/E, P/E and ROE."""
    answers = hcd._answers(profile=_profile("Financial Services", "Banks - Diversified"),
                           ratios=[dict(_BANK_RATIOS)], key_metrics=[dict(_BANK_KM)])
    resp = await _build(monkeypatch, answers)
    # The same numbers on an ordinary company still produce the three red rows.
    ordinary = await _build(monkeypatch, dict(
        answers, get_company_profile=_profile("Technology", "Software - Infrastructure")))
    red = {m.type for m in ordinary.metrics if m.status == "negative"}
    assert _LIQUIDITY <= red, "control: the shape must be red where the ratios apply"
    assert ordinary.total_count == resp.total_count + 3 + 1   # + the three rows + Altman Z
    assert not ({m.type for m in resp.metrics} & _LIQUIDITY)


@pytest.mark.asyncio
async def test_a_bac_shaped_current_ratio_is_not_judged(monkeypatch):
    """BAC's 0.54 "current ratio" against a sub-0.5 median read POSITIVE."""
    resp = await _build(monkeypatch, hcd._answers(
        profile=_profile("Financial Services", "Banks - Diversified"),
        ratios=[dict(_BANK_RATIOS, currentRatioTTM=0.54)],
    ), cells=_cells(bench=dict(hcd._BENCH, current_ratio=0.45)))
    assert all(m.type != "current_ratio" for m in resp.metrics)


@pytest.mark.asyncio
@pytest.mark.parametrize("sector,industry", [
    ("Technology", "Software - Infrastructure"),
    ("Financial Services", "Financial - Data & Stock Exchanges"),   # SPGI, MCO, MSCI
    ("Financial Services", "Shell Companies"),
    ("Real Estate", "REIT - Retail"),                                 # equity REIT: IC matters
    ("Industrials", "Banking Equipment"),                             # not a bank (made up)
    ("Financial Services", ""),                                       # unknown → shown
    ("Financial Services", None),                                     # FMP null → shown
    ("Financial Services", 42),                                       # junk type → shown
])
async def test_everyone_else_keeps_the_three_rows(monkeypatch, sector, industry):
    resp = await _build(monkeypatch, hcd._answers(profile=_profile(sector, industry)))
    types = {m.type for m in resp.metrics}
    assert _LIQUIDITY <= types, f"{industry!r} lost {sorted(_LIQUIDITY - types)}"
    _assert_counts_consistent(resp)


@pytest.mark.asyncio
async def test_a_bank_with_an_n_m_roe_keeps_consistent_counts(monkeypatch):
    """Two exclusions at once — the omitted rows (absent) and an N/M ROE (shown, unscored)
    — must not double-count: total = D/E + P/E only."""
    resp = await _build(monkeypatch, hcd._answers(
        profile=_profile("Financial Services", "Banks - Regional"),
        ratios=[dict(_BANK_RATIOS)], key_metrics=[{"returnOnEquityTTM": -0.5}],
        bs=[dict(hcd._BS, totalStockholdersEquity=-1e9)],
    ))
    roe = next(m for m in resp.metrics if m.type == "roe")
    assert roe.highlighted_value == hc.NOT_MEANINGFUL
    _assert_counts_consistent(resp, unscored=frozenset({"roe"}))
    assert resp.total_count == len(resp.metrics) - 1


@pytest.mark.asyncio
async def test_a_bank_with_nothing_left_is_no_metrics_not_a_fake_verdict(monkeypatch):
    """Ratios leg answered nothing usable and ROE is absent: the bank has no row at all.
    That is `no_metrics` (served, never persisted) — not "excellent [0/0]" from the gate."""
    resp = await _build(monkeypatch, hcd._answers(
        profile=_profile("Financial Services", "Banks - Regional"),
        ratios=[{"currentRatioTTM": 0.3, "quickRatioTTM": 0.2,
                 "interestCoverageRatioTTM": 0.5}],
        key_metrics=[{}],
    ))
    assert resp.metrics == []
    assert resp.degraded == ["no_metrics"]
    assert (resp.passed_count, resp.total_count) == (0, 0)


def _universe_industries() -> Dict[str, str]:
    """{industry: sector} over both universe files (the FMP industry vocabulary)."""
    out: Dict[str, str] = {}
    for name in ("industry_universe.json", "benchmark_universe.json"):
        path = _DATA / name
        if not path.exists():
            continue
        rows = json.loads(path.read_text()).get("industries") or []
        for row in rows:
            if isinstance(row, dict) and row.get("industry"):
                out.setdefault(row["industry"], row.get("sector") or "")
    return out


# Every Financial Services / Real Estate industry that KEEPS the three rows, with why.
_KEEPS_THE_ROWS = {
    "Financial - Data & Stock Exchanges": "data vendors / exchanges: operating balance sheets",
    "Shell Companies": "a SPAC's trust and its liabilities are what the ratios measure",
    "REIT - Diversified": "equity REIT", "REIT - Healthcare Facilities": "equity REIT",
    "REIT - Hotel & Motel": "equity REIT", "REIT - Industrial": "equity REIT",
    "REIT - Office": "equity REIT", "REIT - Residential": "equity REIT",
    "REIT - Retail": "equity REIT", "REIT - Specialty": "equity REIT",
    "Real Estate - Development": "developer", "Real Estate - Diversified": "operator",
    "Real Estate - General": "operator", "Real Estate - Services": "service business",
}


def test_every_financial_and_real_estate_industry_is_decided():
    """A new FMP industry in either sector fails here until someone decides which side of
    the gate (`financials_metric_gate`, the ONE source the Health Check, the snapshot
    cards and the benchmark producer share) it belongs on — it never drifts silently."""
    universe = _universe_industries()
    assert len(universe) > 100, "the universe files did not load — the walk proves nothing"
    undecided = sorted(
        ind for ind, sector in universe.items()
        if sector in ("Financial Services", "Real Estate")
        and gate.industry_key(ind) not in gate._NO_LIQUIDITY_INDUSTRIES
        and ind not in _KEEPS_THE_ROWS
    )
    assert undecided == [], f"decide the liquidity rows for: {undecided}"


def test_every_listed_industry_is_a_real_fmp_name():
    """A typo ("insurance - property and casualty") would silently gate nobody."""
    known = {gate.industry_key(i) for i in _universe_industries()}
    assert known, "the universe files did not load"
    for listed in (gate._NO_LIQUIDITY_INDUSTRIES, gate._NO_COVERAGE_INDUSTRIES):
        unknown = sorted(listed - known)
        assert unknown == [], f"not an FMP industry in data/*_universe.json: {unknown}"
    # And the keep-list must not contradict the gate.
    assert not ({gate.industry_key(i) for i in _KEEPS_THE_ROWS} & gate._NO_LIQUIDITY_INDUSTRIES)
    # Coverage is gated for a SUBSET of the liquidity industries (brokers keep it).
    assert gate._NO_COVERAGE_INDUSTRIES < gate._NO_LIQUIDITY_INDUSTRIES


def test_the_health_check_reads_the_shared_gate():
    """No second copy: the Health Check's old names are the gate module's objects."""
    assert hc._LIQUIDITY_NA_INDUSTRIES is gate._NO_LIQUIDITY_INDUSTRIES
    assert hc._industry_key is gate.industry_key
    assert hc.liquidity_ratios_applicable is gate.liquidity_ratios_applicable
    assert hc.interest_coverage_applicable is gate.interest_coverage_applicable


@pytest.mark.parametrize("industry,applicable", [
    ("Banks - Regional", False), ("banks-regional", False), ("Banks—Diversified", False),
    ("Insurance - Specialty", False), ("Asset Management - Income", False),
    ("Financial - Data & Stock Exchanges", True), ("Software - Application", True),
    ("REIT - Office", True), ("", True), (None, True), (3.5, True), ("Banks Regional", True),
])
def test_liquidity_ratios_applicable(industry, applicable):
    assert hc.liquidity_ratios_applicable(industry) is applicable


# ── 2. peer_level ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["industry", "sector"])
async def test_every_compared_metric_reports_its_level(monkeypatch, level):
    resp = await _build(monkeypatch, cells=_cells(level))
    compared = [m for m in resp.metrics if m.comparison_value is not None]
    assert len(compared) == 6, "control: all six benchmark rows compared"
    assert all(m.peer_level == level for m in compared)
    z = next(m for m in resp.metrics if m.type == "altman_z_score")
    assert z.peer_level is None and z.comparison_value is None


@pytest.mark.asyncio
async def test_levels_are_per_metric(monkeypatch):
    """The lookup picks the level PER METRIC (a thin industry P/E falls to the sector)."""
    resp = await _build(monkeypatch, cells=_cells("industry", levels={"pe_ratio": "sector"}))
    by_type = {m.type: m for m in resp.metrics}
    assert by_type["pe_ratio"].peer_level == "sector"
    assert "sector average" in hcd._rendered(by_type["pe_ratio"])
    assert by_type["debt_to_equity"].peer_level == "industry"
    assert "industry average" in hcd._rendered(by_type["debt_to_equity"])


@pytest.mark.asyncio
@pytest.mark.parametrize("label,answers,cells,kind", [
    # Negative D/E: the forced-worst row compares nothing.
    ("negative_de", hcd._answers(ratios=[dict(hcd._RATIOS, debtToEquityRatioTTM=-13.0)],
                                 bs=[dict(hcd._BS, totalStockholdersEquity=-3e9)]),
     None, "debt_to_equity"),
    # ROE on negative equity: N/M, never compared.
    ("roe_n_m", hcd._answers(ratios=[dict(hcd._RATIOS, debtToEquityRatioTTM=-13.0)],
                             key_metrics=[{"returnOnEquityTTM": 3.0}],
                             bs=[dict(hcd._BS, totalStockholdersEquity=-3e9)]),
     None, "roe"),
    # A barely-positive median is no anchor (`_MIN_USABLE_MEDIAN`).
    ("median_below_floor", None, "roe_floor", "roe"),
    # No cell at all for the metric.
    ("no_cell", None, "no_cr", "current_ratio"),
])
async def test_peer_level_is_none_without_a_comparison(monkeypatch, label, answers, cells, kind):
    cell_map = _cells("industry")
    if cells == "roe_floor":
        cell_map["roe"] = dict(cell_map["roe"], value=0.01)
    elif cells == "no_cr":
        cell_map["current_ratio"] = None
    resp = await _build(monkeypatch, answers, cells=cell_map)
    m = next(m for m in resp.metrics if m.type == kind)
    assert m.comparison_value is None, label
    assert m.peer_level is None, f"{label}: a level with nothing compared"
    assert "industry" not in hcd._rendered(m), f"{label}: names a median it did not use"


@pytest.mark.asyncio
async def test_crossed_zero_keeps_the_median_and_its_level(monkeypatch):
    resp = await _build(monkeypatch, hcd._answers(key_metrics=[{"returnOnEquityTTM": -0.10}]),
                        cells=_cells("industry"))
    roe = next(m for m in resp.metrics if m.type == "roe")
    assert roe.comparison_value == 15.0 and roe.percent_difference is None
    assert roe.peer_level == "industry"
    assert hcd._rendered(roe) == (
        "Negative ROE vs a positive industry average. The company is losing money on its equity."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    float("nan"), float("inf"), float("-inf"), True, "22.5x", [1.0], {"v": 1},
])
async def test_a_junk_median_is_no_benchmark(monkeypatch, bad):
    """NaN slipped every `<=` floor check and reached pct_diff — a JSON encode failure on
    the whole card. Any non-finite / non-numeric / bool median is "no benchmark"."""
    cells = _cells("industry")
    cells["pe_ratio"] = dict(cells["pe_ratio"], value=bad)
    resp = await _build(monkeypatch, cells=cells)
    pe = next(m for m in resp.metrics if m.type == "pe_ratio")
    assert pe.comparison_value is None and pe.percent_difference is None
    assert pe.peer_level is None
    assert pe.status == hc._absolute_status("pe_ratio", 25.0)
    json.dumps(resp.model_dump(), allow_nan=False)   # the wire stays encodable


@pytest.mark.asyncio
async def test_a_numeric_string_median_still_compares(monkeypatch):
    """PostgREST can hand a NUMERIC back as a string; that is a real median."""
    cells = _cells("sector")
    cells["pe_ratio"] = dict(cells["pe_ratio"], value="28.0")
    resp = await _build(monkeypatch, cells=cells)
    pe = next(m for m in resp.metrics if m.type == "pe_ratio")
    assert pe.comparison_value == 28.0 and pe.peer_level == "sector"


@pytest.mark.asyncio
@pytest.mark.parametrize("cell", ["junk", 28.0, ["x"]])
async def test_a_non_dict_cell_is_no_benchmark(monkeypatch, cell):
    cells = _cells("industry")
    cells["pe_ratio"] = cell
    resp = await _build(monkeypatch, cells=cells)
    pe = next(m for m in resp.metrics if m.type == "pe_ratio")
    assert pe.comparison_value is None and pe.peer_level is None


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [None, "", "Industry", "peer", 7])
async def test_an_unknown_level_keeps_the_comparison_and_the_old_wording(monkeypatch, level):
    """Every cell the lookup builds carries its level; one that does not is a contract
    drift — logged, the comparison kept, the level reported unknown, the text "sector"."""
    cells = _cells("industry", levels={"pe_ratio": level})
    resp = await _build(monkeypatch, cells=cells)
    pe = next(m for m in resp.metrics if m.type == "pe_ratio")
    assert pe.comparison_value == 28.0
    assert pe.peer_level is None
    text = hcd._rendered(pe)
    assert "sector average" in text and "industry" not in text


@pytest.mark.asyncio
async def test_a_lookup_answering_a_non_dict_degrades_to_absolute(monkeypatch):
    class _Weird:
        def get_current_benchmarks(self, industry, sector, metrics):
            return [("pe_ratio", 28.0)]

    resp = await _build(monkeypatch, lookup=_Weird())
    assert all(m.comparison_value is None and m.peer_level is None for m in resp.metrics)
    assert {m.type for m in resp.metrics} >= {"pe_ratio", "roe", "current_ratio"}


# ── 3. the insight word follows the level ────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("level,word,other", [("industry", "industry", "sector"),
                                              ("sector", "sector", "industry")])
async def test_insight_text_names_the_level_used(monkeypatch, level, word, other):
    resp = await _build(monkeypatch, cells=_cells(level))
    for m in resp.metrics:
        text = hcd._rendered(m)
        assert other not in text.lower(), f"{m.type}: {text!r}"
        if m.comparison_value is not None:
            assert f"{word} average" in text, f"{m.type}: {text!r}"


@pytest.mark.asyncio
async def test_current_ratio_in_line_text_names_the_level(monkeypatch):
    """The one generator with the level in its LABEL as well as its text."""
    resp = await _build(monkeypatch, hcd._answers(
        ratios=[dict(hcd._RATIOS, currentRatioTTM=1.45)]), cells=_cells("industry"))
    cr = next(m for m in resp.metrics if m.type == "current_ratio")
    assert cr.highlighted_label == "near industry average."
    assert cr.insight_text == "Liquidity roughly in line with industry peers."


_SWEEP = [-95.0, -60.0, -45.0, -35.0, -28.0, -20.0, -12.0, -8.0, -3.0, 0.0, 3.0, 8.0,
          12.0, 18.0, 25.0, 40.0, 60.0, 90.0, 150.0, 250.0, 900.0]


@pytest.mark.parametrize("kind", sorted(hc._INSIGHT_GENERATORS))
def test_every_generator_branch_follows_the_peer_word(kind):
    """Every branch of every generator, both levels: "industry" never leaves a "sector"
    behind, and the default stays byte-for-byte what shipped builds have always shown."""
    gen = hc._INSIGHT_GENERATORS[kind]
    for pct in _SWEEP:
        default = gen(pct, 1.0, 1.0)
        assert gen(pct, 1.0, 1.0, peer="sector") == default
        ind = gen(pct, 1.0, 1.0, peer="industry")
        rendered = f"{ind[1]} {ind[2]} {ind[0]}"
        assert "sector" not in rendered.lower(), f"{kind} @ {pct}: {rendered!r}"
        assert "industry" in rendered, f"{kind} @ {pct}: names no level: {rendered!r}"
        assert "industry" not in " ".join(str(x) for x in default)
        # Only the level word changes.
        assert tuple(str(x).replace("sector", "industry") for x in default) == ind


@pytest.mark.parametrize("kind", ["roe", "interest_coverage"])
@pytest.mark.parametrize("value", [-5.0, 0.0])
def test_crossed_zero_text_follows_the_peer_word(kind, value):
    default = hc._crossed_zero_insight(kind, value)
    ind = hc._crossed_zero_insight(kind, value, peer="industry")
    assert "sector average" in default[2] and "industry average" in ind[2]
    assert tuple(x.replace("sector", "industry") for x in default) == ind


@pytest.mark.parametrize("level,noun", [("industry", "industry"), ("sector", "sector"),
                                        (None, "sector"), ("weird", "sector")])
def test_peer_noun(level, noun):
    assert hc._peer_noun(level) == noun


# ── 4. wire shape and payload version ────────────────────────────────────────────────


def test_peer_level_is_optional_on_the_wire():
    """Additive: a row cached before the field decodes (None); the key is always emitted."""
    old = {"type": "pe_ratio", "value": 25.0, "comparison_value": 28.0,
           "percent_difference": -10.7, "gauge_position": 0.45, "status": "neutral",
           "insight_text": "sector average. Valued in line with peers.",
           "highlighted_value": "11%", "highlighted_label": "below"}
    m = HealthCheckMetricSchema.model_validate(old)
    assert m.peer_level is None
    assert "peer_level" in m.model_dump()
    new = HealthCheckMetricSchema.model_validate(dict(old, peer_level="industry"))
    assert new.model_dump()["peer_level"] == "industry"


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        class _R:
            pass

        r = _R()
        r.data = self.rows
        return r


def _cached_row(version: int, metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    body = HealthCheckResponse(symbol="C", overall_rating="poor", passed_count=0,
                               total_count=len(metrics), metrics=metrics).model_dump()
    return {"response_json": {**body, "payload_version": version},
            "cached_at": datetime.now(timezone.utc).isoformat(), "next_earnings_date": None}


def test_payload_version_is_6_and_older_rows_are_rebuilt():
    """A v4 row may hold a bank's three red liquidity rows and "sector" text over an
    industry median; a v5 row (2026-10-07 first pass, possibly written by a local run) a
    broker without interest coverage, or an exchange compared with the bank-pooled
    Financial Services sector median. Both are rebuilt on their next read; a v6 row is
    served."""
    assert hc._HC_PAYLOAD_VERSION == 6
    bank_row = {"type": "current_ratio", "value": 0.3, "comparison_value": 1.4,
                "percent_difference": -78.6, "gauge_position": 0.11, "status": "negative",
                "insight_text": "sector average. Constrained liquidity position.",
                "highlighted_value": "79%", "highlighted_label": "well below"}
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    for old in (4, 5):
        svc.supabase = _Rows([_cached_row(old, [bank_row])])
        assert svc._check_supabase_cache("C") is None, f"a v{old} row was served"
    svc.supabase = _Rows([_cached_row(6, [dict(bank_row, type="pe_ratio",
                                               peer_level="industry")])])
    served = svc._check_supabase_cache("C")
    assert served is not None and served.metrics[0].peer_level == "industry"


@pytest.mark.asyncio
async def test_the_built_response_round_trips_through_the_cache_shape(monkeypatch):
    """What the writer stores is what the reader admits (peer_level included)."""
    resp = await _build(monkeypatch, cells=_cells("industry"))
    stored = {**resp.model_dump(), "payload_version": hc._HC_PAYLOAD_VERSION}
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = _Rows([{"response_json": stored,
                           "cached_at": datetime.now(timezone.utc).isoformat(),
                           "next_earnings_date": None}])
    served = svc._check_supabase_cache("TEST")
    assert served == resp
