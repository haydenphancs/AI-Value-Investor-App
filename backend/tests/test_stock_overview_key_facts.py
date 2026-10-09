"""`StockOverviewService.get_key_facts` — the Overview's Key Stats for Ask Cay AI's financials tool.

Pins: the rows are byte-for-byte what `_build_key_statistics` (the screen's builder) prints for
the same bundle and quote; a placeholder ('—', 'N/A') is never a value; a failed quote serves
the fundamentals-only rows and withholds every price-dependent one (never raises); the currencies
are read, never assumed, and a filer whose statements are in another currency than its price gets
the screen's one-currency P/E (`pe_basis`), never price ÷ EPS across currencies; the balance-sheet
totals omit what is missing, never 0; the short-
interest read is bounded and keeps warming its cache. Hermetic: no FMP, no Supabase.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from app.services import stock_overview_service as sos
from app.services.stock_overview_service import (
    StockOverviewService,
    _balance_sheet_totals,
    _currency_code,
    short_percent_of_float,
)


def _bundle(**over):
    base = {
        "profile": {"companyName": "Apple Inc.", "currency": "USD", "beta": 1.2, "lastDiv": 1.04,
                    "mktCap": 3.4e12, "range": "169.21-260.10", "country": "US", "isAdr": False,
                    "isEtf": False, "isFund": False},
        "key_metrics": [{"date": "2025-09-27", "earningsYield": 0.03}],
        "fin_ratios": [],
        "income_annual": [{"date": "2024-09-28", "reportedCurrency": "USD"},
                          {"date": "2025-09-27", "reportedCurrency": "usd"}],
        # The real FMP shape (AAPL FY2024): netDebt = totalDebt − cashAndCashEquivalents, so it
        # never equals totalDebt − cashAndShortTermInvestments (final review 2026-10-09).
        "balance_annual": [{"date": "2025-09-27", "fiscalYear": "2025", "reportedCurrency": "USD",
                            "totalDebt": 1.06629e11, "cashAndCashEquivalents": 2.9943e10,
                            "cashAndShortTermInvestments": 6.5171e10, "netDebt": 7.6686e10}],
        "cashflow_annual": [],
        "analyst_est": [{"date": "2027-09-25", "epsAvg": 8.6}, {"date": "2020-09-26", "epsAvg": 3.0}],
        "shares_float": {"floatShares": 14.8e9, "freeFloat": 99.7, "outstandingShares": 14.84e9},
        "inst_ownership": [{"ownershipPercent": 61.2}],
        "income_quarterly": [{"epsDiluted": 1.85}, {"epsDiluted": 1.57}, {"epsDiluted": 1.65},
                             {"epsDiluted": 2.40}],
        "short_interest": {"shares_short": 1.2e8, "short_ratio": 1.8, "short_change_3m": -4.2,
                           "settlement_date": "2026-09-30"},
    }
    base.update(over)
    return base


_QUOTE = {"price": 250.0, "marketCap": 3.71e12, "yearHigh": 260.1, "yearLow": 169.21,
          "sharesOutstanding": 14.84e9, "open": 248.0, "previousClose": 247.5, "dayHigh": 251.0,
          "dayLow": 246.9, "volume": 4.1e7, "avgVolume": 5.2e7}


class _Price:
    def __init__(self, quote=None, exc=None):
        self.quote, self.exc, self.calls = quote, exc, []

    async def get_quote(self, sym):
        self.calls.append(sym)
        if self.exc:
            raise self.exc
        return self.quote


def _svc(monkeypatch, bundle=None, quote=_QUOTE, quote_exc=None, fund_exc=None):
    svc = StockOverviewService.__new__(StockOverviewService)
    svc.fmp = None
    svc.supabase = None
    svc.price = _Price(quote, quote_exc)

    async def _fund(sym):
        if fund_exc:
            raise fund_exc
        return bundle if bundle is not None else _bundle()

    monkeypatch.setattr(svc, "_get_fundamentals", _fund)

    async def _no_short(sym):
        raise AssertionError("short interest must not be fetched when the bundle has it")

    monkeypatch.setattr(sos, "get_short_interest", _no_short)
    return svc


def _screen_rows(svc, bundle, quote):
    """What the Overview's Key Stats builder prints for the same inputs (label → value)."""
    price = quote.get("price") or 0.0
    _flat, groups = svc._build_key_statistics(
        quote, bundle["profile"], bundle["key_metrics"], bundle["analyst_est"], price,
        shares_float_data=bundle["shares_float"], inst_ownership_data=bundle["inst_ownership"],
        income_quarterly=bundle["income_quarterly"], short_interest=bundle["short_interest"],
        income_annual=bundle["income_annual"], ratios_ttm=bundle.get("ratios_ttm"),
    )
    out = {}
    for g in groups:
        for item in g.statistics:
            out.setdefault(item.label, item.value)
    return out


