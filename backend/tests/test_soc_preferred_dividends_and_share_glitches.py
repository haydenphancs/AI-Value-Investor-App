"""Signal of Confidence — TestFlight 1.0 (11), 2026-10-04: CRWV → Financials → Capital ($).

The owner's screenshot: a lone full-height "$3M" DIVIDEND bar in Q2 '25 over a "$0–$3M"
axis, "$0M" in every other cell, and the share line dipping to 435M in Q4 '25 between 498M
and 527M. Checked against FMP live (2026-10-05) and CoreWeave's own 10-Q:

* CoreWeave has never declared a common dividend. The $2.59M is part of the $29M of
  "Redeemable convertible preferred stock cash dividends paid" in H1 2025 (Series C, pre-IPO).
  FMP's ANNUAL FY2025 row books all $29M as `preferredDividendsPaid`; its Q2 2025 QUARTERLY
  row tagged $2.59M `commonDividendsPaid`. And `ratios.dividendPerShare` is the NET (common +
  preferred) per share — 0.1428 / 0.0667 for FY2024 / FY2025 — so the per-share record said
  "payer", the per-fiscal-year gate kept the mis-tagged bar and the card showed a dividend
  history ("Dividend / Share (FY2025) $0.0667", status Low).
* FMP copies the ANNUAL weighted-average share count into some fiscal-Q4 rows: CRWV Q4 2025
  read 435M (its FY2025 annual figure) while net income / EPS gives ~508M.

Math tests over plain dicts plus the real builder behind a hermetic FMP stand-in
(`.claude/rules/testing.md` §1, never a live call). The figures are the relevant fields of
the live rows, trimmed to what each assertion needs.
"""
from __future__ import annotations

import asyncio
import copy
import math

import pytest

from app.services import signal_of_confidence_service as sos
from app.services.signal_of_confidence_service import SignalOfConfidenceService as S


def _svc() -> S:
    return S.__new__(S)


# ── CRWV, the live shape (2026-10-05) ────────────────────────────────────────

# (date, fiscal period, fiscal year, weightedAverageShsOut, netIncome, eps) — newest first.
_CRWV_INCOME = [
    ("2026-06-30", "Q2", "2026", 551_000_000, -626_000_000, -1.14),
    ("2026-03-31", "Q1", "2026", 527_000_000, -740_000_000, -1.40),
    ("2025-12-31", "Q4", "2025", 435_000_000, -451_726_000, -0.89),   # = the FY2025 annual average
    ("2025-09-30", "Q3", "2025", 497_886_000, -110_124_000, -0.22),
    ("2025-06-30", "Q2", "2025", 486_591_000, -290_509_000, -0.60),
    ("2025-03-31", "Q1", "2025", 404_407_000, -314_641_000, -0.78),
    ("2024-12-31", "Q4", "2024", 404_407_000, -51_372_000, -0.17),
    ("2024-09-30", "Q3", "2024", 435_533_000, -359_807_000, -0.89),   # EPS implies 404.3M
    ("2024-06-30", "Q2", "2024", 403_727_000, -323_021_000, -0.84),
    ("2024-03-31", "Q1", "2024", 403_727_000, -129_248_000, -0.32),
    ("2023-12-31", "Q4", "2023", 403_727_000, -170_574_000, -0.42),
]

# (date, commonDividendsPaid, netDividendsPaid, commonStockRepurchased) — newest first.
_CRWV_CF_Q = [
    ("2026-06-30", 0, 0, 0),
    ("2026-03-31", 0, 0, 0),
    ("2025-12-31", 0, -307_000, 0),
    ("2025-09-30", 0, 0, 0),
    ("2025-06-30", -2_592_000, -2_592_000, 0),   # the mis-tag: Series C PREFERRED per the 10-Q
    ("2025-03-31", 0, -26_101_000, 0),
    ("2024-12-31", 0, -28_414_000, 0),
    ("2024-09-30", 0, -29_331_000, -1_470_000),  # a real (tiny, pre-IPO) repurchase
    ("2024-06-30", 0, 0, 0),
    ("2024-03-31", 0, 0, 0),
    ("2023-12-31", 0, 0, -32_054_000),
]

CRWV_CF_ANNUAL = [
    {"date": "2025-12-31", "fiscalYear": "2025", "period": "FY", "commonDividendsPaid": 0,
     "preferredDividendsPaid": -29_000_000, "netDividendsPaid": -29_000_000,
     "netIncome": -1_167_000_000, "operatingCashFlow": 3_058_000_000},
    {"date": "2024-12-31", "fiscalYear": "2024", "period": "FY", "commonDividendsPaid": 0,
     "preferredDividendsPaid": -57_745_000, "netDividendsPaid": -57_745_000,
     "netIncome": -863_448_000, "operatingCashFlow": 2_749_168_000},
    {"date": "2023-12-31", "fiscalYear": "2023", "period": "FY", "commonDividendsPaid": 0,
     "preferredDividendsPaid": 0, "netDividendsPaid": 0},
]

# The live per-share figures: each is that year's total over the ANNUAL weighted-average
# count (FY2025 435,000,000 and FY2024 404,407,000 — what FMP copies into the Q4 rows).
CRWV_RATIOS = [
    {"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 0.06666666666666667,
     "netIncomePerShare": -2.682758620689655, "operatingCashFlowPerShare": 7.029885057471264},
    {"date": "2024-12-31", "fiscalYear": "2024", "dividendPerShare": 0.14278931868142739,
     "netIncomePerShare": -2.135096573501448, "operatingCashFlowPerShare": 6.798022783977528},
    {"date": "2023-12-31", "fiscalYear": "2023", "dividendPerShare": 0},
    {"date": "2022-12-31", "fiscalYear": "2022", "dividendPerShare": 0},
]

# Period-end caps (approximate shapes; pre-IPO quarters have none and use the current cap).
_CRWV_MCAP = {
    "2025-03-31": 19e9, "2025-06-30": 80e9, "2025-09-30": 67e9,
    "2025-12-31": 35e9, "2026-03-31": 40e9, "2026-06-30": 48e9,
}
_CRWV_CURRENT_CAP = 47_677_396_207

# FY annual weighted averages the annual statements imply (FY2025 435M, FY2024 404.4M), and
# Q4 '25 like with like: its EPS-implied count over Q3's, times Q3's reported count.
_CRWV_ANNUAL = S._annual_share_counts(CRWV_RATIOS, CRWV_CF_ANNUAL)
_CRWV_Q4_25_LIKE = (451_726_000 / 0.89) * 497_886_000 / (110_124_000 / 0.22)   # ≈ 504.84M


def _crwv_income():
    return [
        {"date": d, "period": p, "fiscalYear": fy, "weightedAverageShsOut": sh,
         "netIncome": ni, "eps": eps}
        for d, p, fy, sh, ni, eps in _CRWV_INCOME
    ]


def _crwv_cf_quarterly():
    return [
        {"date": d, "period": "Q", "commonDividendsPaid": c, "netDividendsPaid": n,
         "commonStockRepurchased": r}
        for d, c, n, r in _CRWV_CF_Q
    ]


class _FMP:
    """Hermetic FMP stand-in that answers the ANNUAL and QUARTERLY cash-flow legs apart."""

    def __init__(self, *, annual_cf=None, annual_cf_raises=False, ratios=None,
                 ratios_raises=False, profile=None):
        self.annual_cf = CRWV_CF_ANNUAL if annual_cf is None else annual_cf
        self.annual_cf_raises = annual_cf_raises
        self.ratios = CRWV_RATIOS if ratios is None else ratios
        self.ratios_raises = ratios_raises
        self.profile = profile if profile is not None else [
            {"lastDividend": 0, "marketCap": _CRWV_CURRENT_CAP, "price": 87.39}
        ]
        self.calls = []

    async def get_cash_flow_statement(self, ticker, period="annual", limit=10):
        self.calls.append(("cash-flow-statement", period, limit))
        if period == "annual":
            if self.annual_cf_raises:
                raise RuntimeError("FMP 503 on the annual cash-flow statement")
            return self.annual_cf
        return _crwv_cf_quarterly()

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return _crwv_income()

    async def get_financial_ratios(self, ticker, period="annual", limit=10):
        if self.ratios_raises:
            raise RuntimeError("429")
        return self.ratios

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return []

    async def get_historical_market_cap(self, ticker, from_date=None, to_date=None, limit=2000):
        return [{"date": d, "marketCap": v} for d, v in _CRWV_MCAP.items()]

    async def get_company_profile(self, ticker):
        return self.profile

    async def get_stock_price_quote(self, ticker):
        return {"marketCap": _CRWV_CURRENT_CAP, "price": 87.39}


class _CorporateActions:
    async def get_ex_dividend_dates(self, symbol, from_date=None, to_date=None):
        return []

    async def unclassified_adjustment_or_none(self, *a, **k):
        return False


def _wire(fmp) -> S:
    from tests._price_fakes import PriceFromFMPFake
    svc = _svc()
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    svc.corporate_actions = _CorporateActions()
    return svc


def _by_period(resp):
    return {p.period: p for p in resp.data_points}


# ── the per-share record becomes COMMON-only ─────────────────────────────────


def test_crwv_preferred_only_years_scale_to_zero():
    out = S._common_dividend_ratios(CRWV_RATIOS, CRWV_CF_ANNUAL, "CRWV")
    by_year = S._annual_dividend_map(out)
    assert by_year == {"2025": 0.0, "2024": 0.0, "2023": 0.0, "2022": 0.0}
    assert S._pays_common_dividend(out, {"lastDividend": 0}, []) is False
    assert S._build_annual_dividends(out) == [], "a company that never paid has no series"


def test_the_unsplit_record_is_what_made_crwv_a_payer():
    """The control: without the split the record says payer — the shipped defect."""
    assert S._pays_common_dividend(CRWV_RATIOS, {"lastDividend": 0}, []) is True


def test_a_preferred_issuer_keeps_its_common_dividend_only():
    """WFC FY2025, live: ratios 2.0396 (net 6,484M / 3,179.1M sh) vs common 5,434M → 1.7093,
    against a declared $1.70."""
    ratios = [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 2.03957}]
    cf = [{"date": "2025-12-31", "fiscalYear": "2025", "period": "FY",
           "commonDividendsPaid": -5_434_000_000, "preferredDividendsPaid": -1_050_000_000,
           "netDividendsPaid": -6_484_000_000}]
    out = S._common_dividend_ratios(ratios, cf, "WFC")
    assert out[0]["dividendPerShare"] == pytest.approx(2.03957 * 5_434 / 6_484)
    assert round(out[0]["dividendPerShare"], 4) == 1.7093
    assert ratios[0]["dividendPerShare"] == 2.03957, "the input rows must not be mutated"


def test_a_common_only_payer_is_untouched():
    ratios = [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 2.0402}]
    cf = [{"date": "2025-12-31", "fiscalYear": "2025", "period": "FY",
           "commonDividendsPaid": -8_779_000_000, "preferredDividendsPaid": 0,
           "netDividendsPaid": -8_779_000_000}]
    out = S._common_dividend_ratios(ratios, cf, "KO")
    assert out[0] is ratios[0], "an unchanged row is passed through, not rebuilt"


