"""Overview Key Stats P/E for a filer whose statements are in another currency than its price,
and TTM EPS for a half-year filer (2026-10-09).

THE BUG: `_build_key_statistics` divided the live price (trading currency — USD for an ADR) by
TTM EPS from the statements (REPORTING currency). TSM printed P/E "1.04" ($452.69 ÷ 434.95 TWD)
against a true ~29.6. Owner decision: such a filer gets FMP's `ratios-ttm` multiple (currency-
consistent: FMP converts the market cap into the reporting currency), checked against our TTM EPS;
P/E (FWD) = that multiple's price ÷ the analysts' estimate (both in the reporting currency); EPS
(TTM) carries its currency code. And BHP's four "quarterly" rows are half-years: the TTM is the
newest two, not two years of earnings.

The FMP answers are RECORDED (tests/fixtures/key_stats_pe/fmp_2026_10_09.json); hermetic.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import pytest

from app.services import stock_overview_service as S
from app.services.stock_overview_service import (
    PE_BASIS_LIVE,
    PE_BASIS_PROVIDER,
    PE_BASIS_UNCONFIRMED,
    StockOverviewService,
    _provider_pe,
    _provider_pe_fields,
    _ttm_eps,
    _ttm_rows,
    key_stats_currencies,
    pe_basis,
    provider_pe_needed,
)

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "key_stats_pe" / "fmp_2026_10_09.json").read_text())
_TODAY = "2026-10-09"   # the recording day: the nearest estimate not yet ended is FY2026's
_FOREIGN = ["TSM", "BABA", "HDB", "NVO", "SONY", "TM", "ASML", "SAP"]


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    monkeypatch.setattr(S, "_utc_today_iso", lambda: _TODAY)


def _rec(t):
    return _FIXTURE[t]


def _stats(rec=None, *, profile=None, quote=None, income_quarterly=None, income_annual=None,
           ratios_ttm=None, analyst_est=None, key_metrics=None, price=None):
    """Drive the REAL builder; returns label → value from the grouped rows the screen renders."""
    rec = rec or {}
    profile = rec.get("profile", {}) if profile is None else profile
    price = (profile.get("price") or 0.0) if price is None else price
    quote = {"price": price} if quote is None else quote
    svc = StockOverviewService.__new__(StockOverviewService)
    _flat, groups = svc._build_key_statistics(
        quote, profile, key_metrics or [],
        rec.get("analyst_est", []) if analyst_est is None else analyst_est, price,
        income_quarterly=rec.get("income_quarterly") if income_quarterly is None else income_quarterly,
        income_annual=rec.get("income_annual") if income_annual is None else income_annual,
        ratios_ttm=(_provider_pe_fields(rec.get("ratios_ttm")) if ratios_ttm is None else ratios_ttm),
    )
    flat = {i.label: i.value for i in _flat}
    grouped = {i.label: i.value for g in groups for i in g.statistics}
    for label in ("P/E (TTM)", "P/E (FWD)", "EPS (TTM)"):
        assert flat[label] == grouped[label], f"{label}: the flat and grouped rows disagree"
    return grouped


# ── 1. The recorded filers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("t", _FOREIGN)
def test_a_foreign_filer_gets_fmps_one_currency_multiple_not_price_over_eps(t):
    rec = _rec(t)
    stats = _stats(rec)
    fmp_pe = rec["ratios_ttm"][0]["priceToEarningsRatioTTM"]
    assert stats["P/E (TTM)"] == ("Neg." if fmp_pe < 0 else f"{fmp_pe:.2f}")
    # The old division, which must never come back.
    eps = sum(q["epsDiluted"] for q in rec["income_quarterly"])
    old = rec["profile"]["price"] / round(eps, 2)
    assert stats["P/E (TTM)"] != f"{old:.2f}"
    ccy = rec["income_quarterly"][0]["reportedCurrency"]
    assert stats["EPS (TTM)"] == f"{ccy} {round(eps, 2):.2f}", "a statement figure carries its code"


def test_tsm_pinned():
    """$452.69 ÷ 434.95 TWD printed 1.04. FMP: 29.56; forward 29.558 × 431.37 TWD ÷ 538.40 TWD."""
    stats = _stats(_rec("TSM"))
    assert stats["P/E (TTM)"] == "29.56"
    assert stats["P/E (FWD)"] == "23.68"
    assert stats["EPS (TTM)"] == "TWD 434.95"


def test_a_loss_maker_abroad_keeps_neg_and_a_real_forward_multiple():
    """SONY: TTM loss (FMP -103.78, our EPS -37.38 JPY), FY2028 estimate +231.24 JPY."""
    stats = _stats(_rec("SONY"))
    assert stats["P/E (TTM)"] == "Neg." and stats["EPS (TTM)"] == "JPY -37.38"
    price_jpy = -103.78231292517005 * -37.613559639623496
    assert stats["P/E (FWD)"] == f"{price_jpy / 231.23623:.2f}"


@pytest.mark.parametrize("t", _FOREIGN)
def test_the_forward_multiple_is_in_one_currency(t):
    rec = _rec(t)
    stats = _stats(rec)
    r = rec["ratios_ttm"][0]
    price_rc = r["priceToEarningsRatioTTM"] * r["netIncomePerShareTTM"]
    fwd = min((e for e in rec["analyst_est"] if e["date"] >= _TODAY), key=lambda e: e["date"])["epsAvg"]
    assert stats["P/E (FWD)"] == ("Neg." if price_rc / fwd < 0 else f"{price_rc / fwd:.2f}")
    assert stats["P/E (FWD)"] != f"{rec['profile']['price'] / fwd:.2f}", "never the USD price ÷ TWD estimate"


def test_every_recorded_adr_reports_eps_per_ads():
    """The premise that lets the per-ADS price meet FMP's EPS: market cap ÷ price ÷ diluted
    shares is ~1 on every ADR recorded (TSM 1 ADS = 5 ordinary shares, BHP 2, TM 10 …)."""
    for t, rec in _FIXTURE.items():
        if t.startswith("_") or not rec["profile"].get("isAdr"):
            continue
        p = rec["profile"]
        ratio = p["marketCap"] / p["price"] / rec["income_quarterly"][0]["weightedAverageShsOutDil"]
        assert 0.95 <= ratio <= 1.05, (t, ratio)


def test_fmp_converts_the_market_cap_into_the_reporting_currency():
    """Why `ratios-ttm` is safe: TSM's TTM market cap is in TWD (×28.2 the USD profile figure)."""
    rec = _rec("TSM")
    implied_fx = rec["key_metrics_ttm"][0]["marketCap"] / rec["profile"]["marketCap"]
    assert 25 < implied_fx < 35


