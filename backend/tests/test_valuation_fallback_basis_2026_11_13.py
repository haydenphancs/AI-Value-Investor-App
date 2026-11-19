"""Price card: a P/FCF or EV/EBITDA shown on a LAST-FISCAL-YEAR basis is never a TTM line.

Owner decision 2026-11-13 ("Fallback TTM labels"): when P/FCF or EV/EBITDA had to use the
last fiscal year — no TTM field, so `build_price_snapshot` divided market cap by the annual
statement's free cash flow, or a current EV by the annual statement's EBITDA — the report's
Valuation drill-down must not call that figure "Current (TTM)". The line is built only from
the row's `numeric_value` / `peer_median` (`ticker_report_data_collector._current_comparison`),
so a fiscal-year row carries NEITHER, while its displayed value, label, peer level, score and
the card's rating stay exactly what they were.

Pinned here, rung by rung:

A. P/FCF: every /ratios-ttm and /key-metrics-ttm key, and 1 / the TTM FCF yield, are TTM
   (numbers kept); market cap ÷ the fiscal-year FCF — and the "Neg." printed from a
   negative fiscal-year FCF — are fiscal-year (no numbers);
B. EV/EBITDA: every ratio key is TTM; every reconstruction (each EBITDA rung × each EV rung,
   a negative EBITDA included) is fiscal-year;
C. NaN / zero / negative / missing inputs land on the right basis, and a row that printed
   no fiscal-year figure ("—") keeps its median;
D. nothing else moves: each fiscal-year build equals its TTM twin (the same figure fed
   through the TTM key, i.e. the row as `_metric` builds it with `ttm` defaulted) on every
   field but the two numbers, the card's rating and `weighted_score` included;
E. one tagged INFO line per build names the fiscal-year rows (never an "N/M" one);
F. end to end: `_snapshot_to_card` draws no "Current" keys for those rows, and the service
   path really does read `cf` / `inc` as ANNUAL statements — the premise of the rule.

Hermetic: pure builder calls, a fake FMP client and a fake benchmark lookup.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.services.agents.ticker_report_data_collector as C
import app.services.valuation_snapshot_service as vss
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.schemas.ticker_report import DeepDiveMetricCardResponse

_LOGGER = vss.logger.name
_TAG = "[valuation-fiscal-year-basis]"

_BENCH = {"pe_ratio": 22.4, "pb_ratio": 4.25, "ps_ratio": 2.1, "pfcf_ratio": 16.0,
          "ev_ebitda": 14.2}
_LEVELS = {k: "industry" for k in _BENCH}

_KO_PROFILE = {"symbol": "KO", "sector": "Consumer Defensive",
               "industry": "Beverages - Non-Alcoholic", "mktCap": 300_000_000_000}
_MCAP = 300_000_000_000.0
_FY_FCF = 4.29e9                    # KO: 300e9 / 4.29e9 = 69.93, what prod printed 2026-10-07
_FY_PFCF = round(_MCAP / _FY_FCF, 2)


# ── harness ──────────────────────────────────────────────────────────────────────


def _build(fr=None, km=None, cf=None, inc=None, bs=None, profile=None, bench=None,
           excluded_rows=frozenset(), ticker="KO") -> SnapshotItemResponse:
    return vss.build_price_snapshot(
        fr=dict(fr or {}), km=dict(km or {}), cf=dict(cf or {}), inc=dict(inc or {}),
        bs=dict(bs or {}), profile=dict(_KO_PROFILE if profile is None else profile),
        bench=dict(_BENCH if bench is None else bench), bench_levels=dict(_LEVELS),
        ticker=ticker, excluded_rows=excluded_rows,
    )


def _m(snap: SnapshotItemResponse, key: str) -> SnapshotMetricResponse:
    return next(m for m in snap.metrics if m.metric_key == key)


_NUMBERS = {"numeric_value", "peer_median", "period_label"}


def _row_without_numbers(m: SnapshotMetricResponse) -> Dict[str, Any]:
    return m.model_dump(exclude=_NUMBERS)


def _assert_fiscal_year_row(m: SnapshotMetricResponse) -> None:
    assert m.numeric_value is None and m.peer_median is None, m


def _assert_same_card_but_the_numbers(fy: SnapshotItemResponse, twin: SnapshotItemResponse,
                                      key: str) -> None:
    """D: the fiscal-year build differs from its TTM twin ONLY in `key`'s two numbers."""
    assert (fy.category, fy.rating, fy.weighted_score, fy.full_report_available) == (
        twin.category, twin.rating, twin.weighted_score, twin.full_report_available)
    assert [m.metric_key for m in fy.metrics] == [m.metric_key for m in twin.metrics]
    for a, b in zip(fy.metrics, twin.metrics):
        if a.metric_key == key:
            assert _row_without_numbers(a) == _row_without_numbers(b), (a, b)
        else:
            assert a.model_dump() == b.model_dump(), (a, b)