@pytest.mark.parametrize("row, expected", [
    # a line missing, or not a number → the split is unknowable → FMP's figure stands
    ({"netDividendsPaid": -10e6}, None),
    ({"commonDividendsPaid": None, "preferredDividendsPaid": 0, "netDividendsPaid": -10e6}, None),
    ({"commonDividendsPaid": "n/a", "preferredDividendsPaid": 0, "netDividendsPaid": -10e6}, None),
    ({"commonDividendsPaid": float("nan"), "preferredDividendsPaid": 0,
      "netDividendsPaid": -10e6}, None),
    ({"commonDividendsPaid": 0, "netDividendsPaid": -10e6}, None),          # no preferred line
    ({"commonDividendsPaid": 0, "preferredDividendsPaid": None, "netDividendsPaid": -10e6}, None),
    # the lines do not add up to the net — a row that zeroed both and kept only the net
    # must never read as "0% common" (it would erase a real payer's dividend)
    ({"commonDividendsPaid": 0, "preferredDividendsPaid": 0, "netDividendsPaid": -10e6}, None),
    ({"commonDividendsPaid": -5e6, "preferredDividendsPaid": -1e6, "netDividendsPaid": -10e6}, None),
    # …within 1% (or $1,000) they do (ET FY2023: $3M of $4.2B)
    ({"commonDividendsPaid": -4_248e6, "preferredDividendsPaid": -3e6,
      "netDividendsPaid": -4_248e6}, 1.0),
    ({"commonDividendsPaid": -6_000, "preferredDividendsPaid": -4_000,
      "netDividendsPaid": -10_900}, 6_000 / 10_900),
    # no net → common + preferred is the total
    ({"commonDividendsPaid": -6e6, "preferredDividendsPaid": -4e6}, 0.6),
    ({"commonDividendsPaid": -6e6}, None),
    # nothing paid (or an inflow) → nothing to apportion
    ({"commonDividendsPaid": 0, "preferredDividendsPaid": 0, "netDividendsPaid": 0}, None),
    ({"commonDividendsPaid": 5e6, "preferredDividendsPaid": 0, "netDividendsPaid": 5e6}, None),
    # a common INFLOW (refund / sign error) is no common dividend
    ({"commonDividendsPaid": 5e6, "preferredDividendsPaid": -15e6, "netDividendsPaid": -10e6}, 0.0),
    # common larger than the total (an odd but consistent row) is clamped, never > 1
    ({"commonDividendsPaid": -12e6, "preferredDividendsPaid": 2e6, "netDividendsPaid": -10e6}, 1.0),
    ({"commonDividendsPaid": -10e6, "preferredDividendsPaid": 0, "netDividendsPaid": -10e6}, 1.0),
])
def test_common_share_edge_rows(row, expected):
    shares = S._common_dividend_shares(
        [{"date": "2025-12-31", "fiscalYear": "2025", "period": "FY", **row}]
    )
    if expected is None:
        assert shares == {}
    else:
        assert shares == {"2025": pytest.approx(expected)}


def test_a_quarterly_row_is_never_read_as_the_year():
    cf = [{"date": "2025-06-30", "fiscalYear": "2025", "period": "Q2",
           "commonDividendsPaid": -2_592_000, "netDividendsPaid": -2_592_000}]
    assert S._common_dividend_shares(cf) == {}
    # …and the record it would have "confirmed" stays as FMP sent it
    assert S._common_dividend_ratios(CRWV_RATIOS, cf, "CRWV")[0]["dividendPerShare"] == \
        CRWV_RATIOS[0]["dividendPerShare"]


def test_two_rows_for_one_year_keep_the_later_date():
    cf = [
        {"date": "2025-12-31", "fiscalYear": "2025", "period": "FY",
         "commonDividendsPaid": -1e6, "preferredDividendsPaid": -9e6, "netDividendsPaid": -10e6},
        {"date": "2025-12-28", "fiscalYear": "2025", "period": "FY",
         "commonDividendsPaid": -10e6, "preferredDividendsPaid": 0, "netDividendsPaid": -10e6},
    ]
    assert S._common_dividend_shares(cf) == {"2025": pytest.approx(0.1)}
    assert S._common_dividend_shares(list(reversed(cf))) == {"2025": pytest.approx(0.1)}


def test_fiscal_years_match_on_fiscal_year_not_the_end_date():
    """A Home-Depot-style year (FY2025 ends 2026-02-01) pairs ratios and cash flow by
    `fiscalYear`, the same key the per-share map uses."""
    ratios = [{"date": "2026-02-01", "fiscalYear": "2025", "dividendPerShare": 9.2}]
    cf = [{"date": "2026-02-01", "fiscalYear": "2025", "period": "FY",
           "commonDividendsPaid": -4.6e9, "preferredDividendsPaid": -4.6e9,
           "netDividendsPaid": -9.2e9}]
    out = S._common_dividend_ratios(ratios, cf, "X")
    assert S._annual_dividend_map(out) == {"2025": pytest.approx(4.6)}


@pytest.mark.parametrize("ratios", [
    [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": None}],
    [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": -1.0}],
    [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 0}],
    [{"date": "2025-12-31", "fiscalYear": "2025"}],                       # key drifted away
    ["not a row", {"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 0.5}],
])
def test_unusable_record_rows_pass_through(ratios):
    cf = [{"date": "2025-12-31", "fiscalYear": "2025", "period": "FY",
           "commonDividendsPaid": 0, "preferredDividendsPaid": -10e6, "netDividendsPaid": -10e6}]
    before = copy.deepcopy(ratios)
    out = S._common_dividend_ratios(ratios, cf, "X")
    assert len(out) == len(ratios)
    for got, raw in zip(out, before):
        if isinstance(raw, dict) and isinstance(raw.get("dividendPerShare"), (int, float)) \
                and raw["dividendPerShare"] > 0:
            assert got["dividendPerShare"] == 0.0     # a positive year IS scaled (to 0 here)
        else:
            assert got == raw
    assert ratios == before


@pytest.mark.parametrize("annual_cf", [None, "error", {"Error Message": "x"}, [], [None, 3]])
def test_no_usable_annual_rows_leave_the_record_alone(annual_cf):
    out = S._common_dividend_ratios(CRWV_RATIOS, annual_cf, "CRWV")
    assert out == CRWV_RATIOS
    assert S._common_dividend_ratios("not a list", CRWV_CF_ANNUAL) == "not a list"


# ── the builder: one verdict, bars + card + report ───────────────────────────


@pytest.mark.asyncio
async def test_crwv_charts_no_dividend_and_no_card():
    resp, _, degraded = await _wire(_FMP())._build_signal_of_confidence("CRWV")
    assert degraded == []
    assert resp.data_points
    assert all(p.dividend_amount == 0.0 and p.dividend_yield == 0.0 for p in resp.data_points), \
        "the Q2 '25 preferred payment still charts as a common dividend"
    assert resp.summary.dividend_yield == 0.0
    assert resp.dividend_info is None, "a company that never paid a dividend gets no dividend card"
    # the real (tiny, pre-IPO) repurchase is still charted — never zeroed with the dividend
    assert _by_period(resp)["Q3 '24"].buyback_amount == 1.47
    assert resp.summary.buyback_status == "Diluting"


@pytest.mark.asyncio
async def test_crwv_report_block_says_no_dividend():
    from app.services.agents.ticker_report_data_collector import _build_capital_allocation_block

    resp, _, _ = await _wire(_FMP())._build_signal_of_confidence("CRWV")
    block = _build_capital_allocation_block(resp)
    assert block["dividend_status"] == "None"
    assert block["dividend_yield"] == 0.0
    assert all(dp["dividend_amount"] == 0.0 for dp in block["data_points"])


@pytest.mark.asyncio
async def test_the_report_digest_cites_the_window_the_change_is_measured_over():
    """Q3 '24's count is refused (null), so the +36.25% runs Q4 '24 → Q2 '26 — the digest
    must not claim the null-count quarter as its start."""
    from app.services.agents import narrative_prompts as np_
    from app.services.agents.ticker_report_data_collector import _build_capital_allocation_block

    resp, _, _ = await _wire(_FMP())._build_signal_of_confidence("CRWV")
    block = _build_capital_allocation_block(resp)
    assert block["data_points"][0]["period"] == "Q3 '24"
    assert block["data_points"][0]["shares_outstanding"] is None
    digest = "\n".join(np_._digest_insider({"insider_data": {"capital_allocation": block}}))
    # 36.25 formats "+36.2" (round-half-even on an exact binary half). Round 5: the window
    # is named by its START only (see the 10-K-window digest test below).
    assert "share count +36.2% since Q4 '24" in digest, digest
    assert "Q3 '24" not in digest


@pytest.mark.parametrize("points, expected", [
    ([{"period": "A", "shares_outstanding": None}, {"period": "B", "shares_outstanding": 10.0},
      {"period": "C", "shares_outstanding": 11.0}, {"period": "D", "shares_outstanding": 0.0}],
     "since B"),
    ([{"period": "A", "shares_outstanding": True}, {"period": "B", "shares_outstanding": 10.0},
      {"period": "C", "shares_outstanding": "12"}, {"period": "D", "shares_outstanding": 12.0}],
     "since B"),
    ([{"period": "A", "shares_outstanding": 10.0}, "junk", {"period": "B"}], None),
])
def test_digest_window_skips_points_without_a_count(points, expected):
    from app.services.agents import narrative_prompts as np_

    ca = {"share_count_change": 10.0, "share_count_change_known": True, "data_points": points}
    digest = "\n".join(np_._digest_insider({"insider_data": {"capital_allocation": ca}}))
    if expected is None:
        assert "share count +10.0%" in digest and " since " not in digest, digest
    else:
        assert f"share count +10.0% {expected}" in digest, digest
        assert " to " not in digest, "the window's end is never named"


@pytest.mark.asyncio
async def test_a_failed_annual_leg_keeps_the_record_unsplit_and_is_never_persisted(monkeypatch):
    """Without the split CRWV cannot be told from a payer, so the build carries
    `annual_cash_flow` and stays out of the 24h tier (the positive control shows the spy
    sees a healthy write)."""
    svc = _wire(_FMP(annual_cf_raises=True))
    resp, _, degraded = await svc._build_signal_of_confidence("CRWV")
    assert degraded == ["annual_cash_flow"]
    assert _by_period(resp)["Q2 '25"].dividend_amount == 2.59, \
        "control: this IS the unsplit record's answer — why the build must not be pinned"

    async def _run(service):
        writes = []
        monkeypatch.setattr(service, "_check_supabase_cache", lambda t: None)
        monkeypatch.setattr(service, "_upsert_supabase_cache_safe", lambda *a, **k: writes.append(a))
        sos._cache.clear()
        sos._inflight.clear()
        out = await service.get_signal_of_confidence("CRWV")
        for _ in range(50):
            if writes:
                break
            await asyncio.sleep(0.01)
        return out, writes

    out, writes = await _run(_wire(_FMP()))
    assert out.degraded == [] and writes, "control: a healthy build reaches the 24h tier"
    out, writes = await _run(_wire(_FMP(annual_cf_raises=True)))
    assert out.degraded == ["annual_cash_flow"] and writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"Error Message": "Limit Reach"}, "oops", None])
async def test_a_non_list_annual_answer_is_a_failed_leg(bad):
    fmp = _FMP()
    fmp.annual_cf = bad
    resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("CRWV")
    assert degraded == ["annual_cash_flow"]


@pytest.mark.asyncio
async def test_an_empty_annual_answer_against_a_paying_record_is_a_failed_leg(caplog, monkeypatch):
    """Review 2026-10-07: `[]` used to keep the preferred-inclusive record silently and
    persist it for 24h. `ratios` is derived from the annual statement, so an empty annual
    answer beside a paying record (and a quarterly statement that answered) is a bad answer:
    memory only, like a raise."""
    caplog.set_level("WARNING")
    svc = _wire(_FMP(annual_cf=[]))
    resp, _, degraded = await svc._build_signal_of_confidence("CRWV")
    assert degraded == ["annual_cash_flow"]
    assert "[soc-annual-cashflow-empty]" in caplog.text and "FY2024, FY2025" in caplog.text

    writes = []
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda t: None)
    monkeypatch.setattr(svc, "_upsert_supabase_cache_safe", lambda *a, **k: writes.append(a))
    sos._cache.clear()
    sos._inflight.clear()
    out = await svc.get_signal_of_confidence("CRWV")
    await asyncio.sleep(0.05)
    assert out.degraded == ["annual_cash_flow"] and writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("annual_cf, why", [
    ([{k: v for k, v in r.items() if k != "preferredDividendsPaid"} for r in CRWV_CF_ANNUAL],
     "the preferred line drifted away"),
    ([{**r, "commonDividendsPaid": 0, "preferredDividendsPaid": 0} for r in CRWV_CF_ANNUAL],
     "both lines zeroed, the net kept"),
    ([{**r, "period": "Q4"} for r in CRWV_CF_ANNUAL], "no annual (FY) rows"),
])
async def test_unreadable_annual_rows_warn_by_year_but_stay_cacheable(annual_cf, why, caplog):
    """Rows came back but no year splits: possibly permanent vendor drift, so not a degraded
    reason (it would keep the report out of every shared cache for good) — but never silent."""
    caplog.set_level("WARNING")
    resp, _, degraded = await _wire(_FMP(annual_cf=annual_cf))._build_signal_of_confidence("CRWV")
    assert degraded == [], why
    assert "[soc-annual-split-unreadable]" in caplog.text, why
    assert "FY2024, FY2025" in caplog.text, why
    # the documented consequence: the record is used as FMP sent it (preferred included)
    assert _by_period(resp)["Q2 '25"].dividend_amount == 2.59


