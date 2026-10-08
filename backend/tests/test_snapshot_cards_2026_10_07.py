"""Overview snapshot cards, 2026-10-07: the Price card's numbers and the new wire fields.

Verified on prod 2026-10-07 (read-only): KO's Price card showed "P/FCF (4.25x sector avg 16)
69.93" while FMP /stable ratios-ttm `priceToFreeCashFlowRatioTTM` was 25.83 (MSFT 58.72 vs
57.37 shown) — the card read the PLURAL key, which /stable never sends, and fell to market
cap ÷ the last fiscal year's FCF. "P/S (7.50x sector avg 1)" printed a 0.98 median with no
decimals. With no peer median, P/B / P/S / P/FCF / EV/EBITDA were scored on P/E bands.

Also pinned here: `_safe_float` drops NaN/inf, `peer_level` on each metric (the level of the
median its name prints, None when it prints none), `computed_at` on each card (kept on a
cache hit; a pre-field row takes its own `cached_at`), and the payload-version bumps that
make every pre-fix row rebuild. Hermetic: stubbed FMP, stubbed lookup, fake Supabase rows.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.schemas.stock_overview import (
    SnapshotItemResponse,
    SnapshotMetricResponse,
    snapshot_build_time,
    with_cached_build_time,
)
from app.services import valuation_snapshot_service as vss
from app.services.sector_benchmark_lookup import BenchmarkLookupFailed

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ── harness ──────────────────────────────────────────────────────────────────────


class _FakeFMP:
    """Per-method answers; an Exception answer is RAISED (a failed leg); default []."""

    def __init__(self, **answers: Any) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            answer = self._answers.get(name, [])
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return _call


class _RichLookup:
    """`get_current_benchmarks` stand-in: {metric: (median, level)} → rich cells."""

    def __init__(self, table: Optional[Dict[str, tuple]] = None, failed: bool = False):
        self.table = table or {}
        self.failed = failed
        self.calls: List[tuple] = []

    def get_current_benchmarks(self, industry, sector, metrics):
        self.calls.append((industry, sector, tuple(metrics)))
        out = {
            m: ({"value": self.table[m][0], "level": self.table[m][1],
                 "peer_group_name": industry if self.table[m][1] == "industry" else sector,
                 "n": 40} if m in self.table else None)
            for m in metrics
        }
        return BenchmarkLookupFailed(out) if self.failed else out


def _price(**kw) -> SnapshotItemResponse:
    args = dict(fr={}, km={}, cf={}, inc={}, bs={}, profile={}, bench={})
    args.update(kw)
    return vss.build_price_snapshot(**args)


def _m(snap: SnapshotItemResponse, key: str) -> SnapshotMetricResponse:
    return next(m for m in snap.metrics if m.metric_key == key)


_KO_PROFILE = {"symbol": "KO", "sector": "Consumer Defensive", "industry": "Beverages - Non-Alcoholic",
               "mktCap": 300_000_000_000}
_MSFT_PROFILE = {"symbol": "MSFT", "sector": "Technology", "industry": "Software - Infrastructure",
                 "mktCap": 3_000_000_000_000}


# ── 1. P/FCF reads the key /stable actually sends ─────────────────────────────────


@pytest.mark.parametrize("profile,ttm,fy_fcf,fy_shown", [
    # KO: market cap ÷ FY FCF = 300e9 / 4.29e9 = 69.93 — what prod printed.
    (_KO_PROFILE, 25.83, 4.29e9, "69.93"),
    # MSFT: 3.0e12 / 52.29e9 = 57.37 — what prod printed.
    (_MSFT_PROFILE, 58.72, 52.29e9, "57.37"),
])
def test_pfcf_reads_the_singular_stable_ttm_key(profile, ttm, fy_fcf, fy_shown):
    snap = _price(
        fr={"priceToFreeCashFlowRatioTTM": ttm, "priceToEarningsRatioTTM": 25.0},
        km={"freeCashFlowYieldTTM": round(1 / ttm, 4)},
        cf={"freeCashFlow": fy_fcf},
        profile=profile,
    )
    pfcf = _m(snap, "pfcf")
    assert pfcf.value == f"{ttm:.2f}"
    assert pfcf.value != fy_shown, "the fiscal-year reconstruction won over the TTM key"


def test_control_the_fiscal_year_reconstruction_still_runs_with_no_ttm_signal_at_all():
    """Anti-vacuity for the test above: with no ratio key and no TTM yield, the FY
    reconstruction is what produces 69.93 — so the assertion above could have caught it."""
    snap = _price(cf={"freeCashFlow": 4.29e9}, profile=_KO_PROFILE)
    assert _m(snap, "pfcf").value == "69.93"


def test_the_plural_legacy_key_is_still_a_fallback():
    snap = _price(fr={"priceToFreeCashFlowsRatioTTM": 31.4}, profile=_KO_PROFILE)
    assert _m(snap, "pfcf").value == "31.40"


@pytest.mark.parametrize("km,expected,scored", [
    ({"freeCashFlowYieldTTM": 0.04}, "25.00", True),     # 0 = absent: the TTM yield answers
    ({"freeCashFlowYieldTTM": -0.02}, "Neg.", False),
    ({}, "—", False),
])
def test_a_zero_ratio_is_absent_not_a_neutral_score(km, expected, scored):
    snap = _price(fr={"priceToFreeCashFlowRatioTTM": 0}, km=km,
                  bench={"pfcf_ratio": 20.0}, profile=_KO_PROFILE)
    pfcf = _m(snap, "pfcf")
    assert pfcf.value == expected
    assert (pfcf.score is not None) == scored


def test_singular_wins_over_plural_when_both_are_present():
    snap = _price(fr={"priceToFreeCashFlowRatioTTM": 25.83,
                      "priceToFreeCashFlowsRatioTTM": 69.93}, profile=_KO_PROFILE)
    assert _m(snap, "pfcf").value == "25.83"


def test_negative_fy_fcf_but_positive_ttm_fcf_is_not_neg():
    """No ratio key, a POSITIVE TTM yield, a NEGATIVE last fiscal year: the trailing year
    decides — P/FCF = 1 / yield, never "Neg." from the older year."""
    snap = _price(km={"freeCashFlowYieldTTM": 0.04}, cf={"freeCashFlow": -2.0e9},
                  profile=_KO_PROFILE)
    assert _m(snap, "pfcf").value == "25.00"
    assert vss._fmt_pfcf(None, {"freeCashFlowYieldTTM": 0.04}, {"freeCashFlow": -2.0e9}) == "—"


def test_negative_ttm_fcf_beats_a_positive_fiscal_year():
    """The reverse: burning cash over the trailing year is "Neg." even though the last
    fiscal year's FCF would reconstruct a positive multiple."""
    snap = _price(km={"freeCashFlowYieldTTM": -0.02}, cf={"freeCashFlow": 4.29e9},
                  profile=_KO_PROFILE)
    pfcf = _m(snap, "pfcf")
    assert pfcf.value == "Neg."
    assert pfcf.score is None


