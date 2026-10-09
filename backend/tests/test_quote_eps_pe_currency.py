"""GET /stocks/{ticker}/quote's ``eps`` / ``pe`` (2026-10-09).

THE BUG: `get_stock_quote` summed the four newest `period=quarter` EPS rows and divided the live
price by the sum. The price is in the TRADING currency (USD for an ADR), the statements in the
REPORTING one, so TSM sent pe ≈ $452.69 ÷ 434.95 TWD = 1.04 (true ~29.6). It also summed four
half-years for a half-year filer (BHP: two years of earnings), dropped a negative EPS
(`ttm_eps > 0`) and read a break-even 0.0 as absent (`or`). iOS reads both keys in the detail
screen's FALLBACK Key Stats (TickerDetailViewModel), which prints EPS with no currency code and
P/E only when > 0.

The fix reuses the Overview's helpers (`_ttm_eps`, `key_stats_currencies`, `pe_basis`). Owner
decisions 2026-10-09: two currencies → FMP's TTM multiple (one cached `ratios-ttm` call) and no
EPS; a negative or 0.00 EPS is sent, with no P/E.

FMP answers are RECORDED (tests/fixtures/key_stats_pe/fmp_2026_10_09.json); hermetic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from app.api.v1.endpoints import stocks
from app.services import stock_overview_service as S
from app.services.stock_overview_service import (
    PE_BASIS_LIVE,
    PE_BASIS_PROVIDER,
    PE_BASIS_UNCONFIRMED,
    StockOverviewService,
    _provider_pe_fields,
    quote_eps_pe,
)

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "key_stats_pe" / "fmp_2026_10_09.json").read_text())
_FOREIGN = ["TSM", "BABA", "HDB", "NVO", "SONY", "TM", "ASML", "SAP"]
_TSM_FIELDS = _provider_pe_fields(_FIXTURE["TSM"]["ratios_ttm"])


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    """The module cache and in-flight slots are process-global: each test starts empty."""
    monkeypatch.setattr(S, "_cache", {})
    monkeypatch.setattr(S, "_quote_pe_ratios_inflight", {})


class _FakeFMP:
    """The four FMP calls the quote path makes, answered from one recorded filer."""

    def __init__(self, rec, *, ratios=None, ratios_exc=None, delay=0.0):
        self.rec = rec
        self.ratios = rec.get("ratios_ttm") if ratios is None else ratios
        self.ratios_exc = ratios_exc
        self.delay = delay
        self.ratios_calls = 0

    async def get_income_statement(self, ticker, period="annual", limit=10):
        assert period == "quarter"
        return self.rec["income_quarterly"][:limit]

    async def get_shares_float(self, ticker):
        return {}

    async def get_company_profile(self, ticker):
        return dict(self.rec["profile"])

    async def get_ratios_ttm(self, ticker):
        self.ratios_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.ratios_exc is not None:
            raise self.ratios_exc
        return self.ratios


def _svc(fmp):
    svc = StockOverviewService.__new__(StockOverviewService)
    svc.fmp = fmp
    return svc


def _quote_of(profile):
    q = {"symbol": profile.get("symbol"), "price": profile.get("price")}
    if profile.get("currency"):
        q["currency"] = profile["currency"]
    return q


async def _valuation(ticker, rec=None, *, fmp=None, profile=None, quote=None, rows=None):
    rec = _FIXTURE[ticker] if rec is None else rec
    profile = rec["profile"] if profile is None else profile
    quote = _quote_of(profile) if quote is None else quote
    fmp = fmp or _FakeFMP(rec)
    svc = _svc(fmp)
    out = await svc.quote_valuation(
        ticker, price=quote.get("price"), quote=quote, profile=profile,
        income_quarterly=rec["income_quarterly"] if rows is None else rows,
    )
    return out, fmp


def _usd_rows(eps_values, ccy="USD"):
    dates = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]
    return [{"date": d, "reportedCurrency": ccy, "epsDiluted": e} for d, e in zip(dates, eps_values)]


_USD_PROFILE = {"symbol": "XYZ", "price": 50.0, "currency": "USD"}


# ── 1. The pure rule ──────────────────────────────────────────────────────────────────

def test_one_currency_sends_eps_and_price_over_eps():
    assert quote_eps_pe(100.0, 4.0, PE_BASIS_LIVE) == {"eps": 4.0, "pe": 25.0}


@pytest.mark.parametrize("eps", [-7.98, 0.0, -0.01])
def test_a_loss_or_break_even_sends_eps_and_no_pe(eps):
    """Owner 2026-10-09: the EPS is data; a P/E of a loss is not a multiple anyone reads."""
    out = quote_eps_pe(100.0, eps, PE_BASIS_LIVE)
    assert out == {"eps": eps} and "pe" not in out


@pytest.mark.parametrize("price", [None, 0, 0.0, -5.0, float("nan"), float("inf"), True, "abc"])
def test_an_unusable_price_keeps_eps_and_omits_pe(price):
    assert quote_eps_pe(price, 4.0, PE_BASIS_LIVE) == {"eps": 4.0}


@pytest.mark.parametrize("eps", [None, float("nan"), float("inf"), float("-inf")])
def test_no_finite_eps_sends_nothing(eps):
    for basis in (PE_BASIS_LIVE, PE_BASIS_PROVIDER, PE_BASIS_UNCONFIRMED):
        assert quote_eps_pe(100.0, eps, basis, _TSM_FIELDS) == {}


def test_a_near_zero_eps_never_sends_an_infinite_pe():
    out = quote_eps_pe(1e300, 5e-324, PE_BASIS_LIVE)
    assert out == {"eps": 5e-324}, "the overflowed division is omitted, never 'inf'"


def test_two_currencies_send_fmps_multiple_and_no_eps():
    out = quote_eps_pe(452.69, 434.95, PE_BASIS_PROVIDER, _TSM_FIELDS)
    assert out == {"pe": 29.56}, "no bare TWD EPS beside a $ price"


@pytest.mark.parametrize("ratios, why", [
    (None, "the ratios-ttm leg failed"),
    ({}, "FMP answered no row"),
    ({"priceToEarningsRatioTTM": 29.56}, "no FMP EPS to check against"),
    ({"priceToEarningsRatioTTM": 29.56, "netIncomePerShareTTM": 300.0}, "FMP's EPS is >15% off"),
    ({"priceToEarningsRatioTTM": -29.56, "netIncomePerShareTTM": -431.37}, "FMP's sign disagrees"),
])
def test_an_unusable_provider_multiple_is_omitted(ratios, why):
    assert quote_eps_pe(452.69, 434.95, PE_BASIS_PROVIDER, ratios) == {}, why


def test_two_currencies_and_a_loss_send_nothing():
    sony = _provider_pe_fields(_FIXTURE["SONY"]["ratios_ttm"])
    assert quote_eps_pe(24.185, -37.38, PE_BASIS_PROVIDER, sony) == {}


def test_the_price_currency_unknown_sends_nothing():
    assert quote_eps_pe(452.69, 434.95, PE_BASIS_UNCONFIRMED, _TSM_FIELDS) == {}


# ── 2. The recorded filers, through the service ───────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("t", _FOREIGN)
async def test_a_foreign_filer_gets_fmps_multiple_never_price_over_eps(t):
    rec = _FIXTURE[t]
    out, fmp = await _valuation(t)
    assert "eps" not in out, "a reporting-currency EPS is never sent bare"
    fmp_pe = rec["ratios_ttm"][0]["priceToEarningsRatioTTM"]
    if fmp_pe > 0:
        assert out == {"pe": round(fmp_pe, 2)}
        assert fmp.ratios_calls == 1
    else:
        assert out == {} and fmp.ratios_calls == 0, "a loss-maker abroad needs no ratios-ttm call"
    old_eps = round(sum(q["epsDiluted"] for q in rec["income_quarterly"]), 2)
    old_pe = round(rec["profile"]["price"] / old_eps, 2)
    assert out.get("pe") != old_pe, "the cross-currency division must never come back"


@pytest.mark.asyncio
async def test_tsm_pinned():
    out, _ = await _valuation("TSM")
    assert out == {"pe": 29.56}, "was 1.04 ($452.69 ÷ 434.95 TWD)"


@pytest.mark.asyncio
async def test_bhp_half_years_are_one_year_not_two():
    """BHP's four 'quarterly' rows are half-years: the TTM is the newest two (1.66 + 2.22)."""
    out, fmp = await _valuation("BHP")
    assert out == {"eps": 3.88, "pe": round(85.79 / 3.88, 2)}
    assert out["eps"] != 7.44 and fmp.ratios_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("t", ["AAPL", "INFY"])