def test_a_half_year_filer_sums_two_halves_not_two_years():
    """BHP: rows six months apart. 1.66 + 2.22 = 3.88, never 7.44."""
    rec = _rec("BHP")
    stats = _stats(rec)
    assert stats["EPS (TTM)"] == "3.88"
    assert stats["P/E (TTM)"] == f"{rec['profile']['price'] / 3.88:.2f}"
    assert abs(rec["profile"]["price"] / 3.88 - rec["ratios_ttm"][0]["priceToEarningsRatioTTM"]) < 1.0


def test_one_currency_keeps_the_live_multiple():
    """INFY: USD statements, USD price — live price ÷ EPS (FMP's own ratios happen to be in INR,
    which is why its multiple is used only where our two currencies differ)."""
    stats = _stats(_rec("INFY"))
    assert stats["P/E (TTM)"] == f"{10.765 / 0.81:.2f}" and stats["EPS (TTM)"] == "0.81"
    aapl = _stats(_rec("AAPL"))
    assert aapl["P/E (TTM)"] == f"{333.2 / 8.73:.2f}" and aapl["EPS (TTM)"] == "8.73"
    assert aapl["P/E (FWD)"] == f"{333.2 / 9.60811:.2f}"


# ── 2. Degraded and hostile inputs: "—", never a wrong number ─────────────────────────