@pytest.mark.parametrize("km,cf,expected", [
    ({"freeCashFlowYieldTTM": -0.03}, None, "Neg."),
    ({"freeCashFlowYieldTTM": 0.03}, {"freeCashFlow": -1.0}, "—"),   # TTM sign decides
    ({"freeCashFlowYield": -0.03}, None, "Neg."),                     # legacy bare name
    ({}, {"freeCashFlow": -1.0}, "Neg."),                             # no TTM: FY decides
    ({"freeCashFlowYieldTTM": "NaN"}, {"freeCashFlow": -1.0}, "Neg."),  # NaN = unknown
    ({}, None, "—"),
])
def test_fmt_pfcf_recovers_the_sign_ttm_first(km, cf, expected):
    assert vss._fmt_pfcf(None, km, cf) == expected


# ── 2. The median prints with decimals, the words stay "sector avg" ───────────────


@pytest.mark.parametrize("median,printed", [
    (0.98, "0.98"), (0.4, "0.40"), (4.25, "4.25"), (9.99, "9.99"),
    (9.996, "10.0"), (16.0, "16.0"), (22.43, "22.4"), (151.27, "151.3"),
])
def test_the_median_prints_with_decimals(median, printed):
    assert vss._fmt_median(median) == printed
    assert vss._sector_ctx(None, median) == f"sector avg {printed}"