@pytest.mark.asyncio
async def test_rows_are_byte_equal_to_the_screens_key_stats(monkeypatch):
    svc = _svc(monkeypatch)
    facts = await svc.get_key_facts("aapl")
    screen = _screen_rows(svc, _bundle(), _QUOTE)
    assert facts["ticker"] == "AAPL" and facts["live_price_ok"] is True
    assert set(facts["rows"]) | set(facts["unavailable"]) == set(screen) - sos._KEY_FACTS_INTRADAY
    for label, value in facts["rows"].items():
        assert value == screen[label], label
    for label in ("P/E (TTM)", "P/E (FWD)", "EPS (TTM)", "Market Cap", "Dividends", "Free Float",
                  "Shares Outstanding", "Short % of Float", "Days to Cover"):
        assert label in facts["rows"], label
    assert facts["rows"]["EPS (TTM)"] == "7.47" and facts["rows"]["P/E (TTM)"] == screen["P/E (TTM)"]


@pytest.mark.asyncio
async def test_the_live_trading_day_rows_are_left_to_the_price_tool(monkeypatch):
    facts = await _svc(monkeypatch).get_key_facts("AAPL")
    for label in ("Open", "Previous Close", "Day High", "Day Low", "Volume", "Avg. Volume (3M)"):
        assert label not in facts["rows"] and label not in facts["unavailable"]


@pytest.mark.asyncio
async def test_placeholders_are_never_values(monkeypatch):
    bundle = _bundle(short_interest={"settlement_date": "2026-09-30"}, profile={"companyName": "X"},
                     shares_float={}, key_metrics=[], analyst_est=[], income_quarterly=[])
    facts = await _svc(monkeypatch, bundle=bundle, quote={"price": 10.0}).get_key_facts("X")
    for value in facts["rows"].values():
        assert value not in ("—", "N/A", "", "-")
    for label in ("Short % of Float", "Days to Cover", "Beta", "EPS (TTM)", "P/E (TTM)", "P/E (FWD)"):
        assert label in facts["unavailable"], label


@pytest.mark.parametrize("quote,exc", [(None, RuntimeError("quote 503")), ({}, None),
                                       ({"price": 0}, None), ({"price": float("nan")}, None),
                                       ("garbage", None)])
@pytest.mark.asyncio
async def test_a_failed_quote_serves_fundamentals_only_and_withholds_price_rows(monkeypatch, quote, exc):
    svc = _svc(monkeypatch, quote=quote, quote_exc=exc)
    facts = await svc.get_key_facts("AAPL")
    assert facts["live_price_ok"] is False
    # Market Cap and the 52-week range too (final review 2026-10-09): with no quote they came from
    # the cached profile's `mktCap` / `range`, up to a close cycle old, as current Key Stats.
    for label in ("Market Cap", "52-Week High", "52-Week Low", "P/E (TTM)", "P/E (FWD)", "Dividends"):
        assert label in facts["unavailable"] and label not in facts["rows"], label
    # Fundamentals that need no price are still served.
    assert facts["rows"]["Shares Outstanding"] and facts["rows"]["Beta"] == "1.20"
    assert facts["rows"].get("Dividends") != "None", "a payer is never told 'pays none' for want of a price"


