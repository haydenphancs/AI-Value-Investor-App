"""Signal of Confidence P19 (2026-10-01) — a quarter with NO cash-flow row inside the window.

Before: `_build_quarters` kept the point (its share count is real) but filled the cash with
`dividend, buyback = None, 0.0`, so the wire carried a plain $0 / 0.00% that every reader —
the Financials bar labels, the report's mini-chart popup, the 24h cache, the frozen report —
took as a MEASURED zero. A gap inside the newest four also raised `cash_flow_row`, which
kept the ticker out of the 24h tier for up to four quarters (owner decision 1: a vendor
history hole must not), while the report froze the $0 anyway (the reason is ignorable).

After:
  * each point carries `cash_flow_reported` (default True); a gap ships False with 0.0
    placeholders (non-null — shipped iOS decodes the four cash fields as `Double`);
  * an interior or leading-edge gap emits NO degraded reason, so the build persists;
    `cash_flow_row` names ONLY the trimmed newest edge;
  * the summary, the verdict points and the dividend average skip a flagged point even
    when called without `missing_cash_flow_periods`;
  * `_PAYLOAD_VERSION` moved past 9, so a v9 row (whose $0 would decode as reported) is
    refused.

Hermetic (testing.md): every FMP leg is the in-test `_FMP` stand-in from
tests/test_soc_deepcheck.py. Every outlier asserts the CORRECT degraded behaviour.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.schemas.signal_of_confidence import (
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
)
from app.schemas.ticker_report import CapitalAllocationResponse
from app.services import signal_of_confidence_service as sos
from app.services.agents import narrative_prompts as np_
from app.services.agents.ticker_report_data_collector import (
    _build_capital_allocation_block,
    _refuse_degraded_financials,
)
from tests.test_soc_deepcheck import _FMP, _QEND, _cf, _inc, _served, _svc, _wire

_CAP = 100e9


# ── fixtures ────────────────────────────────────────────────────────────────


def _label(d: str) -> str:
    return f"Q{(int(d[5:7]) - 1) // 3 + 1} '{d[2:4]}"


def _payer_cf(d: str) -> dict:
    """$1B bought back and $0.5B paid in dividends every quarter."""
    return _cf(d, commonStockRepurchased=-1e9, commonDividendsPaid=-0.5e9)


def _fmp(*, cf_dates, inc_dates=None, shares=None) -> _FMP:
    """A steady payer on a flat $100B cap (default ratios/profile: a known payer).
    ``shares`` maps an income date to its `weightedAverageShsOut`."""
    inc_dates = list(inc_dates or _QEND)
    shares = shares or {}
    return _FMP(
        cashflow=[_payer_cf(d) for d in cf_dates],
        income=[_inc(d, shares=shares.get(d, 4.3e9)) for d in inc_dates],
        quote={"marketCap": _CAP},
        hist=[{"date": d, "marketCap": _CAP} for d in inc_dates],
    )


def _without(*missing: str) -> list:
    return [d for d in _QEND if d not in missing]


def _report_gate(resp):
    """Run the report collector's frozen-section gate on one SoC build."""
    out = SimpleNamespace(
        ticker="T", degraded_sections=[], signal_of_confidence=resp,
        growth_chart=None, profit_power=None, earnings=None, revenue_breakdown=None,
    )
    _refuse_degraded_financials(out)
    return out.signal_of_confidence, list(out.degraded_sections)


def _by_period(resp) -> dict:
    return {p.period: p for p in resp.data_points}


def _assert_placeholder(p):
    """A flagged point: every cash field a 0.0 FLOAT (never None — shipped iOS decodes
    `Double`), and the flag False."""
    assert p.cash_flow_reported is False
    for key in ("dividend_yield", "buyback_yield", "dividend_amount", "buyback_amount"):
        value = getattr(p, key)
        assert value == 0.0 and isinstance(value, float), (p.period, key, value)


# ── (1) a gap OLDER than the newest four ────────────────────────────────────