def _tsm_with(**over):
    rec = dict(_rec("TSM"))
    rec.update(over)
    return rec


@pytest.mark.parametrize("ratios,why", [
    ({}, "no multiple in the bundle"),
    ({"priceToEarningsRatioTTM": 29.56}, "no EPS to check it against"),
    ({"priceToEarningsRatioTTM": 29.56, "netIncomePerShareTTM": 86.27}, "per ordinary share (5x off)"),
    ({"priceToEarningsRatioTTM": 29.56, "netIncomePerShareTTM": 15.31}, "a USD EPS against TWD"),
    ({"priceToEarningsRatioTTM": 29.56, "netIncomePerShareTTM": -431.37}, "sign disagrees with ours"),
    ({"priceToEarningsRatioTTM": -29.56, "netIncomePerShareTTM": 431.37}, "ratio sign disagrees"),
    ({"priceToEarningsRatioTTM": 0, "netIncomePerShareTTM": 431.37}, "zero = FMP's absent"),
    ({"priceToEarningsRatioTTM": 29.56, "netIncomePerShareTTM": 0}, "zero EPS"),
    ({"priceToEarningsRatioTTM": float("nan"), "netIncomePerShareTTM": 431.37}, "NaN"),
    ({"priceToEarningsRatioTTM": float("inf"), "netIncomePerShareTTM": 431.37}, "inf"),
    ({"priceToEarningsRatioTTM": True, "netIncomePerShareTTM": 431.37}, "a bool"),
    ({"priceToEarningsRatioTTM": "29.56", "netIncomePerShareTTM": 431.37}, "a string"),
    ("garbage", "not a dict"),
])
def test_an_unusable_multiple_is_withheld(ratios, why, caplog):
    caplog.set_level(logging.WARNING, logger=S.__name__)
    stats = _stats(_rec("TSM"), ratios_ttm=ratios)
    assert stats["P/E (TTM)"] == "—", why
    assert stats["P/E (FWD)"] == "—", why
    assert stats["EPS (TTM)"] == "TWD 434.95", "the statement figure stays, labelled"
    assert any("[pe-withheld]" in r.getMessage() for r in caplog.records), "never silently"


@pytest.mark.parametrize("nips,ok", [(434.95, True), (434.95 * 1.149, True), (434.95 * 0.851, True),
                                     (434.95 * 1.151, False), (434.95 * 0.849, False)])
def test_the_eps_check_tolerance_boundary(nips, ok):
    got = _provider_pe({"priceToEarningsRatioTTM": 29.56, "netIncomePerShareTTM": nips}, 434.95)
    assert (got is not None) == ok


def test_no_trading_currency_fallback_for_a_foreign_filer():
    """Quarterly rows missing: the earnings-yield × price fallback is a USD figure — never shown
    as the TWD EPS, never divided into a P/E."""
    stats = _stats(_rec("TSM"), income_quarterly=[],
                   key_metrics=[{"earningsYield": 0.035}], quote={"price": 452.69, "eps": 15.9})
    assert stats["EPS (TTM)"] == "—" and stats["P/E (TTM)"] == "—" and stats["P/E (FWD)"] == "—"


def test_an_unknown_price_currency_withholds_both_multiples(caplog):
    caplog.set_level(logging.WARNING, logger=S.__name__)
    profile = {k: v for k, v in _rec("TSM")["profile"].items() if k != "currency"}
    stats = _stats(_rec("TSM"), profile=profile)
    assert stats["P/E (TTM)"] == "—" and stats["P/E (FWD)"] == "—"
    assert stats["EPS (TTM)"] == "TWD 434.95"
    assert any("price's currency is unknown" in r.getMessage() for r in caplog.records)