@pytest.mark.asyncio
async def test_no_statements_at_all_is_not_a_failed_annual_leg(caplog):
    """A fund-like answer — no quarterly AND no annual cash-flow rows — is the company's
    state, never `annual_cash_flow`, and no warning about a split."""
    class _NoStatements(_FMP):
        async def get_cash_flow_statement(self, ticker, period="annual", limit=10):
            return []

    caplog.set_level("WARNING")
    _resp, _, degraded = await _wire(_NoStatements())._build_signal_of_confidence("CRWV")
    assert "annual_cash_flow" not in degraded
    assert "[soc-annual-split-unreadable]" not in caplog.text
    assert "[soc-annual-cashflow-empty]" not in caplog.text


@pytest.mark.asyncio
async def test_an_empty_annual_answer_needs_nothing_without_a_paying_year(caplog):
    zero = [{"date": f"{y}-12-31", "fiscalYear": str(y), "dividendPerShare": 0}
            for y in (2022, 2023, 2024, 2025)]
    caplog.set_level("WARNING")
    _resp, _, degraded = await _wire(_FMP(ratios=zero, annual_cf=[]))._build_signal_of_confidence("CRWV")
    assert degraded == []
    assert "[soc-annual" not in caplog.text


def test_dividend_years_without_split():
    shares = S._common_dividend_shares(CRWV_CF_ANNUAL)
    assert S._dividend_years_without_split(CRWV_RATIOS, shares) == []
    assert S._dividend_years_without_split(CRWV_RATIOS, {}) == ["2024", "2025"]
    assert S._dividend_years_without_split(CRWV_RATIOS, {"2025": 0.0}) == ["2024"]
    assert S._dividend_years_without_split(
        [{"date": "2025-12-31", "dividendPerShare": "n/a"}, "junk", None,
         {"date": "garbage", "dividendPerShare": 1.0}], {}) == []
    assert S._dividend_years_without_split("not a list", {}) == []


@pytest.mark.asyncio
async def test_a_failed_annual_leg_costs_nothing_when_the_record_has_no_dividend_year():
    zero = [{"date": f"{y}-12-31", "fiscalYear": str(y), "dividendPerShare": 0}
            for y in (2022, 2023, 2024, 2025)]
    resp, _, degraded = await _wire(_FMP(ratios=zero, annual_cf_raises=True)) \
        ._build_signal_of_confidence("CRWV")
    assert degraded == []
    assert resp.dividend_info is None


@pytest.mark.asyncio
async def test_failed_ratios_and_annual_legs_report_only_the_ratios():
    resp, _, degraded = await _wire(_FMP(ratios_raises=True, annual_cf_raises=True)) \
        ._build_signal_of_confidence("CRWV")
    assert degraded == ["annual_ratios"]


@pytest.mark.asyncio
async def test_the_builder_asks_for_the_annual_statement_once():
    fmp = _FMP()
    await _wire(fmp)._build_signal_of_confidence("CRWV")
    annual = [c for c in fmp.calls if c[1] == "annual"]
    assert annual == [("cash-flow-statement", "annual", sos._ANNUAL_DIVIDEND_YEARS)]


# ── one-quarter share-count artifacts ────────────────────────────────────────


def _q(date, shares, ni=None, eps=None, label=None):
    rec = {"date": date, "weightedAverageShsOut": shares}
    if ni is not None:
        rec["netIncome"] = ni
    if eps is not None:
        rec["eps"] = eps
    return (date, label or date, rec)


_DATES = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]


def test_crwv_q4_and_q3_24_artifacts_are_refused():
    quarters = sorted(
        ((d, f"{p} '{fy[2:]}", rec) for d, p, fy, rec in
         ((r["date"], r["period"], r["fiscalYear"], r) for r in _crwv_income())),
        key=lambda q: q[0],
    )
    flagged = S._share_count_glitches(quarters)
    assert set(flagged) == {"2025-12-31", "2024-09-30"}
    assert "EPS implies 507.6M" in flagged["2025-12-31"].detail
    # both refusals are EPS-confirmed, so each carries an estimate: like with like against
    # the quarter before when that lands inside the neighbours' range (Q4 '25: 504.8M) …
    assert flagged["2025-12-31"].estimate == pytest.approx(_CRWV_Q4_25_LIKE)
    # … else the raw implied count the range already bounds (Q2 '24's own EPS is 4.8% off
    # its count, so like-for-like lands at 424M, outside 395.6-412.5M)
    assert flagged["2024-09-30"].estimate == pytest.approx(359_807_000 / 0.89)


def test_aapl_annual_copy_a_fraction_of_a_percent_off_is_kept():
    """AAPL FY2025's Q4 is also the annual figure, but 0.3% off its neighbours: real noise
    level, never refused."""
    q = [_q("2025-06-28", 14_902.9e6), _q("2025-09-27", 14_948.5e6, 27_466e6, 1.85),
         _q("2025-12-27", 14_748.2e6)]
    assert S._share_count_glitches(q) == {}


@pytest.mark.parametrize("series", [
    [100e6, 100e6, 120e6, 121e6, 122e6, 122e6],     # a real issuance: a STEP, it stays
    [945.8e6, 1_126.6e6, 1_158.5e6, 1_158.5e6, 1_389.7e6, 1_391.2e6],   # PLUG: monotonic
    [1_000e6, 980e6, 960e6, 940e6, 920e6, 900e6],   # a steady repurchaser
    [500e6, 500e6, 500e6, 500e6, 500e6, 500e6],     # flat
])
def test_real_share_count_moves_are_never_refused(series):
    assert S._share_count_glitches([_q(d, s) for d, s in zip(_DATES, series)]) == {}


def test_the_unconfirmed_bound_is_wider_than_the_eps_confirmed_one():
    # an 8% V: refused only when the row's own EPS puts the count back between neighbours
    base = [_q("2025-06-30", 500e6), None, _q("2025-12-31", 500e6)]
    v8 = _q("2025-09-30", 540e6)
    assert S._share_count_glitches([base[0], v8, base[2]]) == {}
    confirmed = _q("2025-09-30", 540e6, ni=-100e6, eps=-0.20)          # implies 500M
    assert set(S._share_count_glitches([base[0], confirmed, base[2]])) == {"2025-09-30"}
    # a 12% V needs no confirmation
    v12 = _q("2025-09-30", 560e6)
    assert set(S._share_count_glitches([base[0], v12, base[2]])) == {"2025-09-30"}


@pytest.mark.parametrize("ni, eps, why", [
    (-100e6, -0.04, "EPS under $0.05 rounds too coarsely to imply a count"),
    (-100e6, 0.20, "net income and EPS disagree in sign"),
    (0, -0.20, "zero net income implies nothing"),
    (-100e6, None, "no EPS"),
    (None, -0.20, "no net income"),
    (-100e6, float("nan"), "NaN EPS"),
    (-100e6, -0.15, "EPS implies 667M — outside the neighbours, so it does not confirm"),
])
def test_an_unusable_or_contradicting_eps_never_confirms(ni, eps, why):
    q = [_q("2025-06-30", 500e6), _q("2025-09-30", 540e6, ni=ni, eps=eps), _q("2025-12-31", 500e6)]
    assert S._share_count_glitches(q) == {}, why


@pytest.mark.parametrize("neighbour", [None, 0, -5e6, float("nan"), "n/a"])
def test_an_unreported_neighbour_means_no_verdict(neighbour):
    q = [_q("2025-06-30", neighbour), _q("2025-09-30", 700e6), _q("2025-12-31", 500e6)]
    assert S._share_count_glitches(q) == {}


def test_the_edges_need_their_own_eps_to_be_judged():
    """The oldest quarter is never judged, and the newest only as a fiscal-Q4 row on its own
    EPS's word — a bare jump at either edge (no EPS, not Q4) stays as reported."""
    q = [_q("2025-06-30", 900e6), _q("2025-09-30", 500e6), _q("2025-12-31", 500e6),
         _q("2026-03-31", 900e6)]
    assert S._share_count_glitches(q) == {}
    q4_no_eps = [_q("2025-06-30", 500e6), _q("2025-09-30", 500e6, label="Q3 '25"),
                 _q("2025-12-31", 900e6, label="Q4 '25")]
    assert S._share_count_glitches(q4_no_eps) == {}


# ── review 2026-10-07: a real move beside an annual copy is kept ─────────────

_STEP_DATES = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]
_STEP_LABELS = ["Q1 '25", "Q2 '25", "Q3 '25", "Q4 '25", "Q1 '26"]


def _series(rows):
    """[(shares, implied_or_None)] → quarters; EPS fixed at -1.00 so net income = -implied."""
    out = []
    for (shares, implied), d, lbl in zip(rows, _STEP_DATES, _STEP_LABELS):
        out.append(_q(d, shares, ni=None if implied is None else -implied,
                      eps=None if implied is None else -1.0, label=lbl))
    return out


@pytest.mark.parametrize("rows, why", [
    # an IPO / all-stock deal in Q3; FMP copies the annual average into Q4
    ([(300e6, 300e6), (300e6, 300e6), (450e6, 450e6), (376.25e6, 455e6), (465e6, 465e6)],
     "a real +50% step in Q3"),
    # a 20% tender completing at the start of Q3
    ([(1_000e6, 1_000e6), (1_000e6, 1_000e6), (800e6, 800e6), (900e6, 797e6), (795e6, 795e6)],
     "a real -20% tender in Q3"),
    # the real step row carries NO EPS: the confirmed copy beside it withdraws its refusal
    ([(300e6, 300e6), (300e6, 300e6), (450e6, None), (376.25e6, 455e6), (465e6, 465e6)],
     "a real step with no EPS of its own"),
])
def test_a_real_step_beside_an_annual_copy_is_kept_and_only_the_copy_refused(rows, why):
    flagged = S._share_count_glitches(_series(rows))
    assert set(flagged) == {"2025-12-31"}, why
    assert flagged["2025-12-31"].estimate == pytest.approx(rows[3][1])


def test_a_row_that_agrees_with_its_own_eps_is_never_refused():
    # a V by its neighbours, but the filing's own EPS backs the count
    q = [_q("2025-06-30", 500e6), _q("2025-09-30", 600e6, ni=-120e6, eps=-0.20),
         _q("2025-12-31", 500e6)]
    assert S._share_count_glitches(q) == {}


# ── review 2026-10-07: the newest fiscal-Q4 row (10-K → next 10-Q window) ─────

def _crwv_until(last_date):
    return [r for r in _crwv_income() if r["date"] <= last_date]


def _crwv_quarters(income):
    return sorted(((r["date"], f"{r['period']} '{r['fiscalYear'][2:]}", r) for r in income),
                  key=lambda q: q[0])


def test_the_newest_q4_annual_copy_is_refused_on_its_signature():
    """CRWV between its FY2025 10-K and its Q1 2026 10-Q: Q4 '25 (435M) IS the FY2025 annual
    average the annual statements imply; like with like against Q3 its own EPS puts the
    quarter at 504.8M, beside Q3's 497.9M."""
    assert _CRWV_ANNUAL == {"2025": pytest.approx(435e6), "2024": pytest.approx(404.407e6)}
    flagged = S._share_count_glitches(_crwv_quarters(_crwv_until("2025-12-31")), _CRWV_ANNUAL)
    assert "2025-12-31" in flagged
    assert flagged["2025-12-31"].estimate == pytest.approx(_CRWV_Q4_25_LIKE)
    assert "newest quarter" in flagged["2025-12-31"].detail
    # without the annual statements there is no signature, so the newest row stands
    assert "2025-12-31" not in S._share_count_glitches(_crwv_quarters(_crwv_until("2025-12-31")))