@pytest.mark.asyncio
async def test_an_old_interior_gap_is_flagged_and_the_build_persists(monkeypatch, caplog):
    gap = "2024-12-31"
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        out, writes = await _served(_wire(_fmp(cf_dates=_without(gap))), "OLDG", monkeypatch)

    assert out.degraded == []
    assert len(writes) == 1, "a vendor history hole must not keep the build out of the 24h tier"
    assert writes[0][1] is out
    assert sos._cache["signal_of_confidence:OLDG"][2] == sos._CACHE_TTL

    by = _by_period(out)
    assert len(out.data_points) == 8 and _label(gap) in by, "the share line keeps the quarter"
    _assert_placeholder(by[_label(gap)])
    assert by[_label(gap)].shares_outstanding == 4300.0
    assert [p.period for p in out.data_points if not p.cash_flow_reported] == [_label(gap)]
    assert all(p.buyback_amount == 1000.0 for p in out.data_points if p.cash_flow_reported)

    assert out.summary.buyback_yield == pytest.approx(4.0)
    assert out.summary.dividend_yield == pytest.approx(2.0)
    assert "[soc-cashflow-row-missing] ticker=OLDG step=cash_flow" in caplog.text
    assert "cash_flow_reported=false" in caplog.text


# ── (2) a gap WITHIN the newest four ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_recent_interior_gap_no_longer_emits_cash_flow_row(monkeypatch):
    """FAILS on the pre-P19 code: degraded == ['cash_flow_row'] and nothing persisted."""
    gap = "2025-12-31"
    fmp = _fmp(cf_dates=_without(gap))
    resp, _ne, degraded = await _wire(fmp)._build_signal_of_confidence("RCNT")
    assert degraded == [] and resp.degraded == []
    assert "cash_flow_row" not in degraded

    by = _by_period(resp)
    _assert_placeholder(by[_label(gap)])
    assert by[_label(gap)].shares_outstanding == 4300.0
    # four REPORTED quarters, the flagged one skipped (summing it read 3.0 / 1.5)
    assert resp.summary.buyback_yield == pytest.approx(4.0)
    assert resp.summary.dividend_yield == pytest.approx(2.0)

    # The quarters after the gap have no four consecutive rows behind them: x4 fallback.
    _pts, diag = _svc()._build_quarters(
        [_payer_cf(d) for d in _without(gap)], [_inc(d) for d in _QEND], _CAP,
        {d: _CAP for d in _QEND}, "RCNT", pays_common_dividend=True,
    )
    assert diag.missing_cash_flow_periods == [_label(gap)]
    assert diag.missing_cash_flow_recent is False, "only a TRIMMED newest edge sets it"
    assert _label("2026-06-30") in diag.ttm_fallback_periods
    # A fallback newest point with < 4 baseline points: the relative verdict cannot run,
    # so the absolute ladder reads the 2.0% T12M ("High"), never a ratio against a $0.
    assert resp.dividend_info is not None and resp.dividend_info.status == "High"

    out, writes = await _served(_wire(_fmp(cf_dates=_without(gap))), "RCNT", monkeypatch)
    assert out.degraded == [] and len(writes) == 1, "persisted (owner decision 1)"


@pytest.mark.asyncio
async def test_the_report_keeps_the_section_and_forwards_the_flag():
    gap = "2025-12-31"
    resp, _ne, _deg = await _wire(_fmp(cf_dates=_without(gap)))._build_signal_of_confidence("RPT")
    kept, lost = _report_gate(resp)
    assert kept is resp and lost == []

    block = _build_capital_allocation_block(resp)
    flags = {p["period"]: p["cash_flow_reported"] for p in block["data_points"]}
    assert flags[_label(gap)] is False
    assert sum(1 for v in flags.values() if v is False) == 1
    assert block["data_points"][-1]["cash_flow_reported"] is True
    assert block["data_points"][-1]["buyback_amount"] == 1000.0

    model = CapitalAllocationResponse.model_validate(block)
    by = {p.period: p for p in model.data_points}
    assert by[_label(gap)].cash_flow_reported is False
    assert by[_label(gap)].buyback_amount == 0.0