async def test_a_one_currency_filer_keeps_the_live_division(t):
    rec = _FIXTURE[t]
    out, fmp = await _valuation(t)
    eps = round(sum(q["epsDiluted"] for q in rec["income_quarterly"]), 2)
    assert out == {"eps": eps, "pe": round(rec["profile"]["price"] / eps, 2)}
    assert fmp.ratios_calls == 0, "no ratios-ttm call when the currencies agree"


@pytest.mark.asyncio
async def test_a_us_loss_maker_sends_its_negative_eps():
    out, _ = await _valuation("XYZ", {"profile": _USD_PROFILE,
                                      "income_quarterly": _usd_rows([-2.1, -1.9, -2.0, -1.98])})
    assert out == {"eps": -7.98}


@pytest.mark.asyncio
async def test_a_break_even_quarter_is_a_value_not_a_gap():
    """`epsDiluted: 0.0` used to fall through `or` to `eps` and then to 0 — now read as 0.0."""
    rows = _usd_rows([0.0, 0.0, 0.0, 0.0])
    for r in rows:
        r["eps"] = 9.99            # the old `or` chain would have read this
    out, _ = await _valuation("XYZ", {"profile": _USD_PROFILE, "income_quarterly": rows})
    assert out == {"eps": 0.0}


@pytest.mark.asyncio
async def test_the_price_currency_unknown_sends_nothing_and_fetches_nothing():
    rec = _FIXTURE["TSM"]
    profile = {k: v for k, v in rec["profile"].items() if k != "currency"}
    out, fmp = await _valuation("TSM", profile=profile, quote={"price": profile["price"]})
    assert out == {} and fmp.ratios_calls == 0