# ═══════════════════════════════════════════════════════════════════════════
# A. P/FCF
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("src,key", [
    ("fr", "priceToFreeCashFlowRatioTTM"),
    ("fr", "priceToFreeCashFlowRatio"),
    ("fr", "priceToFreeCashFlowsRatioTTM"),
    ("fr", "priceToFreeCashFlowsRatio"),
    ("km", "pfcfRatioTTM"),
    ("km", "pfcfRatio"),
])
def test_every_pfcf_ratio_key_is_ttm_and_keeps_its_numbers(src, key):
    """/ratios-ttm and /key-metrics-ttm are trailing whatever the key's suffix — and the
    fiscal-year FCF beside them is never read."""
    snap = _build(**{src: {key: 25.83}}, cf={"freeCashFlow": _FY_FCF})
    row = _m(snap, "pfcf")
    assert row.value == "25.83"
    assert row.numeric_value == 25.83 and row.peer_median == 16.0
    assert row.score is not None


@pytest.mark.parametrize("key", ["freeCashFlowYieldTTM", "freeCashFlowYield"])
def test_one_over_the_ttm_fcf_yield_is_ttm(key):
    snap = _build(km={key: 0.04}, cf={"freeCashFlow": _FY_FCF})
    row = _m(snap, "pfcf")
    assert row.value == "25.00"
    assert row.numeric_value == 25.0 and row.peer_median == 16.0


@pytest.mark.parametrize("mcap_source", ["profile", "key_metrics"])
def test_market_cap_over_the_fiscal_year_fcf_carries_no_numbers(mcap_source):
    if mcap_source == "profile":
        fy = _build(cf={"freeCashFlow": _FY_FCF})
    else:
        fy = _build(km={"marketCap": _MCAP}, cf={"freeCashFlow": _FY_FCF}, profile={})
    row = _m(fy, "pfcf")
    assert row.value == f"{_FY_PFCF:.2f}" == "69.93"
    # The label still prints its comparison and the row is still scored (non-vacuous D).
    assert row.name == "P/FCF (4.37x sector avg 16.0)" and row.peer_level == "industry"
    assert row.score == 1
    _assert_fiscal_year_row(row)


def test_pfcf_fiscal_year_build_equals_its_ttm_twin_but_for_the_numbers():
    fy = _build(cf={"freeCashFlow": _FY_FCF})
    twin = _build(fr={"priceToFreeCashFlowRatioTTM": _FY_PFCF}, cf={"freeCashFlow": _FY_FCF})
    _assert_same_card_but_the_numbers(fy, twin, "pfcf")
    assert _m(twin, "pfcf").numeric_value == _FY_PFCF and _m(twin, "pfcf").peer_median == 16.0
    _assert_fiscal_year_row(_m(fy, "pfcf"))