@pytest.mark.asyncio
async def test_a_non_payer_keeps_none_without_a_price(monkeypatch):
    bundle = _bundle(profile={**_bundle()["profile"], "lastDiv": 0})
    facts = await _svc(monkeypatch, bundle=bundle, quote=None,
                       quote_exc=RuntimeError("x")).get_key_facts("AAPL")
    assert facts["rows"]["Dividends"] == "None"


@pytest.mark.asyncio
async def test_a_failed_bundle_raises_for_the_caller_to_degrade(monkeypatch):
    svc = _svc(monkeypatch, fund_exc=RuntimeError("bundle down"))
    with pytest.raises(RuntimeError, match="bundle down"):
        await svc.get_key_facts("AAPL")


@pytest.mark.asyncio
async def test_currencies_are_read_never_assumed(monkeypatch):
    bundle = _bundle(income_annual=[{"date": "2025-12-31", "reportedCurrency": "TWD"},
                                    {"date": "2024-12-31", "reportedCurrency": "USD"}])
    facts = await _svc(monkeypatch, bundle=bundle).get_key_facts("TSM")
    assert facts["statement_currency"] == "TWD" and facts["price_currency"] == "USD"
    # TWD statements, USD price and no FMP multiple in the bundle: never the old USD ÷ TWD P/E.
    assert facts["pe_basis"] == "provider_ttm"
    assert "P/E (TTM)" in facts["unavailable"] and "P/E (FWD)" in facts["unavailable"]
    assert facts["rows"]["EPS (TTM)"] == "TWD 7.47"
    bundle = _bundle(income_annual=[{"date": "2025-12-31"}], balance_annual=[],
                     profile={"companyName": "X", "currency": "US Dollars"})
    facts = await _svc(monkeypatch, bundle=bundle).get_key_facts("X")
    assert facts["statement_currency"] is None and facts["price_currency"] is None
    assert facts["pe_basis"] == "live", "no known statement currency: the behaviour before"


# ── P/E for a filer whose statements are in another currency than its price (2026-10-09) ──

def _tsm_bundle():
    """TSM as FMP answered on 2026-10-09 (tests/fixtures/key_stats_pe), in the bundle's shape."""
    import json
    from pathlib import Path

    rec = json.loads((Path(__file__).parent / "fixtures" / "key_stats_pe" /
                      "fmp_2026_10_09.json").read_text())["TSM"]
    return _bundle(
        profile={**rec["profile"], "companyName": "Taiwan Semiconductor", "beta": 1.1,
                 "lastDividend": 3.49569},
        income_quarterly=rec["income_quarterly"], income_annual=rec["income_annual"],
        balance_annual=[{"date": "2025-12-31", "reportedCurrency": "TWD", "totalDebt": 9.5e11}],
        analyst_est=rec["analyst_est"],
        ratios_ttm=sos._provider_pe_fields(rec["ratios_ttm"]),
    )


_TSM_QUOTE = {"price": 452.69, "marketCap": 2.348e12, "yearHigh": 487.47, "yearLow": 266.82,
              "currency": "USD"}


@pytest.mark.asyncio
async def test_a_foreign_filers_pe_is_the_screens_one_currency_multiple(monkeypatch):
    monkeypatch.setattr(sos, "_utc_today_iso", lambda: "2026-10-09")
    bundle = _tsm_bundle()
    svc = _svc(monkeypatch, bundle=bundle, quote=_TSM_QUOTE)
    facts = await svc.get_key_facts("TSM")
    # $452.69 ÷ 434.95 TWD printed "1.04"; FMP's TTM multiple is 29.56.
    assert facts["rows"]["P/E (TTM)"] == "29.56"
    assert facts["rows"]["P/E (FWD)"] == "23.68"
    assert facts["rows"]["EPS (TTM)"] == "TWD 434.95"
    assert facts["pe_basis"] == "provider_ttm"
    assert facts["statement_currency"] == "TWD" and facts["price_currency"] == "USD"
    screen = _screen_rows(svc, bundle, _TSM_QUOTE)
    for label, value in facts["rows"].items():
        assert value == screen[label], label