@pytest.mark.asyncio
async def test_the_quotes_currency_stands_in_for_a_missing_profile():
    """A failed profile leg degrades to {}; the quote's own `currency` still names the price's."""
    out, _ = await _valuation("TSM", profile={}, quote=_quote_of(_FIXTURE["TSM"]["profile"]))
    assert out == {"pe": 29.56}


@pytest.mark.asyncio
@pytest.mark.parametrize("rows, why", [
    (_usd_rows([1.0, 1.0, 1.0, 1.0])[:3] + [{"date": "2024-06-30", "reportedCurrency": "USD",
                                              "epsDiluted": 1.0}], "a missing quarter"),
    (_usd_rows([1.0, 1.0, 1.0])[:2] * 2, "duplicate rows"),
    (_usd_rows([1.0, 1.0]), "only two quarters"),
    ([], "the statements leg failed"),
    ([{"date": "2026-06-30", "reportedCurrency": "USD", "epsDiluted": None}] * 4, "four copies of one row, no EPS"),
    (_usd_rows([1.0, 1.0, 1.0]) + [{"date": "2025-09-30", "reportedCurrency": "USD",
                                     "epsDiluted": "n/a"}], "a non-numeric EPS"),
])
async def test_rows_that_are_not_a_ttm_send_nothing(rows, why):
    out, _ = await _valuation("XYZ", {"profile": _USD_PROFILE, "income_quarterly": rows})
    assert out == {}, why


@pytest.mark.asyncio
async def test_rows_in_two_currencies_are_never_summed():
    rows = _usd_rows([1.0, 1.0, 1.0, 1.0])
    rows[3]["reportedCurrency"] = "EUR"
    out, _ = await _valuation("XYZ", {"profile": _USD_PROFILE, "income_quarterly": rows})
    assert out == {}


# ── 3. The ratios-ttm leg: cached, deduplicated, never cached on failure ──────────────

@pytest.mark.asyncio
async def test_repeated_polls_make_one_ratios_call():
    fmp = _FakeFMP(_FIXTURE["TSM"])
    for _ in range(5):
        out, _ = await _valuation("TSM", fmp=fmp)
        assert out == {"pe": 29.56}
    assert fmp.ratios_calls == 1


@pytest.mark.asyncio
async def test_concurrent_polls_share_one_ratios_call():
    fmp = _FakeFMP(_FIXTURE["TSM"], delay=0.02)
    outs = await asyncio.gather(*[_valuation("TSM", fmp=fmp) for _ in range(6)])
    assert all(o == {"pe": 29.56} for o, _ in outs)
    assert fmp.ratios_calls == 1
    assert S._quote_pe_ratios_inflight == {}, "the in-flight slot is released"