def _edge(prev, cur, *, prev_ni, prev_eps, ni, eps, label="Q4 '25", annual=None,
          newest_date="2025-12-31"):
    """Two reported quarters, then a newest row; ``annual`` = the FY annual average (default:
    the newest count itself — FMP's copy)."""
    q = [_q("2025-06-30", prev, ni=prev_ni, eps=prev_eps, label="Q2 '25"),
         _q("2025-09-30", prev, ni=prev_ni, eps=prev_eps, label="Q3 '25"),
         _q(newest_date, cur, ni=ni, eps=eps, label=label)]
    year = str(int(newest_date[:4]) - (1 if newest_date[5:7] == "01" else 0))
    return q, {year: cur if annual is None else annual}


def test_the_newest_copy_with_a_like_for_like_count_is_refused():
    q, annual = _edge(500e6, 435e6, prev_ni=-500e6, prev_eps=-1.00, ni=-507e6, eps=-1.00)
    flagged = S._share_count_glitches(q, annual)
    assert set(flagged) == {"2025-12-31"}
    assert flagged["2025-12-31"].estimate == pytest.approx(507e6)


@pytest.mark.parametrize("kwargs, why", [
    (dict(prev=14_902.9e6, cur=14_948.5e6, prev_ni=23_434e6, prev_eps=1.57, ni=27_466e6,
          eps=1.85), "AAPL: a 0.3% annual copy — like-for-like within 5%, noise"),
    (dict(prev=420e6, cur=399.4e6, prev_ni=-420e6, prev_eps=-1.00, ni=-399.4e6, eps=-1.00),
     "ADP-like: EPS derived from the copy agrees with it"),
    (dict(prev=500e6, cur=435e6, prev_ni=-500e6, prev_eps=-1.00, ni=-507e6, eps=-1.00,
          label="Q1 '26"), "not a fiscal-Q4 row"),
    (dict(prev=500e6, cur=435e6, prev_ni=-500e6, prev_eps=-1.00, ni=-507e6, eps=-1.00,
          annual=470e6), "not the annual average: a REAL count stands"),
    (dict(prev=500e6, cur=435e6, prev_ni=-500e6, prev_eps=-1.00, ni=-30.4e6, eps=-0.06),
     "newest |EPS| under 0.10: cent rounding alone is 8%"),
    (dict(prev=500e6, cur=435e6, prev_ni=-30e6, prev_eps=-0.06, ni=-507e6, eps=-1.00),
     "the quarter before has |EPS| under 0.10: no like to compare with"),
    (dict(prev=500e6, cur=435e6, prev_ni=-500e6, prev_eps=-1.00, ni=-600e6, eps=-1.00),
     "like-for-like puts it 20% from the quarter before: out of band, no estimate"),
    (dict(prev=500e6, cur=435e6, prev_ni=-500e6, prev_eps=-1.00, ni=0, eps=0.0),
     "EPS of zero"),
    (dict(prev=500e6, cur=435e6, prev_ni=-500e6, prev_eps=-1.00, ni=507e6, eps=-1.00),
     "net income and EPS disagree in sign"),
    (dict(prev=500e6, cur=435e6, prev_ni=-500e6, prev_eps=-1.00, ni=-507e6, eps=-1.00,
          newest_date="2026-03-31"), "two quarters after the one before: not consecutive"),
    # round-5 review 2026-10-08, R5-1: the count must have MOVED off the quarter before
    (dict(prev=100e6, cur=100e6, prev_ni=100e6, prev_eps=0.90, ni=65e6, eps=0.55),
     "a FLAT count IS its year's annual average; like for like (106.4M) is only noise"),
    (dict(prev=100e6, cur=95.1e6, prev_ni=-100e6, prev_eps=-1.00, ni=-100e6, eps=-1.00),
     "4.9% off the quarter before: inside the bound, the reported count stands"),
])
def test_a_newest_row_without_the_full_signature_is_kept(kwargs, why):
    q, annual = _edge(**kwargs)
    assert S._share_count_glitches(q, annual) == {}, why


def test_a_newest_copy_that_moved_past_the_bound_is_still_refused():
    """The must-refuse twin of the 4.9% case above: 5.1% off the quarter before, the count
    IS the annual average, like for like puts it back at 100M."""
    q, annual = _edge(100e6, 94.9e6, prev_ni=-100e6, prev_eps=-1.00, ni=-100e6, eps=-1.00)
    flagged = S._share_count_glitches(q, annual)
    assert set(flagged) == {"2025-12-31"}
    assert flagged["2025-12-31"].estimate == pytest.approx(100e6)


def test_a_newest_copy_beside_a_refused_quarter_is_not_judged():
    """Both rows bad: Q3 is itself a confirmed artifact, so there is no sound quarter to
    measure the newest copy against — it ships as sent (never null), Q3 is refused."""
    q = [_q("2025-03-31", 500e6, ni=-500e6, eps=-1.00, label="Q1 '25"),
         _q("2025-06-30", 500e6, ni=-500e6, eps=-1.00, label="Q2 '25"),
         _q("2025-09-30", 560e6, ni=-500e6, eps=-1.00, label="Q3 '25"),
         _q("2025-12-31", 435e6, ni=-507e6, eps=-1.00, label="Q4 '25")]
    flagged = S._share_count_glitches(q, {"2025": 435e6})
    assert set(flagged) == {"2025-09-30"}


def test_rows_in_any_order_get_the_same_verdict():
    ordered = _crwv_quarters(_crwv_until("2025-12-31"))
    shuffled = list(reversed(ordered))
    shuffled[2], shuffled[5] = shuffled[5], shuffled[2]
    assert S._share_count_glitches(shuffled, _CRWV_ANNUAL).keys() == \
        S._share_count_glitches(ordered, _CRWV_ANNUAL).keys()


# ── review 2026-10-07: the newest DISPLAYED quarter never ships null (build 10) ─

def _cf_until(last_date):
    return [r for r in _crwv_cf_quarterly() if r["date"] <= last_date]


def test_10k_window_ships_the_like_for_like_count_at_the_newest_point():
    pts, diag = _svc()._build_quarters(_cf_until("2025-12-31"), _crwv_until("2025-12-31"),
                                       _CRWV_CURRENT_CAP, {}, "CRWV",
                                       annual_share_counts=_CRWV_ANNUAL)
    assert pts[-1].period == "Q4 '25"
    assert pts[-1].shares_outstanding == round(_CRWV_Q4_25_LIKE / 1e6, 2)   # 504.84, not 435
    # Round 5 (R5-2): that figure is the chart's, never a measurement — the summary runs from
    # the oldest to the newest REPORTED count (Q3 '25, 497.89M).
    assert diag.estimated_share_periods == ["Q4 '25"]
    summary = _svc()._build_summary(
        pts, _CRWV_CURRENT_CAP, estimated_share_periods=set(diag.estimated_share_periods)
    )
    reported = [p.shares_outstanding for p in pts[:-1] if p.shares_outstanding]
    assert reported[-1] == 497.89
    assert summary.share_count_change == round((497.89 - reported[0]) / reported[0] * 100, 2)
    assert summary.share_count_change > 20, "the copy understated the dilution as +7.75%"


def test_a_refused_quarter_left_newest_by_the_trim_is_never_null():
    """Q1 '26's income landed, its cash-flow row has not: Q1 '26 is trimmed and the refused
    Q4 '25 becomes the newest displayed point. Build 1.0 (10) would print a bold '0.00M'
    for a null there."""
    pts, diag = _svc()._build_quarters(_cf_until("2025-12-31"), _crwv_until("2026-03-31"),
                                       _CRWV_CURRENT_CAP, {}, "CRWV")
    assert diag.missing_cash_flow_recent is True
    assert pts[-1].period == "Q4 '25"
    assert pts[-1].shares_outstanding == round(_CRWV_Q4_25_LIKE / 1e6, 2)
    # …and once Q1 '26's row lands, the same quarter is interior again and ships null
    pts, _ = _svc()._build_quarters(_cf_until("2026-03-31"), _crwv_until("2026-03-31"),
                                    _CRWV_CURRENT_CAP, {}, "CRWV")
    assert {p.period: p.shares_outstanding for p in pts}["Q4 '25"] is None
    assert pts[-1].shares_outstanding == 527.0


def test_an_unconfirmed_refusal_left_newest_ships_the_raw_count():
    """A 12% V with no EPS at all, then a trimmed newest quarter: no better figure exists,
    so the raw count ships — never null."""
    income = [
        {"date": d, "period": p, "fiscalYear": fy, "weightedAverageShsOut": sh}
        for d, p, fy, sh in (
            ("2025-03-31", "Q1", "2025", 500e6), ("2025-06-30", "Q2", "2025", 500e6),
            ("2025-09-30", "Q3", "2025", 560e6), ("2025-12-31", "Q4", "2025", 500e6),
        )
    ]
    cf = [{"date": d, "commonStockRepurchased": 0}
          for d in ("2025-03-31", "2025-06-30", "2025-09-30")]
    pts, diag = _svc()._build_quarters(cf, income, 1e10, {}, "X")
    assert diag.missing_cash_flow_recent is True and pts[-1].period == "Q3 '25"
    assert pts[-1].shares_outstanding == 560.0


@pytest.mark.parametrize("shape, a, b", [
    ("crwv", "2025-12-31", "2025-12-31"), ("crwv", "2026-03-31", "2025-12-31"),
    ("crwv", "2026-03-31", "2026-03-31"), ("crwv", "2026-06-30", "2026-03-31"),
    ("crwv", "2026-06-30", "2026-06-30"), ("crwv", "2024-12-31", "2024-12-31"),
    # final review 2026-10-08, F2: CD's newest row came from FMP with weightedAverageShsOut 0
    ("cd", 0, None), ("cd", None, None), ("cd", "missing", None), ("cd", -5e6, None),
])
def test_the_newest_displayed_count_is_never_null_when_reported(shape, a, b):
    """'Reported' = FMP sent a count, OR the quarter's own EPS gives one beside the quarter
    before. Build 1.0 (10) prints any null newest count as a bold '0.00M'."""
    if shape == "crwv":
        pts, _ = _svc()._build_quarters(_cf_until(b), _crwv_until(a), _CRWV_CURRENT_CAP, {},
                                        "CRWV", annual_share_counts=_CRWV_ANNUAL)
    else:
        income = _cd_income(None, key=False) if a == "missing" else _cd_income(a)
        pts, _ = _svc()._build_quarters(_cd_cf(), income, 120e9, {}, "CD")
    assert pts and pts[-1].shares_outstanding is not None and pts[-1].shares_outstanding > 0


def test_a_gap_in_the_history_suspends_the_check():
    # 2025-06-30 → 2026-03-31 is three quarters: the move may have happened in between
    q = [_q("2025-06-30", 500e6), _q("2026-03-31", 700e6), _q("2026-06-30", 500e6)]
    assert S._share_count_glitches(q) == {}


def test_duplicate_and_unsorted_rows_still_refuse_the_artifact_once():
    """FMP serves newest-first and occasionally repeats a row (a restatement): the check
    runs on the de-duplicated, date-sorted quarters, so the Q4 '25 copy is refused once and
    every real count survives."""
    income = _crwv_income()
    income = income + [dict(income[2])] + [dict(income[5])]       # Q4 '25 and Q1 '25 twice
    income = list(reversed(income))                                 # oldest-first this time
    cf = _crwv_cf_quarterly()
    pts, _ = _svc()._build_quarters(cf, income, _CRWV_CURRENT_CAP, {}, "CRWV")
    by = {p.period: p for p in pts}
    assert len(pts) == len(by) == 8, "one point per quarter"
    assert by["Q4 '25"].shares_outstanding is None and by["Q3 '24"].shares_outstanding is None
    assert [by[q].shares_outstanding for q in ("Q4 '24", "Q1 '25", "Q2 '25", "Q3 '25", "Q1 '26",
                                                "Q2 '26")] == [404.41, 404.41, 486.59, 497.89,
                                                               527.0, 551.0]