def test_a_neg_printed_from_the_fiscal_year_fcf_is_fiscal_year_too():
    """No TTM ratio or yield, a negative fiscal-year FCF: `_fmt_pfcf` prints "Neg." from that
    year's sign. Its median would draw "Current (TTM) Negative · Industry 16.00x" — calling a
    fiscal-year figure trailing. The TTM twin (a negative TTM yield) keeps its median."""
    fy = _build(cf={"freeCashFlow": -2.0e9})
    twin = _build(km={"freeCashFlowYieldTTM": -0.02}, cf={"freeCashFlow": -2.0e9})
    assert _m(fy, "pfcf").value == _m(twin, "pfcf").value == "Neg."
    _assert_fiscal_year_row(_m(fy, "pfcf"))
    assert _m(twin, "pfcf").peer_median == 16.0 and _m(twin, "pfcf").numeric_value is None
    _assert_same_card_but_the_numbers(fy, twin, "pfcf")


@pytest.mark.parametrize("km,cf,shown", [
    ({"freeCashFlowYieldTTM": -0.02}, {"freeCashFlow": _FY_FCF}, "Neg."),  # TTM sign decides
    ({"freeCashFlowYieldTTM": 0.0}, {"freeCashFlow": _FY_FCF}, "—"),       # TTM yield known
    ({}, {"freeCashFlow": 0.0}, "—"),                       # zero FY FCF: nothing printed
    ({}, {"freeCashFlow": "NaN"}, "—"),                     # NaN FY FCF: absent
    ({}, {"freeCashFlow": float("inf")}, "—"),              # inf FY FCF: absent
    ({}, {}, "—"),                                          # nothing at all
])
def test_a_row_that_printed_no_fiscal_year_figure_keeps_its_median(km, cf, shown):
    row = _m(_build(km=km, cf=cf), "pfcf")
    assert row.value == shown
    assert row.numeric_value is None and row.peer_median == 16.0


@pytest.mark.parametrize("km,profile", [
    ({}, {}),                                   # no market cap anywhere
    ({"marketCap": 0}, {}),                     # zero
    ({"marketCap": -5e9}, {}),                  # negative
    ({"marketCap": "NaN"}, {}),                 # NaN
    ({"marketCap": float("inf")}, {}),          # inf
])
def test_no_usable_market_cap_means_no_reconstruction_and_no_fiscal_year_basis(km, profile):
    row = _m(_build(km=km, profile=profile, cf={"freeCashFlow": _FY_FCF}), "pfcf")
    assert row.value == "—"
    assert row.numeric_value is None and row.peer_median == 16.0


@pytest.mark.parametrize("ratio", [0, 0.0, "NaN", float("nan"), float("inf"), None])
def test_an_absent_ttm_pfcf_ratio_falls_to_the_fiscal_year_rung(ratio):
    """0 is FMP's "absent" on this field; NaN / inf are dropped: with no TTM yield either, the
    fiscal-year reconstruction answers — and is marked so."""
    fr = {"priceToFreeCashFlowRatioTTM": ratio} if ratio is not None else {}
    row = _m(_build(fr=fr, cf={"freeCashFlow": _FY_FCF}), "pfcf")
    assert row.value == "69.93"
    _assert_fiscal_year_row(row)


def test_a_nan_ttm_yield_is_absent_so_the_fiscal_year_rung_answers():
    row = _m(_build(km={"freeCashFlowYieldTTM": float("nan")},
                    cf={"freeCashFlow": _FY_FCF}), "pfcf")
    assert row.value == "69.93"
    _assert_fiscal_year_row(row)


def test_a_negative_ttm_pfcf_ratio_is_ttm():
    row = _m(_build(fr={"priceToFreeCashFlowRatioTTM": -12.0},
                    cf={"freeCashFlow": _FY_FCF}), "pfcf")
    assert row.value == "Neg."
    assert row.numeric_value is None and row.peer_median == 16.0