# ── (3) an interior gap plus a one-quarter lagging tail ─────────────────────


@pytest.mark.asyncio
async def test_an_interior_gap_plus_a_lagging_tail_is_cash_flow_row_only(monkeypatch):
    gap, tail = "2025-12-31", "2026-06-30"
    out, writes = await _served(_wire(_fmp(cf_dates=_without(gap, tail))), "BOTH", monkeypatch)
    assert out.degraded == ["cash_flow_row"], "the tail alone names the reason"
    assert writes == [], "kept out of the 24h tier until the newest row lands"
    periods = [p.period for p in out.data_points]
    assert _label(tail) not in periods, "the lagging tail is trimmed, never shipped as $0"
    assert periods[-1] == _label("2026-03-31")
    assert out.data_points[-1].cash_flow_reported is True
    _assert_placeholder(_by_period(out)[_label(gap)])

    kept, lost = _report_gate(out)
    assert kept is out and lost == [], "`cash_flow_row` stays ignorable in the report"


# ── (4) a leading-edge gap ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_leading_edge_gap_is_flagged_not_trimmed(monkeypatch):
    """Cash-flow history starts two quarters after income: the oldest two points keep the
    share line (flagged), and the share-count window spans all eight counts."""
    inc_dates = _QEND[-8:]
    shares = {d: 4.4e9 - i * 0.01e9 for i, d in enumerate(inc_dates)}   # 4400 → 4330
    out, writes = await _served(
        _wire(_fmp(cf_dates=inc_dates[2:], inc_dates=inc_dates, shares=shares)),
        "LEAD", monkeypatch,
    )
    assert out.degraded == [] and len(writes) == 1
    assert [p.cash_flow_reported for p in out.data_points] == [False, False] + [True] * 6
    for p in out.data_points[:2]:
        _assert_placeholder(p)
    assert out.data_points[0].shares_outstanding == 4400.0
    assert out.summary.share_count_change_known is True
    assert out.summary.share_count_change == pytest.approx(round((4330 - 4400) / 4400 * 100, 2))
    assert out.summary.buyback_yield == pytest.approx(4.0)


# ── (5)/(6) outliers ────────────────────────────────────────────────────────


def test_a_flagged_quarter_with_zero_shares_is_still_emitted():
    """FMP's `weightedAverageShsOut: 0` sentinel on the SAME quarter that lacks its
    cash-flow row: shares None, flag False, the point still on the wire, no exception."""
    gap = "2025-03-31"
    pts, diag = _svc()._build_quarters(
        [_payer_cf(d) for d in _without(gap)],
        [_inc(d, shares=0 if d == gap else 4.3e9) for d in _QEND], _CAP,
        {d: _CAP for d in _QEND}, "ZERO", pays_common_dividend=True,
    )
    p = {x.period: x for x in pts}[_label(gap)]
    assert p.shares_outstanding is None
    _assert_placeholder(p)
    assert len(pts) == 8 and diag.missing_cash_flow_recent is False
    s = _svc()._build_summary(pts, _CAP)
    assert s.share_count_change_known is True and s.share_count_change == 0.0


def test_a_measured_zero_is_reported_not_flagged():
    """A row that IS there with a 0 repurchase line on a non-payer is a measurement."""
    dates = _QEND[-8:]
    cfs = [_cf(d, commonStockRepurchased=-1e9) for d in dates[:-1]]
    cfs.append(_cf(dates[-1], commonStockRepurchased=0, commonDividendsPaid=0))
    pts, diag = _svc()._build_quarters(
        cfs, [_inc(d) for d in dates], _CAP, {d: _CAP for d in dates}, "ZERO",
        pays_common_dividend=False,
    )
    newest = pts[-1]
    assert newest.cash_flow_reported is True
    assert newest.buyback_amount == 0.0 and newest.dividend_amount == 0.0
    assert all(p.cash_flow_reported for p in pts)
    assert diag.missing_cash_flow_periods == []