def test_empty_and_tiny_histories():
    assert S._share_count_glitches([]) == {}
    assert S._share_count_glitches([_q("2025-06-30", 500e6)]) == {}
    assert S._share_count_glitches([_q("2025-06-30", 500e6), _q("2025-09-30", 900e6)]) == {}


@pytest.mark.asyncio
async def test_refused_counts_ship_null_and_the_change_is_measured_around_them():
    resp, _, _ = await _wire(_FMP())._build_signal_of_confidence("CRWV")
    pts = _by_period(resp)
    assert pts["Q4 '25"].shares_outstanding is None
    assert pts["Q3 '24"].shares_outstanding is None
    assert pts["Q3 '25"].shares_outstanding == 497.89 and pts["Q1 '26"].shares_outstanding == 527.0
    # oldest REPORTED (Q4 '24, 404.41M) → newest (Q2 '26, 551M): +36.25%, not +26.51% off
    # the refused 435.53M
    assert resp.summary.share_count_change_known is True
    assert resp.summary.share_count_change == round((551.0 - 404.41) / 404.41 * 100, 2)
    assert math.isfinite(resp.summary.share_count_change)


# ── market_cap: the Capital view's scale ─────────────────────────────────────


@pytest.mark.asyncio
async def test_each_point_carries_the_cap_its_yields_were_divided_by():
    resp, _, _ = await _wire(_FMP())._build_signal_of_confidence("CRWV")
    pts = _by_period(resp)
    assert pts["Q2 '26"].market_cap == 48_000.0
    assert pts["Q2 '25"].market_cap == 80_000.0
    # a pre-IPO quarter has no period cap: its yields use the CURRENT cap, and so does this
    assert pts["Q4 '24"].market_cap == round(_CRWV_CURRENT_CAP / 1e6, 2)
    for p in resp.data_points:
        assert p.market_cap is None or (math.isfinite(p.market_cap) and p.market_cap > 0)


def test_no_usable_cap_ships_none_never_zero():
    pts, _ = _svc()._build_quarters(
        [{"date": "2026-06-30", "commonStockRepurchased": -1e6}],
        [{"date": "2026-06-30", "period": "Q2", "fiscalYear": "2026",
          "weightedAverageShsOut": 1e8}],
        None, {}, "X",
    )
    assert pts[0].market_cap is None
    assert pts[0].buyback_yield == 0.0


def test_payload_version_moved_for_the_common_record_and_the_new_field():
    assert sos._PAYLOAD_VERSION >= 11


# ── final review 2026-10-08, F1: the newest fiscal-Q4 check must not trade a REAL count
#    for an EPS-implied one. |net income / EPS| is biased — basic EPS is struck after
#    preferred dividends (a profitable preferred issuer reads HIGH, a loss-making one
#    LOW) and rounded to cents — so the old edge rule replaced real 5-12% moves: a tender
#    read "Diluting", real dilution vanished. ──────────────────────────────────────────

_FY_ROWS = [("2024-12-31", "Q4", "2024"), ("2025-03-31", "Q1", "2025"),
            ("2025-06-30", "Q2", "2025"), ("2025-09-30", "Q3", "2025"),
            ("2025-12-31", "Q4", "2025")]


class _SeriesFMP(_FMP):
    """`_FMP` with every statement injectable — a synthetic filer, still hermetic."""

    def __init__(self, *, income, cf_q, cf_a, ratios, cap):
        super().__init__(annual_cf=cf_a, ratios=ratios,
                         profile=[{"lastDividend": 0, "marketCap": cap, "price": 10.0}])
        self._income, self._cf_q, self._cap = income, cf_q, cap

    async def get_cash_flow_statement(self, ticker, period="annual", limit=10):
        if period == "annual" and isinstance(self.annual_cf, Exception):
            raise self.annual_cf
        return self.annual_cf if period == "annual" else self._cf_q

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self._income

    async def get_historical_market_cap(self, ticker, from_date=None, to_date=None, limit=2000):
        return []

    async def get_stock_price_quote(self, ticker):
        return {"marketCap": self._cap, "price": 10.0}


def _q4_window(counts_m, net_income, eps, *, buyback_q4=0.0, annual_avg_m=None, cap=10e9):
    """Five quarters ending at fiscal Q4 '25 — the 10-K → next 10-Q window — with the FY2025
    annual statements FMP serves beside them. `annual_avg_m` is the annual weighted average
    (default: the mean of the four FY2025 quarters, what a filer reports)."""
    income = [{"date": d, "period": p, "fiscalYear": fy, "weightedAverageShsOut": c * 1e6,
               "netIncome": n, "eps": e}
              for (d, p, fy), c, n, e in zip(_FY_ROWS, counts_m, net_income, eps)]
    cf_q = [{"date": d, "commonDividendsPaid": 0, "netDividendsPaid": 0,
             "preferredDividendsPaid": 0,
             "commonStockRepurchased": -buyback_q4 if d == "2025-12-31" else 0}
            for d, _p, _fy in _FY_ROWS]
    avg = (annual_avg_m if annual_avg_m is not None else sum(counts_m[1:]) / 4) * 1e6
    ni_fy, ocf_fy = sum(net_income[1:]), 1e9
    cf_a = [{"date": "2025-12-31", "fiscalYear": "2025", "period": "FY",
             "netIncome": ni_fy, "operatingCashFlow": ocf_fy, "commonDividendsPaid": 0,
             "preferredDividendsPaid": 0, "netDividendsPaid": 0}]
    ratios = [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 0,
               "netIncomePerShare": ni_fy / avg, "operatingCashFlowPerShare": ocf_fy / avg}]
    return _SeriesFMP(income=income, cf_q=cf_q, cf_a=cf_a, ratios=ratios, cap=cap)


# (counts in M, net income, EPS as FILED: after preferred dividends, rounded to cents)
_F1_REAL_MOVES = {
    # (a) profitable, preferred = 10% of NI, a real -7% Q4 tender ($700M on a $10B cap)
    "a": ([100, 100, 100, 100, 93], [200e6] * 5, [1.80, 1.80, 1.80, 1.80, 1.94], 700e6),
    # (b) loss-making, preferred = 10% of |NI|, a real +10% Q4 issuance
    "b": ([100, 100, 100, 100, 110], [-200e6] * 5, [-2.20, -2.20, -2.20, -2.20, -2.00], 0.0),
    # (c) no preferred, EPS 0.0566 filed as 0.06, a real +6% Q4 issuance
    "c": ([100, 100, 100, 100, 106], [6e6] * 5, [0.06, 0.06, 0.06, 0.06, 0.06], 0.0),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case, expected_newest, expected_change, expected_status", [
    ("a", 93.0, -7.0, "Very High"),
    ("b", 110.0, 10.0, "Diluting"),
    ("c", 106.0, 6.0, "Diluting"),
])
async def test_a_real_newest_q4_move_survives_a_biased_eps(case, expected_newest,
                                                           expected_change, expected_status):
    counts, ni, eps, buyback = _F1_REAL_MOVES[case]
    resp, _, degraded = await _wire(_q4_window(counts, ni, eps, buyback_q4=buyback)) \
        ._build_signal_of_confidence("PREF")
    assert degraded == []
    assert resp.data_points[-1].period == "Q4 '25"
    assert resp.data_points[-1].shares_outstanding == expected_newest, \
        "a real share count was replaced by a biased EPS-implied one"
    assert resp.summary.share_count_change == expected_change
    assert resp.summary.buyback_status == expected_status


@pytest.mark.asyncio
async def test_a_preferred_issuers_annual_copy_is_refused_with_the_bias_cancelled():
    """The must-refuse twin of (a): the counts moved inside the year (60 → 100), FMP copied
    the 90M annual average into Q4, and the row's own EPS is 11% high from preferred
    dividends. Like with like (its implied count over Q3's implied count) puts the quarter
    back at 100M; the raw implied count (111M) is no count at all."""
    counts = [60, 60, 100, 100, 90]                      # Q4 '25 = FY2025 average (60+100+100+100)/4
    ni = [200e6] * 5
    eps = [3.00, 3.00, 1.80, 1.80, 1.80]                # filed on the TRUE counts, after preferred
    resp, _, degraded = await _wire(
        _q4_window(counts, ni, eps, annual_avg_m=90)
    )._build_signal_of_confidence("PREF")
    assert degraded == []
    assert resp.data_points[-1].shares_outstanding == 100.0


@pytest.mark.asyncio
async def test_crwv_10k_window_still_refuses_the_q4_copy_like_with_like():
    """CRWV between its FY2025 10-K and its Q1 2026 10-Q: Q4 '25 carries the 435M annual copy;
    Q3's own EPS implies 500.6M and Q4's 507.6M, so like with like the quarter is
    497.9 x 507.6 / 500.6 = 504.8M — never the 435M copy and never null."""
    cut_income, cut_cf = _crwv_until("2025-12-31"), _cf_until("2025-12-31")

    class _Cut(_FMP):
        async def get_income_statement(self, ticker, period="quarter", limit=20):
            return cut_income

        async def get_cash_flow_statement(self, ticker, period="annual", limit=10):
            return self.annual_cf if period == "annual" else cut_cf

    resp, _, _degraded = await _wire(_Cut())._build_signal_of_confidence("CRWV")
    newest = resp.data_points[-1]
    assert newest.period == "Q4 '25"
    q3_implied, q4_implied = 110_124_000 / 0.22, 451_726_000 / 0.89
    assert newest.shares_outstanding == round(q4_implied * 497_886_000 / q3_implied / 1e6, 2)
    # …on the chart only (round 5, R5-2): the summary measures oldest → Q3 '25, both reported
    oldest = next(p.shares_outstanding for p in resp.data_points if p.shares_outstanding)
    assert resp.summary.share_count_change == round((497.89 - oldest) / oldest * 100, 2)
    assert resp.summary.share_count_change > 20, "the copy understated the dilution as +7.75%"


# ── final review 2026-10-08, F2: a newest count FMP sent as 0 / null / missing ships the
#    count its own EPS implies (bias-cancelled against the quarter before), never null —
#    public build 1.0 (10) draws a null newest count as a bold "0.00M". ───────────────

def _cd_income(newest_count, *, newest_ni=121e6, newest_eps=0.10, key=True):
    rows = []
    for (d, p, fy), c in zip(_FY_ROWS[:-1], (1_220, 1_218, 1_215, 1_213)):
        rows.append({"date": d, "period": p, "fiscalYear": fy, "weightedAverageShsOut": c * 1e6,
                     "netIncome": c * 1e6 * 0.10, "eps": 0.10})
    d, p, fy = _FY_ROWS[-1]
    newest = {"date": d, "period": p, "fiscalYear": fy, "netIncome": newest_ni, "eps": newest_eps}
    if key:
        newest["weightedAverageShsOut"] = newest_count
    rows.append(newest)
    return rows


def _cd_cf():
    return [{"date": d, "commonStockRepurchased": 0} for d, _p, _fy in _FY_ROWS]


@pytest.mark.parametrize("raw", [0, None, "missing", -5e6])
def test_a_zero_or_missing_newest_count_ships_its_eps_implied_count(raw):
    income = _cd_income(None, key=False) if raw == "missing" else _cd_income(raw)
    pts, diag = _svc()._build_quarters(_cd_cf(), income, 120e9, {}, "CD")
    assert pts[-1].shares_outstanding == 1210.0, "build 1.0 (10) prints a null newest count as 0.00M"
    # …for the chart only (round 5, R5-2): the summary measures the REPORTED counts
    assert diag.estimated_share_periods == ["Q4 '25"]
    summary = _svc()._build_summary(
        pts, 120e9, estimated_share_periods=set(diag.estimated_share_periods)
    )
    assert summary.share_count_change == round((1213.0 - 1220.0) / 1220.0 * 100, 2)


@pytest.mark.parametrize("ni, eps, why", [
    (121e6, 0.0, "EPS of zero"),
    (121e6, -0.10, "net income and EPS disagree in sign"),
    (121e6, 0.04, "|EPS| under 0.10: cent rounding alone is up to 12.5%"),
    (None, 0.10, "no net income"),
    (121e6, None, "no EPS"),
    (121e6, float("nan"), "NaN EPS"),
    (200e6, 0.10, "EPS puts it at 2.0B beside 1.21B: out of band"),
])
def test_a_zero_newest_count_without_a_usable_eps_stays_null(ni, eps, why):
    pts, _ = _svc()._build_quarters(_cd_cf(), _cd_income(0, newest_ni=ni, newest_eps=eps),
                                    120e9, {}, "CD")
    assert pts[-1].shares_outstanding is None, why