def test_ps_median_098_reads_098_not_1():
    snap = _price(fr={"priceToSalesRatioTTM": 7.35}, bench={"ps_ratio": 0.98},
                  bench_levels={"ps_ratio": "sector"})
    ps = _m(snap, "ps")
    assert ps.name == "P/S (7.50x sector avg 0.98)"
    assert "sector avg 1)" not in ps.name


def test_contract_example_pe_reads_one_decimal_from_ten_up():
    snap = _price(fr={"priceToEarningsRatioTTM": 29.12}, bench={"pe_ratio": 22.4})
    assert _m(snap, "pe").name == "P/E (1.30x sector avg 22.4)"


@pytest.mark.parametrize("median", [0.0, -3.0, 0.004, float("nan"), float("inf")])
def test_an_unusable_median_is_no_comparison(median):
    """≤ 0, prints as 0.00, or non-finite: no "sector avg", no peer score, no peer level."""
    snap = _price(fr={"priceToSalesRatioTTM": 7.5}, bench={"ps_ratio": median},
                  bench_levels={"ps_ratio": "industry"})
    ps = _m(snap, "ps")
    assert ps.name == "P/S"
    assert ps.score is None and ps.peer_level is None
    assert ps.value == "7.50"


def test_names_never_say_industry_avg_and_keep_the_ios_strip_word():
    """Shipped iOS strips `\\s*\\([^)]*sector[^)]*\\)` — the suffix must keep "sector"."""
    snap = _price(
        fr={"priceToEarningsRatioTTM": 30.0, "priceToSalesRatioTTM": 5.0,
            "priceToBookRatioTTM": 9.0, "priceToFreeCashFlowRatioTTM": 40.0,
            "enterpriseValueMultipleTTM": 20.0, "earningsYieldTTM": 0.033},
        bench={"pe_ratio": 25.0, "ps_ratio": 4.0, "pb_ratio": 6.0, "pfcf_ratio": 30.0,
               "ev_ebitda": 16.0, "earnings_yield": 0.04},
        bench_levels={k: "industry" for k in ("pe_ratio", "ps_ratio", "pb_ratio",
                                              "pfcf_ratio", "ev_ebitda", "earnings_yield")},
    )
    strip = re.compile(r"\s*\([^)]*sector[^)]*\)")
    for m in snap.metrics:
        assert "industry avg" not in m.name.lower()
        assert m.peer_level == "industry"
        assert strip.sub("", m.name) == m.name.split(" (")[0], m.name


# ── 3. NaN / inf are absent, not numbers ──────────────────────────────────────────


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf"), "NaN",
                                 "Infinity", "-Infinity", "abc", None])
def test_safe_float_rejects_non_finite(raw):
    assert vss._safe_float({"k": raw}, "k") is None


def test_a_nan_multiple_renders_unknown_and_is_not_scored():
    snap = _price(fr={"priceToEarningsRatioTTM": float("nan"),
                      "priceToSalesRatioTTM": "Infinity"},
                  bench={"pe_ratio": 20.0, "ps_ratio": 3.0})
    pe, ps = _m(snap, "pe"), _m(snap, "ps")
    assert pe.value == "—" and pe.score is None
    assert ps.value == "—" and ps.score is None
    assert "nan" not in pe.name.lower() and "inf" not in ps.name.lower()
    assert 1.0 <= snap.weighted_score <= 5.0


# ── 4. No peer median: the four non-P/E multiples are unscored, not P/E-banded ─────


def test_without_peers_pb_ps_pfcf_ev_are_unscored_and_vote_neutral():
    snap = _price(fr={"priceToEarningsRatioTTM": 8.0, "priceToBookRatioTTM": 1.2,
                      "priceToSalesRatioTTM": 7.5, "priceToFreeCashFlowRatioTTM": 45.0,
                      "enterpriseValueMultipleTTM": 45.0}, bench={})
    for key in ("pb", "ps", "pfcf", "ev_ebitda"):
        m = _m(snap, key)
        assert m.score is None, f"{key} was scored with no peer median: {m.score}"
        assert m.value not in ("—", "Neg.")          # the value is still shown
    # P/E keeps its own absolute bands (8 < 10 → 5); the other four vote the neutral 3.
    assert _m(snap, "pe").score == 5
    assert snap.weighted_score == pytest.approx(5 * 0.25 + 3 * 0.75)
    assert snap.rating == 4