def test_no_gap_flags_nothing():
    pts, diag = _svc()._build_quarters(
        [_payer_cf(d) for d in _QEND], [_inc(d) for d in _QEND], _CAP,
        {d: _CAP for d in _QEND}, "FULL", pays_common_dividend=True,
    )
    assert all(p.cash_flow_reported is True for p in pts)
    assert diag.missing_cash_flow_periods == [] and not diag.missing_cash_flow_recent


# ── (7) the whole statement unusable ────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_empty_statement_flags_every_point_and_is_not_persisted(monkeypatch):
    dates = _QEND[-8:]
    out, writes = await _served(_wire(_fmp(cf_dates=[], inc_dates=dates)), "EMPTY", monkeypatch)
    assert out.degraded == ["cash_flow_statement_missing"]
    assert writes == []
    assert len(out.data_points) == 8
    for p in out.data_points:
        _assert_placeholder(p)
    # nothing measured: the summary sums nothing rather than reading $0 as a measurement
    assert out.summary.buyback_yield == 0.0 and out.summary.dividend_yield == 0.0


@pytest.mark.asyncio
async def test_a_raised_cash_flow_leg_flags_every_point_and_stays_blocking(monkeypatch):
    class _Raising(_FMP):
        async def get_cash_flow_statement(self, ticker, period="quarter", limit=20):
            raise RuntimeError("cash-flow-statement 503")

    dates = _QEND[-8:]
    base = _fmp(cf_dates=[], inc_dates=dates)
    fmp = _Raising(cashflow=[], income=base._inc, quote={"marketCap": _CAP},
                   hist=[{"date": d, "marketCap": _CAP} for d in dates])
    out, writes = await _served(_wire(fmp), "RAIS", monkeypatch)
    assert "cash_flow" in out.degraded and "cash_flow_row" not in out.degraded
    assert writes == []
    assert out.data_points and all(p.cash_flow_reported is False for p in out.data_points)
    kept, lost = _report_gate(out)
    assert kept is None and "signal_of_confidence" in " ".join(lost)


# ── (8) the readers skip a flagged point WITHOUT the builder's diagnostics ───


def _flagged(period: str) -> SignalOfConfidenceDataPointSchema:
    return SignalOfConfidenceDataPointSchema(
        period=period, shares_outstanding=4300.0, cash_flow_reported=False,
    )


def _measured(period: str, *, bb=1000.0, div=500.0, by=4.0, dy=2.0):
    return SignalOfConfidenceDataPointSchema(
        period=period, buyback_amount=bb, dividend_amount=div, buyback_yield=by,
        dividend_yield=dy, shares_outstanding=4300.0,
    )


def test_the_summary_skips_a_flagged_point_on_its_own_flag():
    """No `missing_cash_flow_periods` passed: the point's own flag is enough. Summing the
    0.0 placeholder read 3.0% "High" for a steady 4.0% "Very High" repurchaser."""
    pts = [_measured(f"Q{i} '25") for i in range(1, 5)] + [_measured("Q1 '26"),
                                                            _flagged("Q2 '26")]
    s = _svc()._build_summary(pts, _CAP)
    assert s.buyback_yield == pytest.approx(4.0)
    assert s.dividend_yield == pytest.approx(2.0)
    assert s.buyback_status == "Very High"
    # No current cap: the newest MEASURED point, never the flagged 0.0
    assert _svc()._build_summary(pts, None).buyback_yield == pytest.approx(4.0)


def test_the_verdict_points_skip_a_flagged_point_on_its_own_flag():
    pts = [_measured(f"P{i}") for i in range(8)] + [_flagged("GAP")]
    newest, baseline = sos.SignalOfConfidenceService._verdict_points(pts)
    assert newest.period == "P7", "the flagged 0.0 was read as the newest yield"
    assert [p.period for p in baseline] == ["P0", "P1", "P2", "P3"]