def test_the_quote_supplies_the_trading_currency_when_the_profile_has_none():
    profile = {k: v for k, v in _rec("TSM")["profile"].items() if k != "currency"}
    stats = _stats(_rec("TSM"), profile=profile, quote={"price": 452.69, "currency": "usd"})
    assert stats["P/E (TTM)"] == "29.56"


def test_the_annual_statements_name_the_currency_when_the_quarters_do_not():
    rows = [{k: v for k, v in q.items() if k != "reportedCurrency"}
            for q in _rec("TSM")["income_quarterly"]]
    stats = _stats(_rec("TSM"), income_quarterly=rows)
    assert stats["P/E (TTM)"] == "29.56" and stats["EPS (TTM)"] == "TWD 434.95"


def test_ttm_rows_in_two_currencies_are_never_summed(caplog):
    caplog.set_level(logging.WARNING, logger=S.__name__)
    rows = [dict(q) for q in _rec("TSM")["income_quarterly"]]
    rows[3]["reportedCurrency"] = "USD"
    stats = _stats(_rec("TSM"), income_quarterly=rows)
    assert stats["EPS (TTM)"] == "—" and stats["P/E (TTM)"] == "—"
    assert any("[eps-ttm-mixed-currency]" in r.getMessage() for r in caplog.records)


def test_a_missing_forward_estimate_leaves_only_the_forward_row_blank():
    stats = _stats(_rec("TSM"), analyst_est=[{"date": "2025-12-31", "epsAvg": 323.3}])
    assert stats["P/E (TTM)"] == "29.56" and stats["P/E (FWD)"] == "—"


def test_a_forecast_loss_abroad_reads_neg():
    stats = _stats(_rec("TSM"), analyst_est=[{"date": "2026-12-31", "epsAvg": -12.0}])
    assert stats["P/E (FWD)"] == "Neg."


def test_an_overflowing_forward_multiple_is_unknown_not_inf():
    for t in ("TSM", "AAPL"):
        stats = _stats(_rec(t), analyst_est=[{"date": "2026-12-31", "epsAvg": 1e-320}])
        assert stats["P/E (FWD)"] == "—", t


@pytest.mark.parametrize("est", ["junk", {"date": "2026-12-31"}, [None, 7], [{"date": None, "epsAvg": 5.0}],
                                 [{"date": 20261231, "epsAvg": 5.0}], [{"date": "2026-12-31"}],
                                 [{"date": "2026-12-31", "epsAvg": 0}]])
def test_malformed_estimates_are_unknown(est):
    assert _stats(_rec("AAPL"), analyst_est=est)["P/E (FWD)"] == "—"
    assert S._nearest_forward_eps(None, _TODAY) is None


# ── 3. Which multiple, decided by the two currencies ──────────────────────────────────

@pytest.mark.parametrize("trading,statements,expected", [
    ("USD", "USD", PE_BASIS_LIVE), ("JPY", "JPY", PE_BASIS_LIVE),
    ("USD", None, PE_BASIS_LIVE), (None, None, PE_BASIS_LIVE),
    ("USD", "TWD", PE_BASIS_PROVIDER), ("CAD", "USD", PE_BASIS_PROVIDER),
    (None, "TWD", PE_BASIS_UNCONFIRMED), (None, "USD", PE_BASIS_UNCONFIRMED),
])
def test_pe_basis(trading, statements, expected):
    assert pe_basis(trading, statements) == expected


def test_currencies_are_read_never_assumed():
    assert key_stats_currencies({"currency": " usd "}, {}, [{"date": "2026-06-30",
                                "reportedCurrency": "twd"}]) == ("USD", "TWD")
    assert key_stats_currencies({"currency": "US$"}, None, [], [{"reportedCurrency": "USDT"}]) == (None, None)
    assert key_stats_currencies(None, "junk", None, None) == (None, None)
    # The NEWEST quarterly row decides (FMP's order is not trusted).
    rows = [{"date": "2025-09-30", "reportedCurrency": "USD"}, {"date": "2026-06-30", "reportedCurrency": "TWD"}]
    assert key_stats_currencies({"currency": "USD"}, {}, rows)[1] == "TWD"