def test_a_zero_newest_count_with_no_recent_reported_quarter_stays_null():
    """The quarter before is missing too, and the last reported one is more than two
    quarters back: nothing sound to measure against — never a guess."""
    income = _cd_income(0)
    for row in income[1:-1]:
        row["weightedAverageShsOut"] = 0          # Q1-Q3 '25 unreported as well
    pts, _ = _svc()._build_quarters(_cd_cf(), income, 120e9, {}, "CD")
    assert pts[-1].shares_outstanding is None


def test_a_single_unreported_quarter_stays_null():
    income = _cd_income(0)[-1:]
    pts, _ = _svc()._build_quarters(_cd_cf()[-1:], income, 120e9, {}, "CD")
    assert len(pts) == 1 and pts[0].shares_outstanding is None


def test_unsorted_rows_give_the_same_newest_estimate():
    income = list(reversed(_cd_income(0)))
    pts, _ = _svc()._build_quarters(list(reversed(_cd_cf())), income, 120e9, {}, "CD")
    assert pts[-1].period == "Q4 '25" and pts[-1].shares_outstanding == 1210.0


def test_an_anchor_without_usable_eps_falls_back_to_the_raw_count_in_band():
    income = _cd_income(0)
    for row in income[:-1]:
        row["eps"] = 0.04                         # the quarters before: |EPS| under 0.10
    pts, _ = _svc()._build_quarters(_cd_cf(), income, 120e9, {}, "CD")
    assert pts[-1].shares_outstanding == 1210.0   # raw 121M / 0.10, 0.2% from 1,213M
    income[-1]["netIncome"] = 150e6               # raw 1.5B: 24% from 1,213M → no figure
    pts, _ = _svc()._build_quarters(_cd_cf(), income, 120e9, {}, "CD")
    assert pts[-1].shares_outstanding is None


def test_a_refused_quarter_is_never_the_anchor():
    quarters = _crwv_quarters(_cd_income(0))
    glitch = sos._ShareGlitch(detail="test", estimate=None)
    # Q3 '25 refused → Q2 '25 (1,215M) anchors instead: 1,210 x 1,215 / 1,215
    got = S._unreported_newest_count(quarters, "2025-12-31", {"2025-09-30": glitch})
    assert got == pytest.approx(1_210e6)
    # every quarter before refused → no anchor
    refused = {q[0]: glitch for q in quarters[:-1]}
    assert S._unreported_newest_count(quarters, "2025-12-31", refused) is None
    assert S._unreported_newest_count(quarters, "2099-12-31", {}) is None   # not in the list


# ── the like-for-like count and the annual weighted averages ──────────────────

@pytest.mark.parametrize("bias", [1.0, 1.11, 0.909, 1.25])
def test_like_for_like_cancels_a_stable_bias(bias):
    """A preferred issuer's |NI / EPS| runs `bias` x its count in every quarter; like with
    like gives the count back."""
    anchor = {"netIncome": 200e6, "eps": 200e6 / (100e6 * bias)}
    rec = {"netIncome": 200e6, "eps": 200e6 / (93e6 * bias)}
    assert S._like_for_like_count(rec, anchor, 100e6) == pytest.approx(93e6)


@pytest.mark.parametrize("rec, anchor, count, why", [
    ({"netIncome": 6e6, "eps": 0.06}, {"netIncome": 1e8, "eps": 1.0}, 1e8, "|EPS| < 0.10"),
    ({"netIncome": 1e8, "eps": 1.0}, {"netIncome": 6e6, "eps": 0.06}, 1e8, "anchor |EPS| < 0.10"),
    ({"netIncome": 1.2e8, "eps": 1.0}, {"netIncome": 1e8, "eps": 1.0}, 1e8, "20% out of band"),
    ({"netIncome": 1e8, "eps": 1.0}, {"netIncome": 1e8, "eps": 1.0}, None, "no anchor count"),
    ({"netIncome": 1e8, "eps": 1.0}, {"netIncome": 1e8, "eps": 1.0}, 0, "zero anchor count"),
    ({"netIncome": 1e8, "eps": 1.0}, {"netIncome": 1e8, "eps": 1.0}, float("nan"), "NaN anchor"),
    ({"netIncome": 1e8, "eps": 1.0}, "junk", 1e8, "anchor not a row"),
    (None, {"netIncome": 1e8, "eps": 1.0}, 1e8, "row not a row"),
])
def test_like_for_like_refuses_what_it_cannot_vouch_for(rec, anchor, count, why):
    assert S._like_for_like_count(rec, anchor, count) is None, why


def test_annual_share_counts_from_the_statements_already_fetched():
    assert S._annual_share_counts(CRWV_RATIOS, CRWV_CF_ANNUAL) == {
        "2025": pytest.approx(435e6), "2024": pytest.approx(404.407e6)}
    ratio = {"date": "2025-12-31", "fiscalYear": "2025",
             "netIncomePerShare": 2.0, "operatingCashFlowPerShare": 3.0}
    cash = {"date": "2025-12-31", "fiscalYear": "2025", "period": "FY",
            "netIncome": 200e6, "operatingCashFlow": 300e6}
    assert S._annual_share_counts([ratio], [cash]) == {"2025": pytest.approx(100e6)}
    # one usable pair is enough
    assert S._annual_share_counts([{**ratio, "netIncomePerShare": None}], [cash]) == \
        {"2025": pytest.approx(100e6)}
    # pairs that disagree (> 0.5%) → no figure, never a guess
    assert S._annual_share_counts([{**ratio, "netIncomePerShare": 2.2}], [cash]) == {}
    # sign mismatch, zero per-share, a quarterly row, no cash-flow row → no figure
    assert S._annual_share_counts([{**ratio, "netIncomePerShare": -2.0,
                                    "operatingCashFlowPerShare": None}], [cash]) == {}
    assert S._annual_share_counts([{**ratio, "netIncomePerShare": 0,
                                    "operatingCashFlowPerShare": 0}], [cash]) == {}
    assert S._annual_share_counts([ratio], [{**cash, "period": "Q4"}]) == {}
    assert S._annual_share_counts([ratio], []) == {}
    assert S._annual_share_counts("junk", None) == {}


def test_payload_version_moved_for_the_final_review():
    """v13: the newest-Q4 values and the 0/null newest count changed (the shape did not)."""
    assert sos._PAYLOAD_VERSION >= 13


@pytest.mark.asyncio
async def test_a_real_q4_move_with_a_shifting_preferred_share_is_kept():
    """Why the annual-copy SIGNATURE is required, not like-for-like alone: preferred
    dividends are a fixed $10M while net income halves in Q4, so the bias moves from 1.11x
    to 1.25x and like-for-like puts a real -10% tender at 102M. The count is not the annual
    average (97.5M), so it is a real count and stands."""
    counts = [100, 100, 100, 100, 90]
    ni = [100e6, 100e6, 100e6, 100e6, 50e6]
    eps = [0.90, 0.90, 0.90, 0.90, 0.44]                # (NI - 10M) / count, to cents
    like = S._like_for_like_count({"netIncome": 50e6, "eps": 0.44},
                                  {"netIncome": 100e6, "eps": 0.90}, 100e6)
    assert like == pytest.approx(102.27e6, rel=1e-3), "the trap this test exists for"
    resp, _, degraded = await _wire(_q4_window(counts, ni, eps))._build_signal_of_confidence("PREF")
    assert degraded == []
    assert resp.data_points[-1].shares_outstanding == 90.0
    assert resp.summary.share_count_change == -10.0


# ── round-5 review 2026-10-08, R5-1: the newest fiscal-Q4 rule needs the reported count to
#    have MOVED off the quarter before. A flat or slowly moving count IS its year's annual
#    average, so the signature alone matched REAL counts, and a like-for-like figure 5-10%
#    off (cent rounding on two rows, a preferred share that shifts with net income) replaced
#    them: a flat 3% repurchaser read "Diluting". ──────────────────────────────────────

_R5_FLAT_AND_SLOW = {
    # (counts in M, net income, EPS as FILED, the FY2025 annual average or None = the mean)
    # flat 100M, preferred $10M a quarter, Q4 net income 100M → 65M: EPS 0.90 → 0.55, like
    # for like 106.4M
    "flat, preferred, Q4 NI 65M": ([100] * 5, [100e6] * 4 + [65e6], [0.90] * 4 + [0.55], None),
    # …Q4 net income 60M: EPS 0.50, like for like 108.0M
    "flat, preferred, Q4 NI 60M": ([100] * 5, [100e6] * 4 + [60e6], [0.90] * 4 + [0.50], None),
    # flat 100M, no preferred, true EPS 0.115 / 0.1049 filed as 0.12 / 0.10: 109.5M
    "flat, EPS 0.12 -> 0.10": ([100] * 5, [11.5e6] * 4 + [10.49e6], [0.12] * 4 + [0.10], None),
    # a slow REAL mover (-0.1M a quarter): Q4's 100.1M is within 0.2% of the year's 100.25M
    "slow real count, EPS 0.12 -> 0.10": (
        [100.5, 100.4, 100.3, 100.2, 100.1],
        [0.115 * c * 1e6 for c in (100.5, 100.4, 100.3, 100.2)] + [0.1049 * 100.1e6],
        [0.12] * 4 + [0.10], None),
    # a steady repurchaser (-1% a quarter: 103, 102, 101, 100) whose Q4 row carries FMP's
    # copy of the 101.5M annual average — 1.5% from the truth, and the reported figure;
    # like for like (109.5M) is 7.8% from it and 9.5% from the truth
    "slow repurchaser, FMP's copy, EPS 0.12 -> 0.10": (
        [104, 103, 102, 101, 101.5],
        [0.115 * c * 1e6 for c in (104, 103, 102, 101)] + [0.1049 * 100e6],
        [0.12] * 4 + [0.10], 101.5),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(_R5_FLAT_AND_SLOW))
async def test_a_flat_or_slow_newest_q4_count_is_never_replaced(case):
    counts, ni, eps, annual_avg = _R5_FLAT_AND_SLOW[case]
    resp, _, degraded = await _wire(
        _q4_window(counts, ni, eps, buyback_q4=300e6, annual_avg_m=annual_avg)
    )._build_signal_of_confidence("FLAT")
    assert degraded == [], case
    oldest, newest = resp.data_points[0], resp.data_points[-1]
    assert newest.period == "Q4 '25"
    assert newest.shares_outstanding == counts[-1], \
        f"{case}: a reported count was replaced by an EPS-implied figure"
    assert resp.summary.share_count_change == round(
        (counts[-1] - oldest.shares_outstanding) / oldest.shares_outstanding * 100, 2
    ), case
    assert resp.summary.buyback_status == "High", f"{case}: a 3% repurchaser read as diluting"


# ── round-5 review 2026-10-08, R5-2: an EPS estimate on the newest point is DISPLAY ONLY.
#    It exists so build 1.0 (10) never prints a bold "0.00M"; its error (|EPS| ≥ 0.10 and a
#    10% band) routinely exceeds the 2% "Diluting" line, so the summary's share-count change
#    and buyback verdict measure REPORTED counts only — as build 10's own summary did. ──

@pytest.mark.asyncio
async def test_an_estimated_newest_count_never_moves_the_summary():
    """Flat 1,213M with true EPS 0.115 filed as 0.12; FMP sends the newest Q4 '25 count as 0
    with true EPS 0.1049 filed as 0.10. Like for like that quarter reads 1,327.76M (+9.5%):
    good enough for the chart's label, never a measurement."""
    resp, _, degraded = await _wire(_q4_window(
        [1213] * 4 + [0], [0.115 * 1213e6] * 4 + [0.1049 * 1213e6], [0.12] * 4 + [0.10],
        buyback_q4=2.4e9, annual_avg_m=1213, cap=120e9,
    ))._build_signal_of_confidence("FLAT")
    assert degraded == []
    assert resp.data_points[-1].shares_outstanding == 1327.76, "control: the chart's figure"
    assert resp.summary.share_count_change_known is True
    assert resp.summary.share_count_change == 0.0
    assert resp.summary.buyback_status == "High"