@pytest.mark.asyncio
async def test_a_foreign_filer_without_a_live_price_still_withholds_both_multiples(monkeypatch):
    """FMP's multiple needs no live price, but key facts keep ONE rule: no quote, no P/E rows."""
    facts = await _svc(monkeypatch, bundle=_tsm_bundle(), quote=None,
                       quote_exc=RuntimeError("quote 503")).get_key_facts("TSM")
    assert "P/E (TTM)" in facts["unavailable"] and "P/E (FWD)" in facts["unavailable"]
    assert facts["rows"]["EPS (TTM)"] == "TWD 434.95"


@pytest.mark.asyncio
async def test_pe_basis_follows_the_two_currencies(monkeypatch):
    assert (await _svc(monkeypatch).get_key_facts("AAPL"))["pe_basis"] == "live"
    no_ccy = _tsm_bundle()
    no_ccy["profile"] = {k: v for k, v in no_ccy["profile"].items() if k != "currency"}
    quote = {k: v for k, v in _TSM_QUOTE.items() if k != "currency"}
    facts = await _svc(monkeypatch, bundle=no_ccy, quote=quote).get_key_facts("TSM")
    assert facts["pe_basis"] == "currency_unconfirmed" and facts["price_currency"] is None
    assert "P/E (TTM)" in facts["unavailable"], "unconfirmed: never a multiple"
    # The live quote's currency (from the same profile, via the price service) fills the gap.
    facts = await _svc(monkeypatch, bundle=no_ccy, quote=_TSM_QUOTE).get_key_facts("TSM")
    assert facts["pe_basis"] == "provider_ttm" and facts["price_currency"] == "USD"


@pytest.mark.parametrize("raw,expected", [("USD", "USD"), (" twd ", "TWD"), ("US$", None), ("", None),
                                          (None, None), (840, None), ("USDT", None), ("ÜSD", None)])
def test_currency_code(raw, expected):
    assert _currency_code(raw) == expected


@pytest.mark.asyncio
async def test_identity_flags_and_settlement(monkeypatch):
    facts = await _svc(monkeypatch).get_key_facts("AAPL")
    assert facts["company_name"] == "Apple Inc." and facts["is_fund"] is False
    assert facts["country"] == "US" and facts["is_adr"] is False
    assert facts["short_interest_settlement_date"] == "2026-09-30"
    fund = _bundle(profile={"companyName": "SPDR", "isEtf": True})
    assert (await _svc(monkeypatch, bundle=fund).get_key_facts("SPY"))["is_fund"] is True
    truthy_string = _bundle(profile={"companyName": "X", "isEtf": "true"})
    assert (await _svc(monkeypatch, bundle=truthy_string).get_key_facts("X"))["is_fund"] is False


# ── balance-sheet totals ─────────────────────────────────────────────────────────

_AAPL_FY2024 = {"date": "2024-09-28", "fiscalYear": "2024", "reportedCurrency": "USD",
                "totalDebt": 106_629e6, "cashAndCashEquivalents": 29_943e6,
                "cashAndShortTermInvestments": 65_171e6, "netDebt": 76_686e6}


def test_balance_sheet_totals_use_the_newest_row():
    rows = [{"date": "2023-09-30", "totalDebt": 1.0, "cashAndCashEquivalents": 2.0},
            dict(_AAPL_FY2024)]
    out = _balance_sheet_totals(rows)
    assert out == {"total_debt": 106_629e6, "cash_and_cash_equivalents": 29_943e6,
                   "cash_and_short_term_investments": 65_171e6, "net_debt": 76_686e6,
                   "period_end": "2024-09-28", "fiscal_year": "2024", "reported_currency": "USD"}