def test_with_no_median_the_fiscal_year_row_has_no_numbers_and_a_bare_label():
    fy = _build(cf={"freeCashFlow": _FY_FCF}, bench={})
    twin = _build(fr={"priceToFreeCashFlowRatioTTM": _FY_PFCF}, cf={"freeCashFlow": _FY_FCF},
                  bench={})
    assert _m(fy, "pfcf").name == "P/FCF" and _m(fy, "pfcf").value == "69.93"
    _assert_fiscal_year_row(_m(fy, "pfcf"))
    assert _m(twin, "pfcf").numeric_value == _FY_PFCF and _m(twin, "pfcf").peer_median is None
    _assert_same_card_but_the_numbers(fy, twin, "pfcf")


# ═══════════════════════════════════════════════════════════════════════════
# B. EV/EBITDA
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("src,key", [
    ("fr", "enterpriseValueMultipleTTM"),
    ("fr", "enterpriseValueMultiple"),
    ("km", "enterpriseValueMultipleTTM"),
    ("km", "enterpriseValueMultiple"),
    ("fr", "enterpriseValueOverEBITDATTM"),
    ("fr", "enterpriseValueOverEBITDA"),
    ("km", "enterpriseValueOverEBITDATTM"),
    ("km", "enterpriseValueOverEBITDA"),
])
def test_every_ev_ebitda_ratio_key_is_ttm_and_keeps_its_numbers(src, key):
    """AAPL: the TTM multiple 27.59 wins over EV ÷ the fiscal-year EBITDA (32.19)."""
    data = {key: 27.59}
    km = {"enterpriseValue": 4648.51e9, **(data if src == "km" else {})}
    snap = _build(fr=data if src == "fr" else {}, km=km, inc={"ebitda": 144.43e9})
    row = _m(snap, "ev_ebitda")
    assert row.value == "27.59"
    assert row.numeric_value == 27.59 and row.peer_median == 14.2
    assert row.score is not None


_EV = 4648.51e9
_DEBT, _CASH_STI, _CASH = 100e9, 60e9, 30e9

# (id, km, inc, cf, bs, profile, ev, ebitda): every EBITDA rung × every EV rung. Each EBITDA
# rung reads the ANNUAL `inc` / `cf`, so each one is a fiscal-year basis.
_EV_RUNGS: List[Tuple[str, Dict, Dict, Dict, Dict, Optional[Dict], float, float]] = [
    ("km_ev/inc_ebitda", {"enterpriseValue": _EV}, {"ebitda": 144.43e9}, {}, {}, None,
     _EV, 144.43e9),
    ("km_ev/op+da_cf", {"enterpriseValue": _EV},
     {"ebitda": 0, "operatingIncome": 120e9}, {"depreciationAndAmortization": 12e9}, {}, None,
     _EV, 132e9),
    ("km_ev/op+da_inc", {"enterpriseValue": _EV},
     {"operatingIncome": 120e9, "depreciationAndAmortization": 11e9}, {}, {}, None,
     _EV, 131e9),
    ("km_ev/ni+int+tax+da", {"enterpriseValue": _EV},
     {"netIncome": 90e9, "interestExpense": 4e9, "incomeTaxExpense": 16e9},
     {"depreciationAndAmortization": 12e9}, {}, None, _EV, 122e9),
    ("km_ev/ni+da_only", {"enterpriseValue": _EV},
     {"netIncome": 90e9, "depreciationAndAmortization": 12e9}, {}, {}, None, _EV, 102e9),
    ("mcap+debt-sti/inc_ebitda", {}, {"ebitda": 20e9}, {},
     {"totalDebt": _DEBT, "cashAndShortTermInvestments": _CASH_STI}, None,
     _MCAP + _DEBT - _CASH_STI, 20e9),
    ("mcap+debt-cash/inc_ebitda", {}, {"ebitda": 20e9}, {},
     {"totalDebt": _DEBT, "cashAndCashEquivalents": _CASH}, None, _MCAP + _DEBT - _CASH, 20e9),
    ("mcap+debt/inc_ebitda", {}, {"ebitda": 20e9}, {}, {"totalDebt": _DEBT}, None,
     _MCAP + _DEBT, 20e9),
    ("km_mcap+debt/inc_ebitda", {"marketCap": 2e11}, {"ebitda": 20e9}, {},
     {"totalDebt": _DEBT, "cashAndShortTermInvestments": _CASH_STI}, {},
     2e11 + _DEBT - _CASH_STI, 20e9),
    ("nonpositive_km_ev→mcap", {"enterpriseValue": -1e9}, {"ebitda": 20e9}, {},
     {"totalDebt": _DEBT}, None, _MCAP + _DEBT, 20e9),
    ("nan_km_ev→mcap", {"enterpriseValue": "NaN"}, {"ebitda": 20e9}, {},
     {"totalDebt": _DEBT}, None, _MCAP + _DEBT, 20e9),
    ("negative_ebitda", {"enterpriseValue": _EV}, {"ebitda": -50e9}, {}, {}, None,
     _EV, -50e9),
]