def _r5_point(period, shares):
    from app.schemas.signal_of_confidence import SignalOfConfidenceDataPointSchema

    return SignalOfConfidenceDataPointSchema(period=period, shares_outstanding=shares)


@pytest.mark.parametrize("estimated, change, known", [
    (None, 30.0, True),                    # nothing estimated: oldest → newest, as before
    (set(), 30.0, True),
    ({"Q3 '25"}, 10.0, True),              # the estimated newest is skipped
    ({"Q2 '25", "Q3 '25"}, 0.0, False),    # one reported count left: the change is unknown
    ({"Q9 '99"}, 30.0, True),              # a label that is not on the series changes nothing
])
def test_the_summary_measures_reported_counts_only(estimated, change, known):
    pts = [_r5_point("Q1 '25", 100.0), _r5_point("Q2 '25", 110.0), _r5_point("Q3 '25", 130.0)]
    summary = _svc()._build_summary(pts, 1e10, estimated_share_periods=estimated)
    assert summary.share_count_change == change
    assert summary.share_count_change_known is known
    if not known:
        assert summary.buyback_status == "Low", "an unknown change never reads as dilution"


# ── round-5 review 2026-10-08, R5-3: the annual cash-flow statement now also recognises
#    FMP's annual copy in the newest fiscal-Q4 row. A failed leg there — or an empty answer
#    from an operating filer — silently dropped that check for a NON-payer and persisted the
#    build as healthy ("nothing depends on it"). ──────────────────────────────────────

_NON_PAYER_RATIOS = [{**r, "dividendPerShare": 0} for r in CRWV_RATIOS]


class _TenK(_FMP):
    """CRWV between its FY2025 10-K and its Q1 2026 10-Q (the newest quarter is fiscal Q4
    '25, FMP's 435M annual copy) as a NON-payer: no dividend split depends on the annual
    statement, only the copy's signature does."""

    def __init__(self, *, income_until="2025-12-31", cf_until="2025-12-31", **kw):
        super().__init__(ratios=kw.pop("ratios", _NON_PAYER_RATIOS), **kw)
        self._income, self._cf = _crwv_until(income_until), _cf_until(cf_until)

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self._income

    async def get_cash_flow_statement(self, ticker, period="annual", limit=10):
        if period != "annual":
            return self._cf
        return await super().get_cash_flow_statement(ticker, period, limit)


async def _persisted(svc, monkeypatch):
    """(response, Supabase writes) through the real getter, cache seams stubbed."""
    writes = []
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda t: None)
    monkeypatch.setattr(svc, "_upsert_supabase_cache_safe", lambda *a, **k: writes.append(a))
    sos._cache.clear()
    sos._inflight.clear()
    out = await svc.get_signal_of_confidence("CRWV")
    for _ in range(50):
        if writes:
            break
        await asyncio.sleep(0.01)
    return out, writes


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, reasons, named", [
    (dict(annual_cf_raises=True), ["annual_cash_flow"], "the annual cash-flow statement failed"),
    (dict(annual_cf={"Error Message": "Limit Reach"}), ["annual_cash_flow"],
     "the annual cash-flow statement failed"),
    # beside a quarterly statement that answered rows
    (dict(annual_cf=[]), ["annual_cash_flow"], "the annual cash-flow statement failed"),
    # round-6 review 2026-10-08, R6-2: the annual average's twin input, the ratios
    (dict(ratios_raises=True), ["annual_ratios"], "the annual ratios failed"),
    (dict(ratios={"Error Message": "Limit Reach"}), ["annual_ratios"], "the annual ratios failed"),
    (dict(ratios=[]), ["annual_ratios"], "the annual ratios failed"),   # statements answered rows
    (dict(annual_cf_raises=True, ratios=[]), ["annual_cash_flow", "annual_ratios"],
     "the annual cash-flow statement and the annual ratios failed"),
])
async def test_a_failed_annual_leg_in_the_10k_window_is_never_persisted(failure, reasons, named,
                                                                        caplog, monkeypatch):
    caplog.set_level("WARNING")
    out, writes = await _persisted(_wire(_TenK()), monkeypatch)
    assert out.degraded == [] and writes, "control: a healthy build reaches the 24h tier"
    assert out.data_points[-1].shares_outstanding == round(_CRWV_Q4_25_LIKE / 1e6, 2)

    caplog.clear()
    out, writes = await _persisted(_wire(_TenK(**failure)), monkeypatch)
    assert out.degraded == reasons and writes == [], failure
    assert out.data_points[-1].shares_outstanding == 435.0, "the copy, served from memory only"
    assert "[soc-annual-share-count-unavailable]" in caplog.text and named in caplog.text
    assert "nothing depends on it" not in caplog.text


@pytest.mark.asyncio
async def test_a_payer_record_in_the_10k_window_names_the_failed_leg_once():
    """CRWV's real (preferred-inclusive) record already marks a failed annual leg through the
    dividend split; the share-count check must not add the reason a second time."""
    resp, _, degraded = await _wire(
        _TenK(ratios=CRWV_RATIOS, annual_cf_raises=True)
    )._build_signal_of_confidence("CRWV")
    assert degraded == ["annual_cash_flow"]
    assert resp.data_points[-1].shares_outstanding == 435.0


@pytest.mark.asyncio
async def test_a_failed_annual_leg_beside_a_trimmed_q1_adds_no_reason():
    """Q1 '26's income landed but its cash-flow row has not: Q1 '26 is trimmed
    (`cash_flow_row`, which the report ignores) and Q4 '25 was judged between its two
    neighbours — the annual statement was not needed."""
    resp, _, degraded = await _wire(
        _TenK(income_until="2026-03-31", annual_cf_raises=True)
    )._build_signal_of_confidence("CRWV")
    assert degraded == ["cash_flow_row"]
    assert resp.data_points[-1].period == "Q4 '25"
    assert resp.data_points[-1].shares_outstanding == round(_CRWV_Q4_25_LIKE / 1e6, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    dict(annual_cf_raises=True),
    dict(annual_cf={"Error Message": "Limit Reach"}),
    dict(annual_cf=[]),
    dict(ratios=[]),
])
async def test_a_failed_annual_leg_beside_a_trimmed_q4_adds_no_reason(failure):
    """Round 7 (R7-1), the twin of the trimmed-Q1 test above, which round 6 left unable to
    reach the "nothing trimmed after it" check: the judge reads the PRE-trim history, so a
    fiscal-Q4 copy whose cash-flow row has not landed still needs the annual figure —
    `annual_year_needed` is "2025" — but it is trimmed, Q3 '25 is the newest displayed point
    either way, and the build cannot change. Only that check keeps the failed leg from
    marking it and dropping a correct section from the report. (A raised or non-list ratios
    answer is a failed call whatever the window — R7-2 — so it is not this check's case.)"""
    from types import SimpleNamespace

    from app.services.agents.ticker_report_data_collector import _refuse_degraded_financials

    healthy, _, degraded = await _wire(
        _TenK(cf_until="2025-09-30")
    )._build_signal_of_confidence("CRWV")
    assert degraded == ["cash_flow_row"], "control: the trimmed build"
    assert (healthy.data_points[-1].period, healthy.data_points[-1].shares_outstanding) == \
        ("Q3 '25", 497.89)
    resp, _, degraded = await _wire(
        _TenK(cf_until="2025-09-30", **failure)
    )._build_signal_of_confidence("CRWV")
    assert degraded == ["cash_flow_row"], failure
    assert resp.model_dump(exclude={"degraded"}) == healthy.model_dump(exclude={"degraded"})
    out = SimpleNamespace(ticker="CRWV", signal_of_confidence=resp, degraded_sections=[])
    _refuse_degraded_financials(out)
    assert out.signal_of_confidence is resp and out.degraded_sections == [], \
        "the report must keep a section the failed leg could not change"


@pytest.mark.asyncio
async def test_an_underivable_annual_count_in_the_10k_window_is_said_and_stays_cacheable(caplog):
    """Both annual legs ANSWERED, but no FY2025 average can be derived (the per-share figures
    drifted away): possibly permanent vendor drift, so not a degraded reason — but never
    silent. The copy ships as FMP sent it (what build 1.0 (10) shows)."""
    caplog.set_level("WARNING")
    ratios = [{k: v for k, v in r.items()
               if k not in ("netIncomePerShare", "operatingCashFlowPerShare")}
              for r in _NON_PAYER_RATIOS]
    resp, _, degraded = await _wire(_TenK(ratios=ratios))._build_signal_of_confidence("CRWV")
    assert degraded == []
    assert resp.data_points[-1].shares_outstanding == 435.0
    assert "[soc-annual-share-count-unavailable]" in caplog.text and "FY2025" in caplog.text


@pytest.mark.asyncio
async def test_the_digest_never_names_an_estimated_newest_quarter_as_the_window_end():
    """R5-2's knock-on: in CRWV's 10-K window the newest point carries the like-for-like
    estimate for the chart (504.84M) while the summary's +23.32% runs to Q3 '25. The report
    digest used to name the window "over Q1 '24 to Q4 '25" — a quarter the figure does not
    span. It names the start only, as the iOS card does."""
    from app.services.agents import narrative_prompts as np_
    from app.services.agents.ticker_report_data_collector import _build_capital_allocation_block

    resp, _, degraded = await _wire(_TenK())._build_signal_of_confidence("CRWV")
    assert degraded == []
    block = _build_capital_allocation_block(resp)
    assert block["data_points"][-1]["period"] == "Q4 '25"
    assert block["data_points"][-1]["shares_outstanding"] == round(_CRWV_Q4_25_LIKE / 1e6, 2)
    assert block["share_count_change"] == round((497.89 - 403.73) / 403.73 * 100, 2)
    digest = "\n".join(np_._digest_insider({"insider_data": {"capital_allocation": block}}))
    assert "share count +23.3% since Q1 '24" in digest, digest
    assert "Q4 '25" not in digest, digest


def test_payload_version_moved_for_the_round_5_review():
    """v14: a flat / slow newest fiscal-Q4 count is no longer replaced, and an estimated
    newest count no longer moves the summary — values change, the shape does not."""
    assert sos._PAYLOAD_VERSION >= 14


# ── round-6 review 2026-10-08, R6-1: a failed annual leg marks the build only when the
#    annual figure could change a value — the newest fiscal-Q4 count moved past the bound
#    and its like-for-like count disagrees (`_newest_q4_copy_like`, the very helper the
#    refusal runs). Otherwise the build is identical without the annual statements, and
#    holding it out of the cache dropped a CORRECT section from 20-credit reports. ──────

# (counts in M, net income, EPS) for non-payers in their FY2025 10-K window
_R6_CANNOT_CHANGE = {
    "flat, EPS agrees": ([100] * 5, [100e6] * 5, [1.00] * 5),
    # like for like 109.5M — 9.5% off the flat count, so only the moved gate keeps it out
    "flat, noisy cents": ([100] * 5, [11.5e6] * 4 + [10.49e6], [0.12] * 4 + [0.10]),
    "a 4.9% move": ([100] * 4 + [95.1], [-100e6] * 5, [-1.00] * 5),
    "a real -10% tender its own EPS confirms": ([100] * 4 + [90], [90e6] * 4 + [81e6], [0.90] * 5),
    "a 13% move with no usable EPS": ([100] * 4 + [87], [-3e6] * 5, [-0.03] * 5),
}
# A ratios answer that is NOT a list (an error dict, a null body) is no case here: it is a
# failed call whatever the count, like a raise (round 7, R7-2 — the tests below).
_R6_FAILURES = {
    "cash flow raises": ("annual_cf", RuntimeError("FMP 429 on the annual cash-flow statement")),
    "cash flow error dict": ("annual_cf", {"Error Message": "Limit Reach"}),
    "cash flow []": ("annual_cf", []),
    "ratios []": ("ratios", []),
}