def test_the_dividend_card_skips_a_flagged_point_on_its_own_flag():
    """The test_the_dividend_card_skips_unknown_quarters shape, flagged on the point
    instead of passed as `missing_cash_flow_periods`."""
    pts = [_measured(f"Q{i}", dy=2.0) for i in range(8)] + [_flagged("MISSING")]
    info = _svc()._build_dividend_info([], 2.0, 0.0, 0.0, data_points=pts,
                                       pays_common_dividend=True)
    assert info.status == "Fair", "the flagged newest quarter read as a cut to 0%"
    assert info.five_year_avg_yield == 2.0 and info.avg_yield_window == "8Q"


def test_a_point_without_the_attribute_still_counts():
    """Callers that pass plain objects (no `cash_flow_reported`) keep the old meaning."""
    class _P:
        def __init__(self, y, period):
            self.dividend_yield, self.period = y, period

    pts = [_P(2.0, f"Q{i}") for i in range(8)]
    info = _svc()._build_dividend_info([], 2.0, 0.0, 0.0, data_points=pts,
                                       pays_common_dividend=True)
    assert info.avg_yield_window == "8Q" and info.status == "Fair"


# ── (9) the wire and the 24h row ────────────────────────────────────────────


def test_the_flag_defaults_to_reported():
    assert SignalOfConfidenceDataPointSchema(period="Q1 '26").cash_flow_reported is True
    # A payload built before the key existed (a cached report) keeps its old meaning.
    old = SignalOfConfidenceDataPointSchema.model_validate(
        {"period": "Q1 '26", "dividend_yield": 1.0, "buyback_yield": 2.0,
         "dividend_amount": 10.0, "buyback_amount": 20.0, "shares_outstanding": None})
    assert old.cash_flow_reported is True
    dumped = SignalOfConfidenceDataPointSchema(period="Q1 '26").model_dump()
    assert dumped["cash_flow_reported"] is True


def _sb(payload):
    class _Res:
        data = [{"response_json": payload,
                 "cached_at": datetime.now(timezone.utc).isoformat(),
                 "next_earnings_date": None}]

    class _Tbl:
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return _Res()

    class _SB:
        def table(self, *_a, **_k): return _Tbl()

    return _SB()


@pytest.mark.asyncio
async def test_a_v9_row_is_refused_and_a_current_row_round_trips_the_flag():
    assert sos._PAYLOAD_VERSION >= 10, "P19 ships in the v10 payload"
    gap = "2024-12-31"
    resp, _ne, _deg = await _wire(_fmp(cf_dates=_without(gap)))._build_signal_of_confidence("RT")
    row = {**resp.model_dump(), "payload_version": sos._PAYLOAD_VERSION}

    svc = _svc()
    svc.supabase = _sb(row)
    hit = svc._check_supabase_cache("RT")
    assert isinstance(hit, SignalOfConfidenceResponse)
    assert _by_period(hit)[_label(gap)].cash_flow_reported is False
    assert sum(1 for p in hit.data_points if not p.cash_flow_reported) == 1

    # A v9 row has no flag: its $0 would decode as reported. Refused, recomputed.
    v9 = {**row, "payload_version": 9}
    for p in v9["data_points"]:
        p.pop("cash_flow_reported", None)
    svc.supabase = _sb(v9)
    assert svc._check_supabase_cache("RT") is None


# ── (10) report consumers with a flagged interior point ─────────────────────


def _report_block_with_interior_gap(change: float = 3.0):
    pts = [
        _measured("Q3 '25"),
        _flagged("Q4 '25"),
        _measured("Q1 '26"),
        _measured("Q2 '26", bb=750.0),
    ]
    soc = SignalOfConfidenceResponse.model_validate({
        "symbol": "T",
        "data_points": [p.model_dump() for p in pts],
        "summary": {"total_yield": 6.0, "dividend_yield": 2.0, "buyback_yield": 4.0,
                    "share_count_change": change, "share_count_change_known": True,
                    "buyback_status": "Diluting"},
    })
    return _build_capital_allocation_block(soc)