@pytest.mark.parametrize("case", _EV_RUNGS, ids=[c[0] for c in _EV_RUNGS])
def test_every_ev_ebitda_reconstruction_is_fiscal_year_and_changes_nothing_else(case):
    _id, km, inc, cf, bs, profile, ev, ebitda = case
    expected = round(ev / ebitda, 2)
    fy = _build(km=km, inc=inc, cf=cf, bs=bs, profile=profile)
    row = _m(fy, "ev_ebitda")
    assert row.value == vss._fmt_ratio(expected)
    _assert_fiscal_year_row(row)
    # The label still prints the median (and, for a positive multiple, its ratio).
    assert "sector avg 14.2" in row.name and row.peer_level == "industry"
    twin = _build(fr={"enterpriseValueMultipleTTM": expected}, km=km, inc=inc, cf=cf, bs=bs,
                  profile=profile)
    _assert_same_card_but_the_numbers(fy, twin, "ev_ebitda")
    trow = _m(twin, "ev_ebitda")
    assert trow.peer_median == 14.2
    assert trow.numeric_value == (expected if expected > 0 else None)


@pytest.mark.parametrize("ratio", ["NaN", float("nan"), float("-inf"), None])
def test_an_absent_ttm_ev_ebitda_falls_to_the_fiscal_year_rung(ratio):
    fr = {"enterpriseValueMultipleTTM": ratio} if ratio is not None else {}
    row = _m(_build(fr=fr, km={"enterpriseValue": _EV}, inc={"ebitda": 144.43e9}),
             "ev_ebitda")
    assert row.value == "32.19"
    _assert_fiscal_year_row(row)


@pytest.mark.parametrize("ratio,shown", [(0, "—"), (-8.0, "Neg.")])
def test_a_zero_or_negative_ttm_ev_ebitda_is_ttm_and_never_reconstructed(ratio, shown):
    """`_first_valid` takes a 0 as a value (unlike P/FCF's zero rule): the TTM key answers,
    the fiscal-year EBITDA is never read, and the median stays."""
    row = _m(_build(fr={"enterpriseValueMultipleTTM": ratio}, km={"enterpriseValue": _EV},
                    inc={"ebitda": 144.43e9}), "ev_ebitda")
    assert row.value == shown
    assert row.numeric_value is None and row.peer_median == 14.2


@pytest.mark.parametrize("km,inc,bs,profile", [
    ({"enterpriseValue": _EV}, {}, {}, None),                         # no EBITDA at all
    ({"enterpriseValue": _EV}, {"ebitda": 0}, {}, None),              # zero EBITDA
    ({"enterpriseValue": _EV}, {"ebitda": "NaN"}, {}, None),          # NaN EBITDA
    ({"enterpriseValue": _EV}, {"operatingIncome": 1e9}, {}, None),   # no D&A for the rung
    ({}, {"ebitda": 20e9}, {}, {}),                                   # no EV, no mcap
    ({}, {"ebitda": 20e9}, {}, None),                                 # mcap but no totalDebt
    ({"enterpriseValue": -1e9}, {"ebitda": 20e9}, {}, {}),            # negative EV, no mcap
])
def test_a_failed_reconstruction_is_not_a_fiscal_year_row(km, inc, bs, profile):
    """"—": no fiscal-year figure was printed, so the row keeps its median (the drill-down's
    "N/A vs industry" line, as before)."""
    row = _m(_build(km=km, inc=inc, bs=bs, profile=profile), "ev_ebitda")
    assert row.value == "—"
    assert row.numeric_value is None and row.peer_median == 14.2