def test_net_debt_is_fmps_definition_total_debt_minus_cash_and_equivalents():
    """Final review 2026-10-09: ONE definition. FMP's netDebt is totalDebt − cashAndCashEquivalents
    (AAPL FY2024: 106.63B − 29.94B = 76.69B); the old fallback subtracted cash PLUS short-term
    investments (41.46B for the same company), so the field meant two things across companies."""
    with_field = _balance_sheet_totals([dict(_AAPL_FY2024)])
    assert with_field["net_debt"] == with_field["total_debt"] - with_field["cash_and_cash_equivalents"]
    without = dict(_AAPL_FY2024)
    del without["netDebt"]
    fallback = _balance_sheet_totals([without])
    assert fallback["net_debt"] == pytest.approx(with_field["net_debt"]), "the same number either way"
    assert fallback["net_debt"] != pytest.approx(106_629e6 - 65_171e6)


def test_cash_equivalents_never_appear_under_the_short_term_investments_label():
    out = _balance_sheet_totals([{"date": "2025-01-01", "totalDebt": 10.0,
                                  "cashAndCashEquivalents": 4.0}])
    assert "cash_and_short_term_investments" not in out
    assert out["cash_and_cash_equivalents"] == 4.0 and out["net_debt"] == 6.0


def test_no_cash_equivalents_line_means_no_derived_net_debt():
    """Only short-term investments reported, no netDebt: the fallback needs the equivalents line —
    never a figure on the other basis."""
    out = _balance_sheet_totals([{"date": "2025-01-01", "totalDebt": 10.0,
                                  "cashAndShortTermInvestments": 4.0}])
    assert "net_debt" not in out and out["cash_and_short_term_investments"] == 4.0


@pytest.mark.parametrize("row,expected_keys", [
    ({"date": "2025-01-01", "totalDebt": float("nan"), "cashAndShortTermInvestments": 5.0},
     {"cash_and_short_term_investments"}),
    ({"date": "2025-01-01", "totalDebt": True, "cashAndShortTermInvestments": "7"}, set()),
    ({"date": "2025-01-01", "totalDebt": -3.0, "cashAndCashEquivalents": 1.0, "netDebt": None},
     {"total_debt", "cash_and_cash_equivalents", "net_debt"}),
    ({"date": "2025-01-01", "netDebt": float("inf")}, set()),
])
def test_balance_sheet_totals_omit_bad_figures_never_zero(row, expected_keys):
    out = _balance_sheet_totals([row])
    figures = {k for k in ("total_debt", "cash_and_cash_equivalents", "cash_and_short_term_investments",
                           "net_debt") if out and k in out}
    assert figures == expected_keys
    if out:
        for k in figures:
            assert math.isfinite(out[k])
        if "net_debt" in figures and "netDebt" not in row:
            assert out["net_debt"] == -4.0, "negative debt keeps its sign through the subtraction"


@pytest.mark.parametrize("rows", [[], [{}], ["junk"], None])
def test_no_balance_sheet_is_none(rows):
    assert _balance_sheet_totals(rows or []) is None


# ── the short-interest side read ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_missing_short_interest_is_fetched_bounded_and_keeps_warming(monkeypatch):
    monkeypatch.setattr(sos, "_KEY_FACTS_SHORT_WAIT", 0.05)
    svc = _svc(monkeypatch, bundle=_bundle(short_interest={}))
    state = {"done": False}

    async def _slow(sym):
        await asyncio.sleep(0.2)
        state["done"] = True
        return {"shares_short": 1e8, "short_ratio": 2.0}

    monkeypatch.setattr(sos, "get_short_interest", _slow)
    facts = await svc.get_key_facts("AAPL")
    assert "Short % of Float" in facts["unavailable"] and "Days to Cover" in facts["unavailable"]
    assert len(sos._key_facts_side_tasks) >= 1, "held by a strong reference"
    await asyncio.sleep(0.3)
    assert state["done"] is True and not sos._key_facts_side_tasks