def test_provider_pe_needed_only_for_two_known_currencies():
    tsm = _rec("TSM")
    assert provider_pe_needed(tsm["profile"], tsm["income_quarterly"], tsm["income_annual"]) is True
    aapl = _rec("AAPL")
    assert provider_pe_needed(aapl["profile"], aapl["income_quarterly"], aapl["income_annual"]) is False
    assert provider_pe_needed({}, tsm["income_quarterly"], []) is False, "unknown price currency"
    assert provider_pe_needed(None, None, None) is False


def test_provider_fields_keep_only_the_two_finite_figures():
    raw = [{"priceToEarningsRatioTTM": 29.5, "netIncomePerShareTTM": float("nan"),
            "priceToBookRatioTTM": 8.0, "symbol": "TSM"}]
    assert _provider_pe_fields(raw) == {"priceToEarningsRatioTTM": 29.5}
    for junk in ([], None, {"x": 1}, ["row"], [None]):
        assert _provider_pe_fields(junk) == {}


# ── 4. TTM cadence ──────────────────────────────────────────────────────────────────

def _q(date, eps, ccy="USD"):
    return {"date": date, "epsDiluted": eps, "reportedCurrency": ccy}


def test_four_quarters_with_a_52_53_week_calendar_are_summed():
    rows = [_q("2026-06-27", 2.0), _q("2026-03-28", 2.0), _q("2025-12-27", 3.0), _q("2025-09-27", 2.0)]
    assert _ttm_eps(rows) == 9.0


def test_unsorted_dated_rows_are_ordered_first():
    rows = [_q("2025-09-30", 1.0), _q("2026-06-30", 4.0), _q("2025-12-31", 2.0), _q("2026-03-31", 3.0)]
    assert _ttm_eps(rows) == 10.0


@pytest.mark.parametrize("rows,why", [
    ([_q("2026-06-30", 1.0), _q("2026-03-31", 1.0), _q("2025-09-30", 1.0), _q("2025-06-30", 1.0)],
     "a missing quarter: 15 months"),
    ([_q("2026-06-30", 1.0), _q("2026-06-30", 1.0), _q("2026-03-31", 1.0), _q("2025-12-31", 1.0)],
     "a duplicate period"),
    ([_q("2026-06-30", 1.0), _q("2025-06-30", 1.0), _q("2024-06-30", 1.0), _q("2023-06-30", 1.0)],
     "annual rows"),
    ([_q("2026-06-30", 1.0), _q("2026-05-31", 1.0), _q("2026-04-30", 1.0), _q("2026-03-31", 1.0)],
     "monthly rows"),
])
def test_an_irregular_cadence_is_no_ttm(rows, why, caplog):
    caplog.set_level(logging.WARNING, logger=S.__name__)
    assert _ttm_rows(rows) is None, why
    assert _ttm_eps(rows, "X") is None, why
    assert any("[eps-ttm-cadence] X" in r.getMessage() for r in caplog.records)


def test_three_half_years_give_the_newest_two():
    rows = [_q("2026-06-30", 1.66), _q("2025-12-31", 2.22), _q("2025-06-30", 1.82)]
    assert _ttm_eps(rows) == 3.88


@pytest.mark.parametrize("rows", [
    [], None, {"date": "2026-06-30"}, "junk",
    [_q("2026-06-30", 1.0), _q("2025-12-31", 1.0)],                   # two rows prove no cadence
    [_q("2026-06-30", 1.0), _q("2026-03-31", 1.0), _q("2025-12-31", 1.0)],   # three quarters
])
def test_too_few_rows_is_no_ttm(rows):
    assert _ttm_eps(rows) is None