# ═══════════════════════════════════════════════════════════════════════════
# C/D. Both rows at once, the other rows, excluded rows
# ═══════════════════════════════════════════════════════════════════════════

_BOTH_FY = dict(km={"enterpriseValue": _EV}, inc={"ebitda": 144.43e9},
                cf={"freeCashFlow": _FY_FCF})
_TTM_PE = {"priceToEarningsRatioTTM": 29.12, "priceToBookRatioTTM": 9.8,
           "priceToSalesRatioTTM": 6.4}


def test_both_rows_on_a_fiscal_year_basis_leave_every_other_row_and_the_rating_alone():
    fy = _build(fr=_TTM_PE, **_BOTH_FY)
    twin = _build(fr={**_TTM_PE, "priceToFreeCashFlowRatioTTM": 69.93,
                      "enterpriseValueMultipleTTM": 32.19}, **_BOTH_FY)
    for key in ("pfcf", "ev_ebitda"):
        _assert_fiscal_year_row(_m(fy, key))
        assert _m(twin, key).numeric_value is not None and _m(twin, key).peer_median is not None
    assert fy.rating == twin.rating and fy.rating > 0
    assert fy.weighted_score == twin.weighted_score is not None
    for key in ("pe", "pb", "ps", "earnings_yield"):
        assert _m(fy, key).model_dump() == _m(twin, key).model_dump()
        assert _m(fy, key).numeric_value is not None and _m(fy, key).peer_median is not None
    for key in ("pfcf", "ev_ebitda"):
        assert _row_without_numbers(_m(fy, key)) == _row_without_numbers(_m(twin, key))


@pytest.mark.parametrize("peers_withheld", [True, False])
def test_peers_withheld_rating_is_unchanged(peers_withheld):
    """The two-judged rule counts a positive P/FCF / EV/EBITDA beside a usable median — on
    the value, never on the numbers this change drops."""
    kw = dict(fr={"priceToEarningsRatioTTM": 12.0}, km={"enterpriseValue": _EV},
              inc={"ebitda": 144.43e9}, cf={"freeCashFlow": _FY_FCF}, bs={}, profile=_KO_PROFILE,
              bench=_BENCH, bench_levels=_LEVELS, ticker="KO", peers_withheld=peers_withheld)
    fy = vss.build_price_snapshot(**kw)
    twin = vss.build_price_snapshot(**{**kw, "fr": {
        "priceToEarningsRatioTTM": 12.0, "priceToFreeCashFlowRatioTTM": 69.93,
        "enterpriseValueMultipleTTM": 32.19}})
    assert (fy.rating, fy.weighted_score) == (twin.rating, twin.weighted_score)
    assert fy.rating > 0


def test_excluded_rows_stay_n_m_with_no_numbers():
    excluded = frozenset({"ps", "pfcf", "ev_ebitda"})
    snap = _build(fr=_TTM_PE, excluded_rows=excluded, ticker="JPM", **_BOTH_FY)
    for key in excluded:
        row = _m(snap, key)
        assert row.value == "N/M"
        assert (row.numeric_value, row.peer_median, row.peer_level, row.score) == (
            None, None, None, None)


# ═══════════════════════════════════════════════════════════════════════════
# E. The basis is logged once per build
# ═══════════════════════════════════════════════════════════════════════════

def _tag_lines(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == _LOGGER and _TAG in r.getMessage()]


def test_one_info_line_names_the_ticker_and_both_fiscal_year_rows(caplog):
    caplog.set_level(logging.INFO, logger=_LOGGER)
    _build(fr=_TTM_PE, ticker="ZZFY", **_BOTH_FY)
    lines = _tag_lines(caplog)
    assert len(lines) == 1, lines
    assert "ticker=ZZFY" in lines[0] and "rows=pfcf,ev_ebitda" in lines[0]
    assert all(r.levelno == logging.INFO for r in caplog.records if _TAG in r.getMessage())