def test_the_digest_prompt_and_pdf_read_a_flagged_interior_point_without_raising():
    from app.services.agents.persona_config import get_persona_config
    from app.services.pdf_report_service import build_context, render_html

    block = _report_block_with_interior_gap()
    assert [p["cash_flow_reported"] for p in block["data_points"]] == [True, False, True, True]

    lines = np_._digest_insider({"insider_data": {"capital_allocation": block}})
    assert any("Capital allocation" in ln for ln in lines)

    shell = {"insider_data": {"sentiment": "Neutral", "transactions": [],
                              "capital_allocation": block},
             "key_management": {"top_holders": [], "officers": []}}
    prompt = np_._key_management_insight_prompt(
        get_persona_config("warren_buffett"), "evidence", shell,
    )
    # `newest_bb` reads the NEWEST point (measured $750M), not the flagged interior 0.0.
    assert "even though it buys back some stock" in prompt
    assert "it is not repurchasing stock" not in prompt

    html = render_html(build_context({"insider_data": {"capital_allocation": block}}))
    assert isinstance(html, str) and html


# ── (11) fewer than four KNOWN quarters in the trailing window (fix pass) ───
#
# `last_4` only drops unknown points, so 1-3 known quarters used to be summed over the
# current cap and labelled T12M — a steady $1B/quarter repurchaser on a $100B cap read
# 3.0% "High" (3 known), 2.0% / 1.0% "Moderate" (2 / 1 known) and its dividend card "Low".
# Since P19 those builds persist (a vendor hole sets no reason), so the summary must be
# right on its own: the known quarters are ANNUALISED (sum x 4/N over the current cap).


def _assert_annualised(summary):
    assert summary.buyback_yield == pytest.approx(4.0), \
        "fewer than four known quarters were summed as if they were a year"
    assert summary.dividend_yield == pytest.approx(2.0)
    assert summary.total_yield == pytest.approx(6.0)
    assert summary.buyback_status == "Very High"


#: The newest eight income quarters the build displays (Q3 '24 … Q2 '26).
_WINDOW = _QEND[-8:]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing, known", [
    (_WINDOW[1:6], ["Q3 '24", "Q1 '26", "Q2 '26"]),   # w[1..5]: the finding's repro
    (_WINDOW[1:7], ["Q3 '24", "Q2 '26"]),
    (_WINDOW[0:7], ["Q2 '26"]),
])
async def test_an_interior_gap_leaving_fewer_than_four_known_quarters_is_annualised(
    missing, known, monkeypatch, caplog,
):
    tag = "GAP" + "ABC"[len(known) - 1]
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        out, writes = await _served(_wire(_fmp(cf_dates=_without(*missing))), tag, monkeypatch)

    assert [p.period for p in out.data_points if p.cash_flow_reported] == known
    assert out.degraded == [] and len(writes) == 1, "a vendor hole still persists (P19)"
    assert sos._cache[f"signal_of_confidence:{tag}"][2] == sos._CACHE_TTL
    _assert_annualised(out.summary)
    # The newest point is the x4 fallback, so the absolute ladder reads the T12M: 2.0%
    # is "High" — the 0.5% a single summed quarter gave read "Low".
    assert out.dividend_info is not None and out.dividend_info.status == "High"
    assert f"[soc-ttm-partial] ticker={tag} step=summary known={len(known)}" in caplog.text


@pytest.mark.asyncio
async def test_a_late_starting_cash_flow_history_is_annualised(monkeypatch, caplog):
    """IPO shape: eight income quarters, the cash-flow statement only for the newest three
    (a leading-edge gap, no older rows to reach back to)."""
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        out, writes = await _served(
            _wire(_fmp(cf_dates=_WINDOW[-3:], inc_dates=_WINDOW)), "IPOC", monkeypatch)
    assert [p.cash_flow_reported for p in out.data_points] == [False] * 5 + [True] * 3
    assert out.degraded == [] and len(writes) == 1
    assert sos._cache["signal_of_confidence:IPOC"][2] == sos._CACHE_TTL
    _assert_annualised(out.summary)
    assert "[soc-ttm-partial] ticker=IPOC step=summary known=3" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("quarters", [3, 2, 1])