@pytest.mark.asyncio
async def test_a_fetched_short_interest_fills_the_rows(monkeypatch):
    svc = _svc(monkeypatch, bundle=_bundle(short_interest={}))

    async def _fast(sym):
        return {"shares_short": 1.48e8, "short_ratio": 2.0, "settlement_date": "2026-09-15"}

    monkeypatch.setattr(sos, "get_short_interest", _fast)
    facts = await svc.get_key_facts("AAPL")
    assert facts["rows"]["Short % of Float"] == "1.00%" and facts["rows"]["Days to Cover"] == "2.00"
    assert facts["short_interest_settlement_date"] == "2026-09-15"


@pytest.mark.asyncio
async def test_a_failing_short_interest_read_degrades_quietly(monkeypatch, caplog):
    svc = _svc(monkeypatch, bundle=_bundle(short_interest=None))

    async def _boom(sym):
        raise RuntimeError("finra down")

    monkeypatch.setattr(sos, "get_short_interest", _boom)
    facts = await svc.get_key_facts("AAPL")
    assert "Short % of Float" in facts["unavailable"]
    await asyncio.sleep(0)
    assert any("short interest read failed for AAPL" in r.getMessage() for r in caplog.records)


# ── the shared Short % of Float rule ─────────────────────────────────────────────

@pytest.mark.parametrize("si,float_shares,km,expected", [
    ({"shares_short": 1e8}, 1e9, None, 10.0),                              # primary
    ({"shares_short": 1e8, "short_percent_of_float": 3.3}, 0, None, 3.3),   # fallback 1 (no float)
    ({"shares_short": 0, "short_percent_of_float": 3.3}, 1e9, None, 3.3),
    ({}, 1e9, [{"shortPercentFloat": 0.042}], 4.2),                         # fallback 2, fraction
    ({}, 1e9, [{"shortPercentOutstanding": 5.5}], 5.5),                     # fallback 2, percent
    ({}, -5, [], None), (None, None, None, None), ("junk", 1e9, None, None),
])
def test_short_percent_of_float(si, float_shares, km, expected):
    got = short_percent_of_float(si, float_shares, km)
    assert (got is None and expected is None) or got == pytest.approx(expected)


# ── `degraded`: an empty slice a company always has did not load (fix round, 2026-10-08) ──────

@pytest.mark.asyncio
async def test_a_complete_bundle_is_not_degraded(monkeypatch):
    assert (await _svc(monkeypatch).get_key_facts("AAPL"))["degraded"] == []


@pytest.mark.parametrize("over,expected", [
    # `_get_fundamentals` folds an FMP outage into an empty profile and empty slices.
    ({"profile": {}, "key_metrics": [], "income_quarterly": [], "balance_annual": []},
     ["profile", "key_metrics", "quarterly_income", "balance_sheet"]),
    ({"profile": None}, ["profile"]),
    ({"balance_annual": []}, ["balance_sheet"]),
    ({"balance_annual": "garbage"}, ["balance_sheet"]),
    ({"income_quarterly": []}, ["quarterly_income"]),
    ({"key_metrics": [None, 7]}, ["key_metrics"]),
])
@pytest.mark.asyncio
async def test_empty_slices_are_named(monkeypatch, over, expected):
    facts = await _svc(monkeypatch, bundle=_bundle(**over)).get_key_facts("AAPL")
    assert facts["degraded"] == expected
    if "balance_sheet" in expected:
        assert facts["balance_sheet"] is None


@pytest.mark.asyncio
async def test_a_fund_with_no_statements_is_not_degraded(monkeypatch):
    profile = {"companyName": "SPDR S&P 500 ETF Trust", "currency": "USD", "isEtf": True, "isFund": False}
    bundle = _bundle(profile=profile, key_metrics=[], income_quarterly=[], balance_annual=[],
                     income_annual=[])
    facts = await _svc(monkeypatch, bundle=bundle).get_key_facts("SPY")
    assert facts["is_fund"] is True and facts["degraded"] == []