def test_no_line_on_a_ttm_build(caplog):
    caplog.set_level(logging.INFO, logger=_LOGGER)
    _build(fr={**_TTM_PE, "priceToFreeCashFlowRatioTTM": 25.0,
               "enterpriseValueMultipleTTM": 20.0}, **_BOTH_FY)
    assert _tag_lines(caplog) == []


def test_an_n_m_row_is_never_named(caplog):
    caplog.set_level(logging.INFO, logger=_LOGGER)
    _build(fr=_TTM_PE, ticker="JPM", excluded_rows=frozenset({"pfcf"}), **_BOTH_FY)
    lines = _tag_lines(caplog)
    assert len(lines) == 1 and "rows=ev_ebitda:" in lines[0], lines
    caplog.clear()
    _build(fr=_TTM_PE, ticker="JPM", excluded_rows=frozenset({"ps", "pfcf", "ev_ebitda"}),
           **_BOTH_FY)
    assert _tag_lines(caplog) == []


# ═══════════════════════════════════════════════════════════════════════════
# F. End to end: the report card, and the service's annual statements
# ═══════════════════════════════════════════════════════════════════════════

_HISTORY_POINT = {"unit": "x", "annual": [{"period": "2025", "value": 20.0}], "quarterly": []}
_CURRENT_KEYS = ("current_value", "current_peer_median", "current_score", "current_period")


def _card_rows(snap: SnapshotItemResponse) -> Dict[str, Dict[str, Any]]:
    lookup = {k: dict(_HISTORY_POINT) for k in ("pe", "pfcf", "ev_ebitda")}
    card = C._snapshot_to_card("Valuation", snap, history_lookup=lookup)
    dump = DeepDiveMetricCardResponse.model_validate(card).model_dump()
    json.dumps(dump, allow_nan=False)
    return {m["label"].split(" (")[0]: m for m in card["metrics"]}


def test_the_report_card_draws_no_current_line_for_a_fiscal_year_row():
    fy_rows = _card_rows(_build(fr=_TTM_PE, **_BOTH_FY))
    for label in ("P/FCF", "EV/EBITDA"):
        assert fy_rows[label].get("history_key"), "vacuous: the row is not charted"
        assert not any(k in fy_rows[label] for k in _CURRENT_KEYS), fy_rows[label]
    assert "current_value" in fy_rows["P/E"] and "current_peer_median" in fy_rows["P/E"]

    twin_rows = _card_rows(_build(fr={**_TTM_PE, "priceToFreeCashFlowRatioTTM": 69.93,
                                      "enterpriseValueMultipleTTM": 32.19}, **_BOTH_FY))
    assert twin_rows["P/FCF"]["current_value"] == 69.93
    assert twin_rows["P/FCF"]["current_peer_median"] == 16.0
    assert twin_rows["EV/EBITDA"]["current_value"] == 32.19
    assert twin_rows["EV/EBITDA"]["current_peer_median"] == 14.2


class _FakeFMP:
    """Per-method answers, recording each call's kwargs."""

    def __init__(self, **answers: Any) -> None:
        self._answers = answers
        self.calls: Dict[str, Dict[str, Any]] = {}

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            self.calls[name] = kwargs
            return self._answers.get(name, [])

        return _call


class _Lookup:
    def get_current_benchmarks(self, industry, sector, metrics):
        return {m: {"value": _BENCH[m], "level": "industry",
                    "peer_group_name": industry, "n": 40}
                for m in metrics if m in _BENCH}