def test_old_p_e_band_scoring_of_ps_is_gone():
    """The bug in one line: P/S 7.5 used to score 5 ("< 10") on the P/E bands."""
    assert vss._valuation_score(7.5, None) == 5          # the P/E bands themselves are kept
    assert vss._peer_relative_score(7.5, None) is None    # ...but never applied to P/S
    assert vss._peer_relative_score(7.5, 0.98) == 1       # 7.6x the peer median


def test_with_peers_every_multiple_is_scored_as_before():
    snap = _price(fr={"priceToEarningsRatioTTM": 15.0, "priceToBookRatioTTM": 2.0,
                      "priceToSalesRatioTTM": 2.0, "priceToFreeCashFlowRatioTTM": 15.0,
                      "enterpriseValueMultipleTTM": 10.0},
                  bench={"pe_ratio": 30.0, "pb_ratio": 4.0, "ps_ratio": 4.0,
                         "pfcf_ratio": 30.0, "ev_ebitda": 20.0})
    assert [_m(snap, k).score for k in ("pe", "pb", "ps", "pfcf", "ev_ebitda")] == [5] * 5
    assert snap.weighted_score == 5.0


# ── 5. peer_level ────────────────────────────────────────────────────────────────


def test_peer_level_is_set_exactly_when_the_name_prints_a_median():
    snap = _price(
        fr={"priceToEarningsRatioTTM": 30.0, "priceToSalesRatioTTM": 5.0},
        bench={"pe_ratio": 25.0, "ps_ratio": 4.0, "pfcf_ratio": 30.0},
        bench_levels={"pe_ratio": "industry", "ps_ratio": "sector", "pfcf_ratio": "industry",
                      "ev_ebitda": "industry"},      # a level with no median: never reported
    )
    assert _m(snap, "pe").peer_level == "industry"
    assert _m(snap, "ps").peer_level == "sector"
    # P/FCF value missing but the median exists: "sector avg 30.0" printed → level reported.
    pfcf = _m(snap, "pfcf")
    assert pfcf.name == "P/FCF (sector avg 30.0)" and pfcf.peer_level == "industry"
    ev = _m(snap, "ev_ebitda")
    assert ev.name == "EV/EBITDA" and ev.peer_level is None
    assert _m(snap, "pb").peer_level is None and _m(snap, "earnings_yield").peer_level is None
    for m in snap.metrics:
        # a level is only ever reported beside a printed median
        assert m.peer_level is None or "sector avg" in m.name, m


def test_no_levels_passed_means_no_peer_level_and_an_unknown_level_is_dropped():
    snap = _price(fr={"priceToEarningsRatioTTM": 30.0}, bench={"pe_ratio": 25.0})
    assert _m(snap, "pe").peer_level is None              # the overview fallback's call shape
    snap = _price(fr={"priceToEarningsRatioTTM": 30.0}, bench={"pe_ratio": 25.0},
                  bench_levels={"pe_ratio": "planet"})
    assert _m(snap, "pe").peer_level is None


@pytest.mark.parametrize("cells,values,levels", [
    ({"pe_ratio": {"value": 22.0, "level": "industry"}}, {"pe_ratio": 22.0},
     {"pe_ratio": "industry"}),
    ({"pe_ratio": None}, {"pe_ratio": None}, {"pe_ratio": None}),
    ({"pe_ratio": {"value": float("nan"), "level": "sector"}}, {"pe_ratio": None},
     {"pe_ratio": None}),
    ({"pe_ratio": {"value": "abc", "level": "sector"}}, {"pe_ratio": None},
     {"pe_ratio": None}),
    ({"pe_ratio": {"value": True, "level": "sector"}}, {"pe_ratio": None},
     {"pe_ratio": None}),
    ({"pe_ratio": {"value": "18.5", "level": "galaxy"}}, {"pe_ratio": 18.5},
     {"pe_ratio": None}),
    ({"pe_ratio": {"level": "sector"}}, {"pe_ratio": None}, {"pe_ratio": None}),
])
def test_split_peer_cells(cells, values, levels):
    got_values, got_levels = vss.split_peer_cells(cells)
    assert got_values == values and got_levels == levels


@pytest.mark.parametrize("junk", [None, [], "x", 3])
def test_split_peer_cells_survives_a_non_dict(junk):
    assert vss.split_peer_cells(junk) == ({}, {})