def _r6_window(case, failure=None):
    fmp = _q4_window(*_R6_CANNOT_CHANGE[case])
    if failure is not None:
        attr, value = _R6_FAILURES[failure]
        setattr(fmp, attr, value)
    return fmp


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", list(_R6_FAILURES))
@pytest.mark.parametrize("case", list(_R6_CANNOT_CHANGE))
async def test_a_failed_annual_leg_that_could_change_nothing_is_cached(case, failure, caplog,
                                                                       monkeypatch):
    # `_persisted` asks for "CRWV"; the symbol is all the two builds may not share otherwise
    healthy, _, degraded = await _wire(_r6_window(case))._build_signal_of_confidence("CRWV")
    assert degraded == [], f"control: {case}"
    caplog.set_level("WARNING")
    out, writes = await _persisted(_wire(_r6_window(case, failure)), monkeypatch)
    assert out.degraded == [] and writes, \
        f"{case} / {failure}: a build the annual figure could not change was held out of the cache"
    assert out.model_dump(exclude={"degraded"}) == healthy.model_dump(exclude={"degraded"})
    assert not [r for r in caplog.records
                if r.getMessage().startswith("[soc-annual-share-count-unavailable]")], \
        "no share-count warning for a count the annual figure could not change"


_CRWV_Q3, _CRWV_Q4 = (
    {"netIncome": -110_124_000, "eps": -0.22}, {"netIncome": -451_726_000, "eps": -0.89}
)


@pytest.mark.parametrize("args, kwargs, expected, why", [
    (("Q4 '25", 435e6, 497.886e6, _CRWV_Q4, _CRWV_Q3), {}, _CRWV_Q4_25_LIKE,
     "CRWV: moved 12.6%, like for like 504.8M — only the annual figure can decide"),
    (("Q1 '26", 435e6, 497.886e6, _CRWV_Q4, _CRWV_Q3), {}, None, "not a fiscal Q4"),
    (("Q4 '25", None, 497.886e6, _CRWV_Q4, _CRWV_Q3), {}, None, "no count"),
    (("Q4 '25", 435e6, None, _CRWV_Q4, _CRWV_Q3), {}, None, "no count before it"),
    (("Q4 '25", 435e6, 497.886e6, _CRWV_Q4, _CRWV_Q3), {"prev_is_artifact": True}, None,
     "the quarter before is itself an artifact"),
    (("Q4 '25", 435e6, 497.886e6, _CRWV_Q4, _CRWV_Q3), {"consecutive": False}, None,
     "not consecutive"),
    (("Q4 '25", 474e6, 497.886e6, _CRWV_Q4, _CRWV_Q3), {}, None, "moved 4.8%: inside the bound"),
    (("Q4 '25", 90e6, 100e6, {"netIncome": 81e6, "eps": 0.90}, {"netIncome": 90e6, "eps": 0.90}),
     {}, None, "a real -10% tender: like for like (90M) agrees with the count"),
    (("Q4 '25", 87e6, 100e6, {"netIncome": -3e6, "eps": -0.03}, {"netIncome": -3e6, "eps": -0.03}),
     {}, None, "moved 13% but |EPS| under 0.10: no like for like to judge with"),
])
def test_the_newest_q4_preconditions_live_in_one_helper(args, kwargs, expected, why):
    flags = {"prev_is_artifact": False, "consecutive": True, **kwargs}
    got = S._newest_q4_copy_like(*args, **flags)
    if expected is None:
        assert got is None, why
    else:
        assert got == pytest.approx(expected), why


def test_the_judge_names_the_year_only_the_annual_figure_can_decide():
    crwv = _crwv_quarters(_crwv_until("2025-12-31"))
    flagged, needed = S._judge_share_counts(crwv)                  # no annual statements
    assert needed == "2025" and "2025-12-31" not in flagged
    flagged, needed = S._judge_share_counts(crwv, _CRWV_ANNUAL)    # the copy recognised
    assert needed is None and "2025-12-31" in flagged
    flat, _annual = _edge(100e6, 100e6, prev_ni=100e6, prev_eps=0.90, ni=65e6, eps=0.55)
    assert S._judge_share_counts(flat) == ({}, None), "moved 0%: nothing to decide"
    assert S._share_count_glitches(crwv) == S._judge_share_counts(crwv)[0]


# ── round-7 review 2026-10-08, R7-2 (pre-existing since build 1.0 (10)): a ratios leg that
#    answers something other than a list — an FMP error dict, a null body — is a FAILED
#    leg, `annual_ratios`, exactly like a raise. It was coerced to [] and the build pinned
#    for 24h: a payer's card lost its history, DPS and growth, a stopped payer its real
#    bars. ────────────────────────────────────────────────────────────────────────────

_R7_ROWS = [("2024-09-30", "Q3", "2024"), ("2024-12-31", "Q4", "2024"),
            ("2025-03-31", "Q1", "2025"), ("2025-06-30", "Q2", "2025"),
            ("2025-09-30", "Q3", "2025")]
_R7_STEADY = {"2023": 2.0, "2024": 2.0, "2025": 2.0}
_R7_STOPPED = {"2023": 2.0, "2024": 2.0, "2025": 0.0}       # the INTC shape


def _r7_filer(dps_by_year, last_dividend):
    """Five quarters to Q3 '25 — not a fiscal Q4, so outside the 10-K window — of a 1B-share
    filer paying ``dps_by_year`` (fiscal year → common dividend per share, in equal quarterly
    cash), with the profile's ``lastDividend``."""
    income = [{"date": d, "period": p, "fiscalYear": fy, "weightedAverageShsOut": 1e9,
               "netIncome": 2e9, "eps": 2.0} for d, p, fy in _R7_ROWS]
    cf_q = [{"date": d, "commonDividendsPaid": -dps_by_year.get(fy, 0.0) * 1e9 / 4,
             "netDividendsPaid": -dps_by_year.get(fy, 0.0) * 1e9 / 4,
             "preferredDividendsPaid": 0, "commonStockRepurchased": -500e6}
            for d, _p, fy in _R7_ROWS]
    cf_a = [{"date": f"{y}-12-31", "fiscalYear": y, "period": "FY",
             "commonDividendsPaid": -dps * 1e9, "preferredDividendsPaid": 0,
             "netDividendsPaid": -dps * 1e9, "netIncome": 8e9, "operatingCashFlow": 10e9}
            for y, dps in dps_by_year.items()]
    record = [{"date": f"{y}-12-31", "fiscalYear": y, "dividendPerShare": dps,
               "netIncomePerShare": 8.0, "operatingCashFlowPerShare": 10.0}
              for y, dps in dps_by_year.items()]
    fmp = _SeriesFMP(income=income, cf_q=cf_q, cf_a=cf_a, ratios=record, cap=100e9)
    fmp.profile = [{"lastDividend": last_dividend, "marketCap": 100e9, "price": 100.0}]
    return fmp


def _r7_unreadable(fmp, body):
    fmp.ratios = body
    return fmp


def _report_section(resp):
    """(the SoC section the report keeps, its degraded_sections) via the real collector gate."""
    from types import SimpleNamespace

    from app.services.agents.ticker_report_data_collector import _refuse_degraded_financials

    out = SimpleNamespace(ticker="X", signal_of_confidence=resp, degraded_sections=[])
    _refuse_degraded_financials(out)
    return out.signal_of_confidence, out.degraded_sections


# An unreadable ratios answer and the tag that says so: not a list (round 7), or [] while the
# annual cash-flow statement books a common dividend (round 8, R8-1 — `_r7_filer` books one).
_R7_UNREADABLE = [
    ({"Error Message": "Limit Reach"}, "[soc-ratios-not-a-list]"),
    (None, "[soc-ratios-not-a-list]"),
    ("oops", "[soc-ratios-not-a-list]"),
    ([], "[soc-ratios-empty]"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("body, tag", _R7_UNREADABLE)
async def test_a_payer_whose_ratios_answer_is_unreadable_is_never_persisted(body, tag, caplog,
                                                                           monkeypatch):
    out, writes = await _persisted(_wire(_r7_filer(_R7_STEADY, 2.0)), monkeypatch)
    assert out.degraded == [] and writes, "control: a healthy payer reaches the 24h tier"
    assert out.dividend_info is not None and out.dividend_info.dividend_per_share == 2.0
    assert out.dividend_info.annual_dividends, "control: the card's history"

    caplog.set_level("WARNING")
    out, writes = await _persisted(_wire(_r7_unreadable(_r7_filer(_R7_STEADY, 2.0), body)),
                                   monkeypatch)
    assert out.degraded == ["annual_ratios"] and writes == [], body
    assert [r for r in caplog.records
            if r.getMessage().startswith(tag)
            and "step=annual_ratios" in r.getMessage()], "the failed leg is said, with its tag"
    assert _report_section(out) == (None, ["signal_of_confidence:annual_ratios"])


@pytest.mark.asyncio
@pytest.mark.parametrize("body, tag", [_R7_UNREADABLE[0], _R7_UNREADABLE[3]])
async def test_a_stopped_payer_whose_ratios_answer_is_unreadable_is_never_persisted(body, tag,
                                                                                   monkeypatch):
    """Without the per-share record a STOPPED payer reads exactly like a never-payer
    (lastDividend 0): its real FY2024 dividend quarters are zeroed. That build must not be
    pinned for a day, nor frozen into a report (it would read dividend_status "None")."""
    out, writes = await _persisted(_wire(_r7_filer(_R7_STOPPED, 0.0)), monkeypatch)
    assert out.degraded == [] and writes, "control"
    paid = {p.period: p.dividend_amount for p in out.data_points}
    assert (paid["Q3 '24"], paid["Q4 '24"], paid["Q1 '25"]) == (500.0, 500.0, 0.0), "control"

    out, writes = await _persisted(_wire(_r7_unreadable(_r7_filer(_R7_STOPPED, 0.0), body)),
                                   monkeypatch)
    assert out.degraded == ["annual_ratios"] and writes == [], tag
    assert all(p.dividend_amount == 0.0 for p in out.data_points), \
        "the reason it may not be pinned: the real FY2024 bars are gone"
    assert _report_section(out) == (None, ["signal_of_confidence:annual_ratios"])


@pytest.mark.asyncio
async def test_a_non_payer_whose_ratios_answer_is_not_a_list_is_not_persisted_either(monkeypatch):
    """The decision for a NON-payer (CRWV, outside its 10-K window): degraded like a raise,
    never pinned — the codebase's rule for a failed ratios call, payer or not. Here the values
    happen to match the healthy build, but only the record unread could say so: without it a
    never-payer and a stopped payer look the same (the test above), and it is what keeps a
    mis-tagged dividend line (PLUG) off the chart. A raise has always been degraded too."""
    healthy, _, degraded = await _wire(_FMP())._build_signal_of_confidence("CRWV")
    assert degraded == [] and healthy.data_points[-1].period == "Q2 '26"
    out, writes = await _persisted(_wire(_FMP(ratios={"Error Message": "Limit Reach"})),
                                   monkeypatch)
    assert out.degraded == ["annual_ratios"] and writes == []
    assert out.model_dump(exclude={"degraded"}) == healthy.model_dump(exclude={"degraded"})


@pytest.mark.asyncio
async def test_a_non_payer_whose_ratios_answer_is_empty_stays_cacheable(caplog, monkeypatch):
    """Round 8 (R8-1), the must-keep twin of the payer `[]` cases: an empty ratios LIST is a
    failed leg only beside a COMMON dividend in the annual cash-flow statement. CRWV's books
    PREFERRED dividends only ($57.7M FY2024, $29M FY2025), so its `[]` record is the honest
    one and its build is the healthy build — cached as such (as R6-1 keeps it)."""
    healthy, _, degraded = await _wire(_FMP())._build_signal_of_confidence("CRWV")
    assert degraded == []
    caplog.set_level("WARNING")
    out, writes = await _persisted(_wire(_FMP(ratios=[])), monkeypatch)
    assert out.degraded == [] and writes, "an honest empty record was held out of the cache"
    assert out.model_dump(exclude={"degraded"}) == healthy.model_dump(exclude={"degraded"})
    assert not [r for r in caplog.records if r.getMessage().startswith("[soc-ratios-empty]")]
    assert _report_section(out) == (out, [])