def test_undated_rows_keep_the_first_four_as_before():
    rows = [{"epsDiluted": v} for v in (1.85, 1.57, 1.65, 2.40)]
    assert _ttm_eps(rows) == 7.47
    rows[1]["date"] = "2026-02-30"            # one unparseable date: no cadence to check
    assert _ttm_eps(rows) == 7.47


def test_junk_rows_are_skipped_and_a_bad_eps_sinks_the_ttm():
    good = [_q("2026-06-30", 1.0), _q("2026-03-31", 1.0), _q("2025-12-31", 1.0), _q("2025-09-30", 1.0)]
    assert _ttm_eps([None, "x", *good]) == 4.0
    for bad in (float("nan"), True, "abc", None):
        rows = [dict(r) for r in good]
        rows[2]["epsDiluted"] = bad
        rows[2].pop("eps", None)
        assert _ttm_eps(rows) is None, bad
    rows = [dict(r) for r in good]
    rows[0]["epsDiluted"] = None
    rows[0]["eps"] = 0.0                       # presence: a break-even quarter is a value
    assert _ttm_eps(rows) == 3.0
    huge = [dict(r, epsDiluted=1e308) for r in good]
    assert _ttm_eps(huge) is None, "an overflowing sum is not a number"


# ── 5. The fundamentals bundle fetches FMP's multiple only for a foreign filer ────────────

class _FMP:
    """The fundamentals fan-out, answering from a recorded filer; unknown legs answer []."""

    def __init__(self, rec, ratios=None):
        self.rec, self.ratios, self.calls = rec, ratios, []

    async def get_company_profile(self, ticker):
        return dict(self.rec["profile"])

    async def get_income_statement(self, ticker, period="annual", limit=3):
        return [dict(r) for r in self.rec["income_quarterly" if period == "quarter" else "income_annual"]]

    async def get_key_metrics(self, ticker, period="annual", limit=5):
        return [{"date": "2025-12-31", "earningsYield": 0.03}]

    async def get_historical_prices(self, symbol, frm, to):
        return [{"date": "2026-10-01", "close": 1.0}]

    async def get_analyst_estimates(self, ticker, period="annual", limit=5):
        return [dict(e) for e in self.rec["analyst_est"]]

    async def get_ratios_ttm(self, ticker):
        self.calls.append(("ratios_ttm", ticker))
        if isinstance(self.ratios, BaseException):
            raise self.ratios
        return self.rec["ratios_ttm"] if self.ratios is None else self.ratios

    def __getattr__(self, name):
        async def _call(*args, **kwargs):
            return []
        return _call


class _NoMovers:
    async def get_sector_performance(self):
        return []

    async def get_industry_performance(self):
        return []


def _svc(monkeypatch, fmp, db_row=None):
    async def _no_short(ticker):
        return {}

    monkeypatch.setattr(S, "get_short_interest", _no_short)
    monkeypatch.setattr(S, "get_market_movers_service", lambda: _NoMovers())
    S._cache.clear()
    S._fundamentals_inflight.clear()
    svc = StockOverviewService.__new__(StockOverviewService)
    svc.fmp, svc.supabase = fmp, None
    upserts = []
    monkeypatch.setattr(svc, "_check_fundamentals_db", lambda ticker: db_row)
    monkeypatch.setattr(svc, "_upsert_fundamentals_db", lambda ticker, data: upserts.append(dict(data)))
    # Every bundle passes the close-cycle check: the essentials gate is what is under test.
    monkeypatch.setattr(S, "_bundle_is_current", lambda bundle, cached_at, now=None: True)
    return svc, upserts


@pytest.mark.asyncio
async def test_a_foreign_filers_bundle_carries_the_trimmed_multiple(monkeypatch):
    fmp = _FMP(_rec("TSM"))
    svc, upserts = _svc(monkeypatch, fmp)
    data = await svc._get_fundamentals("TSM")
    assert fmp.calls == [("ratios_ttm", "TSM")]
    assert data["ratios_ttm"] == {"priceToEarningsRatioTTM": 29.558363278080446,
                                  "netIncomePerShareTTM": 431.37}
    assert len(upserts) == 1 and upserts[0]["ratios_ttm"] == data["ratios_ttm"]
    assert "symbol" not in data["ratios_ttm"], "trimmed to the two figures"