@pytest.mark.asyncio
async def test_compute_reads_the_rich_cells_and_reports_each_level(monkeypatch):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    lookup = _RichLookup({"pe_ratio": (22.4, "industry"), "ps_ratio": (0.98, "sector"),
                          "pfcf_ratio": (16.0, "sector")})
    monkeypatch.setattr(vss, "get_sector_benchmark_lookup", lambda: lookup)
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.supabase = None
    svc.fmp = _FakeFMP(
        get_company_profile=dict(_KO_PROFILE),
        get_ratios_ttm=[{"priceToEarningsRatioTTM": 29.12, "priceToSalesRatioTTM": 7.35,
                         "priceToFreeCashFlowRatioTTM": 25.83}],
        get_key_metrics_ttm=[{"freeCashFlowYieldTTM": 0.0387}],
        get_cash_flow_statement=[{"freeCashFlow": 4.29e9}],
        get_dcf={},
    )
    snap, degraded = await svc._compute_with_status("KO")
    assert degraded == []
    assert lookup.calls and lookup.calls[0][:2] == ("Beverages - Non-Alcoholic", "Consumer Defensive")
    pe, ps, pfcf = _m(snap, "pe"), _m(snap, "ps"), _m(snap, "pfcf")
    assert pe.name == "P/E (1.30x sector avg 22.4)" and pe.peer_level == "industry"
    assert ps.name == "P/S (7.50x sector avg 0.98)" and ps.peer_level == "sector"
    assert pfcf.value == "25.83" and pfcf.name == "P/FCF (1.61x sector avg 16.0)"
    assert _m(snap, "pb").peer_level is None
    assert snap.computed_at and _ISO_Z.match(snap.computed_at)


@pytest.mark.asyncio
async def test_a_failed_rich_lookup_still_marks_the_build_degraded(monkeypatch):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    monkeypatch.setattr(vss, "get_sector_benchmark_lookup",
                        lambda: _RichLookup({"pe_ratio": (22.0, "sector")}, failed=True))
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.supabase = None
    svc.fmp = _FakeFMP(get_company_profile=dict(_KO_PROFILE), get_dcf={},
                       get_ratios_ttm=[{"priceToEarningsRatioTTM": 29.0}])
    _snap, degraded = await svc._compute_with_status("KO")
    assert degraded == ["benchmarks"]


# ── 6. computed_at, cache hits and the version bump ───────────────────────────────


def test_snapshot_build_time_format():
    assert snapshot_build_time(datetime(2026, 10, 7, 13, 4, 5, 999, tzinfo=timezone.utc)) \
        == "2026-10-07T13:04:05Z"
    # naive = UTC; another offset is converted
    assert snapshot_build_time(datetime(2026, 10, 7, 13, 4, 5)) == "2026-10-07T13:04:05Z"
    est = timezone(timedelta(hours=-4))
    assert snapshot_build_time(datetime(2026, 10, 7, 9, 4, 5, tzinfo=est)) == "2026-10-07T13:04:05Z"
    assert _ISO_Z.match(snapshot_build_time())


def test_with_cached_build_time_fills_only_a_row_without_the_key():
    cached_at = datetime(2026, 10, 7, 1, 2, 3, tzinfo=timezone.utc)
    assert with_cached_build_time({}, cached_at) == {"computed_at": "2026-10-07T01:02:03Z"}
    kept = {"computed_at": "2026-10-06T22:00:00Z"}
    assert with_cached_build_time(kept, cached_at)["computed_at"] == "2026-10-06T22:00:00Z"


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
        assert name == "snapshot_cache"
        return _Table(self.rows, self.upserts)


def _row(json_data, age=timedelta(hours=3)):
    cached_at = datetime.now(timezone.utc) - age
    return {"response_json": json_data, "cached_at": cached_at.isoformat()}, cached_at


def _valuation_with_rows(rows):
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.supabase = _Supabase(rows)
    return svc


