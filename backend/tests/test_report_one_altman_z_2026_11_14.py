"""One Altman Z per report (2026-11-14).

ORCL's report showed the Health card's Altman Z 1.48 (the Health Check: latest-quarter balance
sheet + trailing-four-quarter income + market cap), while `_compute_metrics` computed its own
Z from the ANNUAL FY2026 statements with today's market cap: 1.56. That second figure went into
the model's EVIDENCE ("Altman Z-Score: 1.56"), which the thesis and critical-factor syntheses
may quote verbatim, so a bull/bear bullet could print 1.56 beside a card reading 1.48.

`c["altman_z"]` is now the card's own Z (`_card_altman_z(out.snap_health)`); the annual
computation stands in only when the card carries none. Hermetic: no FMP, no Supabase. Each
test names the mutation it catches.
"""

from __future__ import annotations

import math

from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services.agents import ticker_report_data_collector as C

# ORCL's FY2026 (2026-05-31) statements and the 2026-11-14 market cap: the annual Z is 1.56.
_ANNUAL_BALANCE = [{
    "date": "2026-05-31", "fiscalYear": "2026", "totalCurrentAssets": 46.567e9,
    "totalCurrentLiabilities": 41.764e9, "totalAssets": 261.759e9,
    "totalLiabilities": 218.703e9, "retainedEarnings": -4.309e9,
    "totalDebt": 156.189e9, "totalStockholdersEquity": 42.508e9,
    "cashAndCashEquivalents": 31.289e9,
}]
_ANNUAL_INCOME = [
    {"date": "2026-05-31", "fiscalYear": "2026", "revenue": 67.358e9,
     "operatingIncome": 20.778e9, "netIncome": 12.4e9},
    {"date": "2025-05-31", "fiscalYear": "2025", "revenue": 57.4e9,
     "operatingIncome": 17.7e9, "netIncome": 12.4e9},
]
_MKT_CAP = 379_025_809_000


def _health_snap(z_row: dict | None) -> SnapshotItemResponse:
    rows = [SnapshotMetricResponse(name="Debt-to-Equity (vs sector 0.18)", value="2.34",
                                   metric_key="debt_to_equity", score=2,
                                   numeric_value=2.34, peer_median=0.18)]
    if z_row is not None:
        rows.append(SnapshotMetricResponse(name="Altman Z-Score", metric_key="altman_z",
                                           score=2, **z_row))
    return SnapshotItemResponse(category="Financial Health", rating=2, metrics=rows,
                                weighted_score=2.0)


def _collected(snap_health, *, sector="Technology",
               industry="Software - Infrastructure") -> C.CollectedTickerData:
    out = C.CollectedTickerData(ticker="ORCL", persona_key="warren_buffett")
    out.profile = {"companyName": "Oracle Corporation", "symbol": "ORCL", "sector": sector,
                   "industry": industry, "mktCap": _MKT_CAP, "exchangeShortName": "NYSE"}
    out.quote = {"price": 125.35}
    out.income = [dict(r) for r in _ANNUAL_INCOME]
    out.balance = [dict(r) for r in _ANNUAL_BALANCE]
    out.cash_flow = [{"freeCashFlow": -23.7e9, "operatingCashFlow": 20e9}]
    out.ratios = [{"debtToEquityRatio": 3.67, "currentRatio": 1.12}]
    out.ratios_ttm = [{"debtToEquityRatioTTM": 2.335, "currentRatioTTM": 1.1708,
                       "quickRatioTTM": 1.1708, "interestCoverageRatioTTM": 4.552}]
    out.historical = {"historical": [{"date": f"2026-11-{d:02d}", "close": 125.35}
                                     for d in range(2, 14)]}
    out.snap_health = snap_health
    return out


def _compute(out: C.CollectedTickerData) -> C.CollectedTickerData:
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    coll._compute_metrics(out)
    return out


def test_the_annual_fixture_really_reads_1_56():
    """Guard on the fixture itself: without a card Z the annual statements give 1.56, so the
    tests below prove the card's value REPLACES a different number."""
    assert C._altman_z(_ANNUAL_BALANCE, _ANNUAL_INCOME, {"mktCap": _MKT_CAP,
                       "sector": "Technology"}) == 1.56


def test_the_report_uses_the_cards_z_in_computed_and_in_evidence():
    """Mutation caught: computing `c["altman_z"]` from the annual statements again (1.56)."""
    out = _compute(_collected(_health_snap({"value": "1.48", "numeric_value": 1.48})))
    assert out.computed["altman_z"] == 1.48
    ctx = C.build_financial_context(out)
    assert "Altman Z-Score: 1.48" in ctx
    assert "1.56" not in ctx


def test_no_snapshot_falls_back_to_the_annual_z():
    """No Health card at all (a refused or failed snapshot): the annual computation stands
    in. Mutation caught: dropping the fallback (the EVIDENCE line would read N/A)."""
    out = _compute(_collected(None))
    assert out.computed["altman_z"] == 1.56


def test_a_card_without_a_z_row_falls_back_to_the_annual_z():
    out = _compute(_collected(_health_snap(None)))
    assert out.computed["altman_z"] == 1.56


def test_a_bank_without_a_z_row_stays_unmeasured():
    """A bank's card omits Altman Z and the annual fallback refuses it too (the ONE formula's
    sector gate) — no Z reaches the model. Mutation caught: a fallback that skips the gate."""
    out = _compute(_collected(_health_snap(None), sector="Financial Services",
                              industry="Banks - Diversified"))
    assert out.computed["altman_z"] is None


def test_a_dash_z_row_falls_back_and_a_printed_number_is_read():
    """A row printing "—" carries no value (fallback); a row with only the printed "1.48"
    (no numeric_value) is still the card's figure."""
    dash = _compute(_collected(_health_snap({"value": "—"})))
    assert dash.computed["altman_z"] == 1.56
    printed = _compute(_collected(_health_snap({"value": "1.48"})))
    assert printed.computed["altman_z"] == 1.48


def test_non_finite_or_wrong_type_card_values_are_not_a_z():
    """NaN / inf / a bool never stand in for a Z (`_finite_or_none`): the reader returns
    None and the caller falls back. Exercised on the reader directly, since the response
    model refuses some of these shapes on its own."""
    def snap(v):
        return {"metrics": [{"metric_key": "altman_z", "numeric_value": v, "value": "—"}]}
    for bad in (math.nan, math.inf, -math.inf, True, "abc", None):
        assert C._card_altman_z(snap(bad)) is None, bad
    assert C._card_altman_z(snap(1.48)) == 1.48


def test_the_reader_accepts_a_dict_shaped_snapshot_and_ignores_junk():
    """A collection read back from cache may hold plain dicts; junk shapes are None."""
    assert C._card_altman_z({"metrics": [{"metric_key": "altman_z", "value": "1.48"}]}) == 1.48
    for junk in (None, {}, {"metrics": None}, {"metrics": "x"}, [], 3):
        assert C._card_altman_z(junk) is None
    # A row that is not the Z is never read as one.
    assert C._card_altman_z({"metrics": [{"metric_key": "debt_to_equity",
                                          "numeric_value": 2.34}]}) is None