@pytest.mark.asyncio
async def test_the_cache_expires(monkeypatch):
    fmp = _FakeFMP(_FIXTURE["TSM"])
    await _valuation("TSM", fmp=fmp)
    now = S.time.time()
    monkeypatch.setattr(S.time, "time", lambda: now + S._QUOTE_PE_RATIOS_TTL + 1)
    await _valuation("TSM", fmp=fmp)
    assert fmp.ratios_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fmp_kwargs, why", [
    ({"ratios_exc": RuntimeError("FMP 503")}, "the call raised"),
    ({"ratios": {"Error Message": "limit"}}, "FMP answered an error dict"),
])
async def test_a_failed_leg_omits_pe_logs_and_is_not_cached(fmp_kwargs, why, caplog):
    caplog.set_level(logging.WARNING, logger=S.__name__)
    fmp = _FakeFMP(_FIXTURE["TSM"], **fmp_kwargs)
    out, _ = await _valuation("TSM", fmp=fmp)
    assert out == {}, why
    assert any("[quote-pe-provider-leg]" in r.getMessage() and "TSM" in r.getMessage()
               for r in caplog.records), "never silently"
    await _valuation("TSM", fmp=fmp)
    assert fmp.ratios_calls == 2, "a failure is retried on the next poll, never cached"


@pytest.mark.asyncio
async def test_fmps_answered_empty_list_is_cached():
    fmp = _FakeFMP(_FIXTURE["TSM"], ratios=[])
    for _ in range(3):
        out, _ = await _valuation("TSM", fmp=fmp)
        assert out == {}
    assert fmp.ratios_calls == 1


# ── 4. The endpoint ───────────────────────────────────────────────────────────────────

class _FakePrice:
    def __init__(self, quote):
        self.quote = quote

    async def get_quote(self, ticker):
        return dict(self.quote)


def _wire(monkeypatch, rec, *, svc_exc=None):
    fmp = _FakeFMP(rec)
    monkeypatch.setattr(stocks, "price_source", lambda owner=None: _FakePrice(_quote_of(rec["profile"])))
    monkeypatch.setattr(stocks, "get_fmp_client", lambda: fmp)
    svc = _svc(fmp)
    if svc_exc is not None:
        async def _boom(*a, **k):
            raise svc_exc
        svc.quote_valuation = _boom
    monkeypatch.setattr(stocks, "get_stock_overview_service", lambda: svc)
    return fmp


@pytest.mark.asyncio
@pytest.mark.parametrize("t, eps, pe", [
    ("TSM", None, 29.56),
    ("BHP", 3.88, round(85.79 / 3.88, 2)),
    ("AAPL", 8.73, round(333.2 / 8.73, 2)),
    ("SONY", None, None),
])
async def test_the_endpoint_sends_the_services_eps_and_pe(monkeypatch, t, eps, pe):
    _wire(monkeypatch, _FIXTURE[t])
    resp = await stocks.get_stock_quote(t)
    assert resp.get("eps") == eps and resp.get("pe") == pe
    assert ("eps" in resp) == (eps is not None) and ("pe" in resp) == (pe is not None), (
        "an omitted value is an ABSENT key (iOS decodes nil), never a null or a 0")
    assert resp["price"] == _FIXTURE[t]["profile"]["price"]
    json.dumps(resp, allow_nan=False)


@pytest.mark.asyncio
async def test_a_valuation_failure_never_fails_the_quote(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=stocks.__name__)
    _wire(monkeypatch, _FIXTURE["TSM"], svc_exc=RuntimeError("boom"))
    resp = await stocks.get_stock_quote("TSM")
    assert resp["price"] == 452.69 and "pe" not in resp and "eps" not in resp
    assert any("EPS/P/E failed for TSM" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_failed_statements_leg_is_logged_and_omits_both(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=stocks.__name__)
    fmp = _wire(monkeypatch, _FIXTURE["AAPL"])

    async def _fail(*a, **k):
        raise RuntimeError("FMP 500")
    fmp.get_income_statement = _fail
    resp = await stocks.get_stock_quote("AAPL")
    assert "pe" not in resp and "eps" not in resp and resp["price"] == 333.2
    assert any("income_quarterly" in r.getMessage() and "AAPL" in r.getMessage()
               for r in caplog.records), "a failed leg is never swallowed silently"


def test_the_endpoint_no_longer_divides_by_its_own_eps_sum():
    """Anti-regression on the code, comments stripped: no inline TTM sum or division."""
    import inspect
    import re

    src = inspect.getsource(stocks.get_stock_quote)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())
    assert "epsDiluted" not in code and "ttm_eps" not in code
    assert "quote_valuation(" in code