def test_valuation_cache_hit_keeps_the_original_build_time(monkeypatch):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    built = _price(fr={"priceToEarningsRatioTTM": 20.0}).model_copy(
        update={"computed_at": "2026-10-07T10:00:00Z"})
    writer = _valuation_with_rows([])
    writer._upsert_supabase_cache("KO", built)
    (payload,) = writer.supabase.upserts
    # 7 (2026-10-09, NET-4): a listed non-lender member's Price card is peer-free.
    assert payload["response_json"][vss._VERSION_KEY] == vss._SNAPSHOT_PAYLOAD_VERSION == 7
    assert payload["response_json"]["computed_at"] == "2026-10-07T10:00:00Z"
    assert payload["response_json"]["metrics"][0]["peer_level"] is None   # the key travels

    row, _ = _row(payload["response_json"])
    got = _valuation_with_rows([row])._check_supabase_cache("KO")
    assert got is not None and got.computed_at == "2026-10-07T10:00:00Z", (
        "a cached card must show when it was BUILT, not when it was read"
    )


def test_a_row_without_computed_at_takes_its_cached_at(monkeypatch):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    body = _price(fr={"priceToEarningsRatioTTM": 20.0}).model_dump()
    body.pop("computed_at")
    row, cached_at = _row({**body, vss._VERSION_KEY: vss._SNAPSHOT_PAYLOAD_VERSION})
    got = _valuation_with_rows([row])._check_supabase_cache("KO")
    assert got.computed_at == snapshot_build_time(cached_at)
    assert "computed_at" not in row["response_json"], "the SDK's row must not be mutated"


@pytest.mark.parametrize("version", [None, 4, 5])
def test_a_pre_fix_valuation_row_is_rebuilt(monkeypatch, version):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    body = _price(fr={"priceToEarningsRatioTTM": 20.0}).model_dump()
    if version is not None:
        body[vss._VERSION_KEY] = version
    row, _ = _row(body)
    assert _valuation_with_rows([row])._check_supabase_cache("KO") is None
    # negative control: the current version IS served
    row, _ = _row({**body, vss._VERSION_KEY: vss._SNAPSHOT_PAYLOAD_VERSION})
    assert _valuation_with_rows([row])._check_supabase_cache("KO") is not None


@pytest.mark.asyncio
async def test_public_getter_serves_the_cached_build_time_on_tier2_and_tier1(monkeypatch):
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    monkeypatch.setattr(vss.settings, "DCF_SHADOW", False)
    vss._cache.clear()
    vss._inflight.clear()
    body = _price(fr={"priceToEarningsRatioTTM": 20.0}).model_dump()
    body["computed_at"] = "2026-10-06T21:30:00Z"
    row, _ = _row({**body, vss._VERSION_KEY: vss._SNAPSHOT_PAYLOAD_VERSION})
    svc = _valuation_with_rows([row])
    svc.fmp = None                                   # any rebuild would explode
    first = await svc.get_valuation_snapshot("KO")   # Tier 2
    second = await svc.get_valuation_snapshot("KO")  # Tier 1
    assert first.computed_at == second.computed_at == "2026-10-06T21:30:00Z"
    vss._cache.clear()


# ── 7. Every overview card stamps computed_at; growth / ownership rows need no bump ───


def test_growth_and_ownership_rows_without_the_key_take_their_cached_at():
    from app.services import growth_snapshot_service as gs
    from app.services import ownership_snapshot_service as os_

    body = SnapshotItemResponse(category="Growth", rating=4, metrics=[]).model_dump()
    body.pop("computed_at")
    for mod, cls, category in ((gs, gs.GrowthSnapshotService, "Growth"),
                               (os_, os_.OwnershipSnapshotService, "Insiders & Ownership")):
        row, cached_at = _row({**body, "category": category,
                               mod._VERSION_KEY: mod._SNAPSHOT_PAYLOAD_VERSION})
        svc = cls.__new__(cls)
        svc.supabase = _Supabase([row])
        got = svc._check_supabase_cache("KO")
        assert got is not None and got.computed_at == snapshot_build_time(cached_at), mod
        # ...and a row that carries one keeps it
        row, _ = _row({**body, "category": category, "computed_at": "2026-10-01T00:00:00Z",
                       mod._VERSION_KEY: mod._SNAPSHOT_PAYLOAD_VERSION})
        svc.supabase = _Supabase([row])
        assert svc._check_supabase_cache("KO").computed_at == "2026-10-01T00:00:00Z"