@pytest.mark.asyncio
async def test_the_service_reads_annual_statements_and_marks_both_rows(monkeypatch, caplog):
    """The rule's premise: `cf` and `inc` are the LAST FISCAL YEAR (period="annual",
    limit=1). If that fetch ever changes, this fails before a trailing figure is withheld."""
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    monkeypatch.setattr(vss, "get_sector_benchmark_lookup", lambda: _Lookup())
    caplog.set_level(logging.INFO, logger=_LOGGER)
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.supabase = None
    svc.fmp = _FakeFMP(
        get_company_profile=[dict(_KO_PROFILE)],
        get_ratios_ttm=[dict(_TTM_PE)],
        get_key_metrics_ttm=[{"enterpriseValue": _EV}],
        get_cash_flow_statement=[{"freeCashFlow": _FY_FCF}],
        get_income_statement=[{"ebitda": 144.43e9}],
        get_balance_sheet=[{}],
        get_dcf=[],
    )
    snap, degraded = await svc._compute_with_status("KO")
    assert degraded == []
    for method in ("get_cash_flow_statement", "get_income_statement"):
        assert svc.fmp.calls[method].get("period") == "annual", svc.fmp.calls[method]
        assert svc.fmp.calls[method].get("limit") == 1, svc.fmp.calls[method]
    assert _m(snap, "pfcf").value == "69.93" and _m(snap, "ev_ebitda").value == "32.19"
    for key in ("pfcf", "ev_ebitda"):
        _assert_fiscal_year_row(_m(snap, key))
        assert "sector avg" in _m(snap, key).name and _m(snap, key).score is not None
    assert _m(snap, "pe").numeric_value == 29.12 and _m(snap, "pe").peer_median == 22.4
    assert any("ticker=KO" in line for line in _tag_lines(caplog))
    for m in snap.metrics:
        for number in (m.numeric_value, m.peer_median):
            assert number is None or math.isfinite(number)


# ═══════════════════════════════════════════════════════════════════════════
# Review round (2026-11-13): a fiscal-year row NAMES its year, a TTM row never does
# ═══════════════════════════════════════════════════════════════════════════

_CF_FY2025 = {"freeCashFlow": _FY_FCF, "date": "2025-12-31", "fiscalYear": "2025"}


def test_the_fiscal_year_pfcf_row_names_its_statement_year():
    """The drill-down header reads "FY2025 FCF basis: 69.93", never "Current (TTM)": the
    row names the statement year it divides by (`period_label`), and the report row carries it
    as `current_period` with no value, median or score."""
    from app.services.agents import ticker_report_data_collector as C
    row = _m(_build(cf=_CF_FY2025), "pfcf")
    assert row.value == "69.93" and row.period_label == "FY2025"
    _assert_fiscal_year_row(row)
    assert C._current_comparison(row, [row]) == {"current_period": "FY2025"}


def test_a_ttm_row_names_no_period():
    snap = _build(fr={"priceToFreeCashFlowRatioTTM": 25.83, "priceToEarningsRatioTTM": 18.21},
                  cf=_CF_FY2025)
    for m in snap.metrics:
        assert m.period_label is None, m


@pytest.mark.parametrize("cf, expected", [
    ({"freeCashFlow": _FY_FCF, "date": "2026-01-03", "fiscalYear": "2026"}, "FY2025"),  # Jan 1-7 spill
    ({"freeCashFlow": _FY_FCF, "date": "2026-05-31"}, "FY2026"),                       # no fiscalYear
    ({"freeCashFlow": _FY_FCF}, None),                                                  # no date at all
    ({"freeCashFlow": _FY_FCF, "date": "garbage", "fiscalYear": "FY25"}, None),
])
def test_the_statement_year_follows_the_shared_fiscal_year_rule(cf, expected):
    assert _m(_build(cf=cf), "pfcf").period_label == expected


def test_the_fiscal_year_ev_ebitda_row_names_the_income_statement_year():
    snap = _build(km={"enterpriseValue": 400e9},
                  inc={"ebitda": 20e9, "date": "2025-09-27", "fiscalYear": "2025"})
    row = _m(snap, "ev_ebitda")
    assert row.value == "20.00" and row.period_label == "FY2025"
    _assert_fiscal_year_row(row)