async def test_a_company_listed_under_a_year_is_annualised(quarters, monkeypatch):
    """Both statements cover only 1-3 quarters (no gap at all): this was persisted with
    the partial sum even before P19."""
    dates = _QEND[-quarters:]
    out, writes = await _served(
        _wire(_fmp(cf_dates=dates, inc_dates=dates)), "YNG" + "ABC"[quarters - 1], monkeypatch)
    assert len(out.data_points) == quarters
    assert all(p.cash_flow_reported for p in out.data_points)
    assert out.degraded == [] and len(writes) == 1
    _assert_annualised(out.summary)


@pytest.mark.asyncio
async def test_the_partial_window_is_priced_on_todays_cap_not_each_points_own():
    """The cap DOUBLED since 2024. Three known quarters (Q3 '24, Q1 '26, Q2 '26) of $1B
    on today's $100B cap is 4.0%. Averaging the points' own yields would mix Q3 '24's
    trailing year at its $50B cap (8.0%) into it and read 5.33%."""
    fmp = _fmp(cf_dates=_without(*_WINDOW[1:6]))
    fmp._hist = [{"date": d, "marketCap": 50e9 if d < "2025" else _CAP} for d in _QEND]
    resp, _ne, degraded = await _wire(fmp)._build_signal_of_confidence("RERATE")
    by = _by_period(resp)
    assert by["Q3 '24"].buyback_yield == pytest.approx(8.0), "fixture: its own cap is $50B"
    assert degraded == []
    assert resp.summary.buyback_yield == pytest.approx(4.0)
    assert resp.summary.dividend_yield == pytest.approx(2.0)


def test_the_summary_annualises_a_partial_window_directly(caplog):
    pts = [_measured("Q4 '25", bb=1000.0, div=0.0), _flagged("Q1 '26"),
           _measured("Q2 '26", bb=2000.0, div=0.0), _measured("Q3 '26", bb=3000.0, div=0.0)]
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        s = _svc()._build_summary(pts, _CAP, ticker="DIR")
    # ($1B + $2B + $3B) x 4/3 = $8B over $100B — never the 6.0% the plain sum read
    assert s.buyback_yield == pytest.approx(8.0)
    assert s.dividend_yield == 0.0
    assert "[soc-ttm-partial] ticker=DIR step=summary known=3" in caplog.text
    # The keyword is optional: an old call site still works and logs an unknown ticker.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        assert _svc()._build_summary(pts, _CAP).buyback_yield == pytest.approx(8.0)
    assert "[soc-ttm-partial] ticker=? step=summary known=3" in caplog.text


def test_a_full_window_is_still_the_plain_four_quarter_sum(caplog):
    """Four known quarters: no scaling, no log — a lumpy year is its exact sum."""
    pts = [_measured(f"Q{i} '25", bb=bb, div=0.0)
           for i, bb in enumerate((1000.0, 2000.0, 3000.0, 10000.0), start=1)]
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        s = _svc()._build_summary([_measured("Q4 '24", bb=99000.0)] + pts, _CAP, ticker="FULL")
    assert s.buyback_yield == pytest.approx(16.0)
    assert "[soc-ttm-partial]" not in caplog.text


def test_a_partial_window_without_a_current_cap_keeps_the_point_yield_fallback():
    """No current cap: the existing fallback (the newest trailing-year point, or the mean
    of the x4 points) is already annualised and stays as it was."""
    pts = [_measured("Q1 '26", by=4.0, dy=2.0), _flagged("Q2 '26"),
           _measured("Q3 '26", by=5.0, dy=2.0)]
    s = _svc()._build_summary(pts, None, ttm_fallback_periods={"Q1 '26", "Q3 '26"})
    assert s.buyback_yield == pytest.approx(4.5) and s.dividend_yield == pytest.approx(2.0)
    assert _svc()._build_summary(pts, None).buyback_yield == pytest.approx(5.0)