@pytest.mark.asyncio
async def test_growth_and_ownership_builds_stamp_computed_at(monkeypatch):
    from app.schemas.growth import GrowthDataPointSchema, GrowthResponse
    from app.services import growth_service as gmod
    from app.services import growth_snapshot_service as gs
    from app.services import ownership_snapshot_service as os_

    point = [GrowthDataPointSchema(period="2025", value=10.0, yoy_change_percent=12.0,
                                   sector_average_yoy=8.0)]
    growth = GrowthResponse(symbol="KO", eps_annual=point, eps_quarterly=[],
                            revenue_annual=point, revenue_quarterly=[],
                            operating_profit_annual=point, free_cash_flow_annual=[])

    class _Growth:
        async def get_growth_with_status(self, ticker):
            return growth, []

    monkeypatch.setattr(gmod, "get_growth_service", lambda: _Growth())
    snap, _ = await gs.GrowthSnapshotService.__new__(gs.GrowthSnapshotService)._compute_with_status("KO")
    assert snap.computed_at and _ISO_Z.match(snap.computed_at)

    holders = SimpleNamespace(
        shareholder_breakdown=SimpleNamespace(institutions_percent=60.0, insiders_percent=1.0,
                                              public_other_percent=39.0),
        insider_data=SimpleNamespace(summary=SimpleNamespace(total_net_flow=0.0,
                                                             is_positive=True)),
        hedge_funds_data=SimpleNamespace(summary=SimpleNamespace(total_net_flow=1.0,
                                                                 is_positive=True)),
    )
    own = os_.OwnershipSnapshotService.__new__(os_.OwnershipSnapshotService)._compute_from_holders(holders)
    assert own.computed_at and _ISO_Z.match(own.computed_at)


# ── 8. Wire contract: both fields are Optional on iOS (source scan) ───────────────

_DTO_FILE = (Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios" / "Models"
             / "StockOverviewResponseModels.swift")


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def _struct_body(src: str, name: str) -> Optional[str]:
    m = re.search(r"\bstruct\s+" + re.escape(name) + r"\b[^{]*\{", src)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(src) and depth:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
    return src[m.end():i - 1]


def _declares_optional(src: str, struct: str, prop: str, wire: str) -> bool:
    body = _struct_body(_strip_swift_comments(src), struct)
    if body is None:
        return False
    return bool(
        re.search(r"\blet\s+" + prop + r"\s*:\s*String\?", body)
        and re.search(r"\bcase\s+[^\n]*\b" + prop + r"\s*=\s*\"" + wire + r"\"", body)
    )


def test_ios_dtos_decode_both_fields_as_optional():
    if not _DTO_FILE.exists():
        pytest.skip("iOS tree not present")
    src = _DTO_FILE.read_text()
    assert _declares_optional(src, "SnapshotMetricDTO", "peerLevel", "peer_level")
    assert _declares_optional(src, "SnapshotItemDTO", "computedAt", "computed_at")


def test_the_dto_scan_is_not_vacuous():
    """Mutations of a minimal DTO the scan must reject: non-optional, commented out, and
    declared in a DIFFERENT struct."""
    good = ('struct SnapshotMetricDTO: Decodable {\n let peerLevel: String?\n'
            ' enum CodingKeys: String, CodingKey {\n case name\n'
            ' case peerLevel = "peer_level"\n }\n}\n')
    assert _declares_optional(good, "SnapshotMetricDTO", "peerLevel", "peer_level")
    assert not _declares_optional(good.replace("String?", "String"),
                                  "SnapshotMetricDTO", "peerLevel", "peer_level")
    assert not _declares_optional(good.replace(" let peerLevel", " // let peerLevel"),
                                  "SnapshotMetricDTO", "peerLevel", "peer_level")
    other = good.replace("SnapshotMetricDTO", "SomethingElseDTO") + \
        "struct SnapshotMetricDTO: Decodable {\n let name: String\n}\n"
    assert not _declares_optional(other, "SnapshotMetricDTO", "peerLevel", "peer_level")


def test_both_fields_serialize_snake_case_and_default_none():
    item = SnapshotItemResponse(category="Price", rating=3, metrics=[
        SnapshotMetricResponse(name="P/E", value="20.00")])
    wire = item.model_dump(mode="json")
    assert wire["computed_at"] is None and wire["metrics"][0]["peer_level"] is None