@pytest.mark.asyncio
async def test_a_domestic_filer_makes_no_extra_call(monkeypatch):
    fmp = _FMP(_rec("AAPL"))
    svc, upserts = _svc(monkeypatch, fmp)
    data = await svc._get_fundamentals("AAPL")
    assert fmp.calls == [] and data["ratios_ttm"] == {} and len(upserts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ratios", [RuntimeError("ratios 503"), {"Error Message": "Limit"}])
async def test_a_failed_multiple_leg_is_served_but_never_cached(monkeypatch, caplog, ratios):
    caplog.set_level(logging.WARNING, logger=S.__name__)
    svc, upserts = _svc(monkeypatch, _FMP(_rec("TSM"), ratios=ratios))
    data = await svc._get_fundamentals("TSM")
    assert data["ratios_ttm"] == {}
    assert upserts == [] and "fundamentals:TSM" not in S._cache, "a failure must not pin '—' for a day"
    assert any("[pe-provider-leg]" in r.getMessage() for r in caplog.records)
    assert any("ratios_ttm" in r.getMessage() and "NOT cached" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_an_answered_empty_multiple_is_cached(monkeypatch):
    svc, upserts = _svc(monkeypatch, _FMP(_rec("TSM"), ratios=[]))
    data = await svc._get_fundamentals("TSM")
    assert data["ratios_ttm"] == {} and len(upserts) == 1


def _db_row(rec, **extra):
    row = {"profile": dict(rec["profile"]), "income_quarterly": rec["income_quarterly"],
           "income_annual": rec["income_annual"], "key_metrics": [{"date": "2025-12-31"}],
           "stock_historical": [{"date": "2026-10-01", "close": 1.0}]}
    row.update(extra)
    return row


@pytest.mark.asyncio
async def test_a_tier2_row_from_before_the_multiple_is_rebuilt_once(monkeypatch):
    fmp = _FMP(_rec("TSM"))
    svc, upserts = _svc(monkeypatch, fmp, db_row=_db_row(_rec("TSM")))
    data = await svc._get_fundamentals("TSM")
    assert fmp.calls == [("ratios_ttm", "TSM")] and data["ratios_ttm"]["netIncomePerShareTTM"] == 431.37
    assert len(upserts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("t,extra", [("TSM", {"ratios_ttm": {}}), ("AAPL", {})])
async def test_a_tier2_row_that_needs_nothing_more_is_served(monkeypatch, t, extra):
    fmp = _FMP(_rec(t))
    svc, upserts = _svc(monkeypatch, fmp, db_row=_db_row(_rec(t), **extra))
    await svc._get_fundamentals(t)
    assert fmp.calls == [] and upserts == []


@pytest.mark.asyncio
async def test_the_overview_rows_come_from_the_bundles_multiple(monkeypatch):
    """End to end: bundle → `_build_full_response` → the Key Stats row the screen decodes."""
    svc, _ = _svc(monkeypatch, _FMP(_rec("TSM")))
    fund = await svc._get_fundamentals("TSM")
    resp = svc._build_full_response("TSM", fund, {"quote": {"price": 452.69}, "chart_data": []},
                                    "3M", "1day", False)
    rows = {i.label: i.value for g in resp.key_statistics_groups for i in g.statistics}
    flat = {i.label: i.value for i in resp.key_statistics}
    assert rows["P/E (TTM)"] == flat["P/E (TTM)"] == "29.56"
    assert rows["EPS (TTM)"] == "TWD 434.95" and rows["P/E (FWD)"] == "23.68"
    assert all(isinstance(v, str) for v in rows.values()), "iOS decodes display strings"
    assert math.isfinite(resp.current_price)
