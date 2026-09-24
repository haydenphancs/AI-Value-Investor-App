"""CEO Buys — the 4th App-Exclusive Signal (TestFlight 1.0(6), home E2).

Tester: *"Add Insider buys in App-Exclusive Signals. Filter only CEO? And name it CEO buys?"*
Developer decisions (2026-09-23): CEO / co-CEO open-market purchases only, ranked by DOLLARS
bought (one CEO per company, so a buyer count is degenerate), same Pro lock as the other
three cards; a build in which any card RAISED is never persisted to the 24 h tier.

Live shape this is built against (probe 2026-09-23, last 30 days): 2,031 P rows, 1,684 on
common stock, 263 CEO rows across 132 tickers, top GME $46.8M / FOX $10.3M / UBER $10.0M,
most rows token $5-20K buys on micro-caps — hence the $100K floor and the quote gate.

Category 1 (pure) + service tests with injected fakes — no network, no Supabase.
"""
import asyncio
import json
import math
import time
from datetime import datetime, timezone

import pytest

import app.services.signals_service as ssvc
from app.integrations.fmp import (
    FMPNotEntitledException,
    FMPPartialPageException,
    FMPUnavailableException,
)
from app.schemas.home_dashboard import SignalGroupResponse, SignalRowResponse, SignalsGroupResponse
from app.services._insider_common import ceo_role_label, is_ceo_role, is_common_stock
from app.services.signals_service import (
    _aggregate_ceo_buys,
    _ceo_price_plausible,
    _extract_ceo_buys,
    _rank_ceo_buys,
)
from _price_fakes import PriceFromFMPFake

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _r(**over):
    """One qualifying CEO buy: $2.5M of GME common, filed 3 days ago."""
    row = {
        "symbol": "GME",
        "filingDate": "2026-09-20 16:00:00",
        "transactionDate": "2026-09-18",
        "transactionType": "P-Purchase",
        "acquisitionOrDisposition": "A",
        "formType": "4",
        "typeOfOwner": "director, officer: Chief Executive Officer",
        "securityName": "Class A Common Stock",
        "securitiesTransacted": 100_000,
        "price": 25.0,
        "reportingCik": "0001",
        "reportingName": "COHEN RYAN",
        "directOrIndirect": "D",
    }
    row.update(over)
    return row


def _syms(group):
    return [e.symbol for e in group.entries] if group else []


# ── 1. Predicates ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,ok", [
    ("officer: Chief Executive Officer", True),
    ("director, officer: Chief Executive Officer", True),
    ("director, 10 percent owner, officer: President and CEO", True),   # _role_rank got this wrong
    ("officer: CEO", True),
    ("officer: Co-CEO", True),
    ("officer: Co-Chief Executive Officer", True),
    ("officer: Chairman & CEO", True),
    ("officer: Interim CEO", True),
    ("officer: Chief Financial Officer", False),
    ("officer: Chief Operating Officer", False),
    ("director", False),
    ("10 percent owner", False),
    ("director, Former CEO", False),
    ("officer: CEOs Office Manager", False),
    ("", False), (None, False), (7, False), (["CEO"], False),
    # Live titles (3,000 P rows, 2026-09-23) that must stay CEOs.
    ("director, officer: President, CEO and Chairman", True),
    ("officer: Executive Chair, CEO & Pres.", True),
    ("officer: CEO/CFO", True), ("officer: Interim CEO & CFO", True),
    ("officer: Vice Chairman and CEO", True),          # "Vice" is the chairman, not the CEO
    ("officer: CEO, President and Director", True),    # a bare comma is not a segment
    ("officer: President & CEO ASRV & Bank", True),
    ("officer: Chief Executive Officer of the Company", True),
    ("director, officer, other: President & CEO", True),  # officer FLAG, title in other:
    ("officer: CEO, other: Chairman", True),
    # The adversarial review's cases (2026-09-23): not the sitting company CEO.
    ("director, other: Retired CEO", False), ("director, other: Ex CEO", False),
    ("director, other: Previous Chief Executive Officer", False),
    ("director, other: Spouse of CEO", False),
    # Only the OFFICER title counts: the Officer box was not checked, so a bare "CEO" in the
    # free-text Other field is not evidence (isolates the officer-only rule from the others).
    ("director, other: CEO", False), ("10 percent owner, other: Chief Executive Officer", False),
    ("officer: Chief of Staff to the CEO", False), ("officer: EVP, Office of the CEO", False),
    ("officer: Deputy CEO", False), ("officer: Vice CEO", False),
    ("officer: CEO, Consumer & Community Banking", False),
    ("officer: President & CEO of Subsidiary Bank", False),
    ("officer: EVP & Chief Executive Officer - Europe", False),
    ("officer: President & CEO Elect", False), ("officer: Former Chief Executive Officer", False),
    ("officer: Retired CEO", False), ("officer: CEO Emeritus", False),
])
def test_ceo_title_variants(title, ok):
    assert is_ceo_role(title) is ok


@pytest.mark.parametrize("title,label", [
    ("director, officer: Chief Executive Officer", "Chief Executive Officer"),
    ("director, 10 percent owner, officer: President and CEO", "President and CEO"),
    ("officer:   Co-CEO  ", "Co-CEO"),
    ("officer: Chief Financial Officer", "CEO"),   # not the CEO title → generic
    ("CEO", "CEO"), (None, "CEO"), ("officer: " + "Chief Executive Officer " * 5, None),
    ("director, officer, other: President & CEO", "President & CEO"),
    ("officer: CEO, other: Chairman", "CEO"),
    ("director, other: Retired CEO", "CEO"),   # never used as a label: is_ceo_role gates first
])
def test_ceo_role_label(title, label):
    got = ceo_role_label(title)
    if label is None:
        assert got.startswith("Chief Executive Officer") and len(got) <= 60
    else:
        assert got == label


@pytest.mark.parametrize("name,ok", [
    ("Common Stock", True), ("Class A Common Stock", True), ("Common Shares", True),
    ("Ordinary Shares", True), ("Common Units", True),
    ("Series A Preferred Stock", False), ("Warrants to purchase Common Stock", False),
    ("Convertible Senior Notes", False), ("Stock Option (right to buy Common Stock)", False),
    ("Rights", False), ("Restricted Stock Units", False), ("", False), (None, False), (3, False),
])
def test_common_stock_only(name, ok):
    assert is_common_stock(name) is ok


def test_price_plausibility_band():
    assert _ceo_price_plausible(25.0, 24.0)
    assert _ceo_price_plausible(2.6, 25.0) and _ceo_price_plausible(249, 25.0)
    assert not _ceo_price_plausible(2.4, 25.0)       # cents-for-dollars style unit error
    assert not _ceo_price_plausible(260, 25.0)
    for ref in (None, 0, -5, float("nan"), float("inf"), "x"):
        assert _ceo_price_plausible(25.0, ref), f"no usable reference {ref!r} → cannot judge → keep"
    for bad in (None, 0, -1, float("nan"), float("inf")):
        assert not _ceo_price_plausible(bad, 25.0)


# ── 2. Extraction: filters ─────────────────────────────────────────────────────────

def test_empty_none_and_non_list_inputs():
    for bad in ([], None, "rows", {"a": 1}, 3):
        assert _extract_ceo_buys(bad, now=NOW) == []
        assert _aggregate_ceo_buys(bad, now=NOW) is None


def test_malformed_rows_are_skipped_not_fatal():
    rows = ["x", None, 3, [], _r(typeOfOwner=None), _r(typeOfOwner=5), _r(typeOfOwner=["CEO"]),
            _r(securityName=None), _r(symbol=None), _r(symbol=123), _r()]
    buys = _extract_ceo_buys(rows, now=NOW)
    assert len(buys) == 1 and buys[0].symbol == "GME"


def test_all_non_ceo_rows_return_none():
    rows = [_r(typeOfOwner="director"), _r(typeOfOwner="officer: CFO"),
            _r(typeOfOwner="10 percent owner")]
    assert _aggregate_ceo_buys(rows, now=NOW) is None


@pytest.mark.parametrize("tx", ["S-Sale", "A-Award", "M-Exempt", "G-Gift", "F-InKind", "", None, 5])
def test_non_open_market_purchases_are_ignored(tx):
    assert _extract_ceo_buys([_r(transactionType=tx)], now=NOW) == []


def test_disposition_and_form5_are_ignored_but_missing_fields_are_tolerated():
    assert _extract_ceo_buys([_r(acquisitionOrDisposition="D")], now=NOW) == []
    assert _extract_ceo_buys([_r(formType="5")], now=NOW) == []
    assert len(_extract_ceo_buys([_r(acquisitionOrDisposition=None, formType=None)], now=NOW)) == 1


def test_only_common_stock_counts():
    rows = [_r(securityName="Series B Preferred Stock"), _r(securityName="Warrants"),
            _r(securityName="Common Stock", reportingCik="0002")]
    assert len(_extract_ceo_buys(rows, now=NOW)) == 1


def test_dotted_class_shares_fold_into_the_dash_form():
    rows = [_r(symbol="BRK.B", reportingCik="1"), _r(symbol="BRK-B", reportingCik="2"),
            _r(symbol="BRK.A", reportingCik="3")]
    group = _aggregate_ceo_buys(rows, now=NOW)
    assert _syms(group) == ["BRK-B", "BRK-A"]
    assert group.entries[0].value == pytest.approx(5_000_000.0)


@pytest.mark.parametrize("sym", ["NASDAQ: XYZ", "TOOLONGSYMBOL", "", "--", "N/A", "1ABC", "AB CD", "A.B.C"])
def test_garbage_symbols_are_rejected(sym):
    assert _extract_ceo_buys([_r(symbol=sym)], now=NOW) == []


def test_lowercase_and_padded_symbols_are_canonicalised():
    assert _extract_ceo_buys([_r(symbol="  gme ")], now=NOW)[0].symbol == "GME"


@pytest.mark.parametrize("field,value", [
    ("securitiesTransacted", 0), ("securitiesTransacted", -5), ("securitiesTransacted", float("nan")),
    ("securitiesTransacted", float("inf")), ("securitiesTransacted", "lots"), ("securitiesTransacted", None),
    ("price", 0), ("price", -1), ("price", float("nan")), ("price", "n/a"), ("price", None),
])
def test_bad_shares_or_price_skip_the_row(field, value):
    assert _extract_ceo_buys([_r(**{field: value})], now=NOW) == []


def test_a_purchase_larger_than_the_resulting_holding_is_a_unit_error():
    """The review's repro: 150,000,000 shares typed for 150,000 kept the $25 price, passed
    the price band, and led the card with "$3.75B bought"."""
    assert _extract_ceo_buys([_r(securitiesTransacted=150_000_000, securitiesOwned=4_000_000)], now=NOW) == []
    assert len(_extract_ceo_buys([_r(securitiesTransacted=150_000, securitiesOwned=4_000_000)], now=NOW)) == 1
    assert len(_extract_ceo_buys([_r(securitiesTransacted=150_000, securitiesOwned=150_000)], now=NOW)) == 1
    for owned in (None, 0, "n/a", float("nan")):     # unknown holding → cannot judge → keep
        assert len(_extract_ceo_buys([_r(securitiesOwned=owned)], now=NOW)) == 1, owned


def test_numeric_strings_are_accepted():
    buys = _extract_ceo_buys([_r(securitiesTransacted="1000", price="12.5")], now=NOW)
    assert buys and buys[0].dollars == 12_500.0


def test_dollar_overflow_and_garbage_bound():
    assert _extract_ceo_buys([_r(securitiesTransacted=1e308, price=1e308)], now=NOW) == []
    assert _extract_ceo_buys([_r(securitiesTransacted=1_000_000_000, price=10.0)], now=NOW) == []  # $10B


def test_a_real_billion_dollar_ceo_buy_is_kept_and_leads():
    rows = [_r(symbol="TSLA", securitiesTransacted=2_570_000, price=389.0, reportingCik="9"),
            _r()]
    group = _aggregate_ceo_buys(rows, now=NOW)
    assert _syms(group) == ["TSLA", "GME"]
    assert group.entries[0].value == pytest.approx(999_730_000.0)


# ── 3. Extraction: dates ──────────────────────────────────────────────────────────

def test_window_is_on_the_filing_date_inclusive_30_days():
    in_ = _r(filingDate="2026-08-24", transactionDate="2026-08-22")      # 30 days → in
    out = _r(filingDate="2026-08-23", transactionDate="2026-08-21", reportingCik="2")  # 31 → out
    assert [b.filing_date for b in _extract_ceo_buys([in_, out], now=NOW)] == ["2026-08-24"]


def test_future_skew_two_days_kept_three_dropped():
    ok = _r(filingDate="2026-09-25", transactionDate="2026-09-23")
    bad = _r(filingDate="2026-09-26", transactionDate="2026-09-24", reportingCik="2")
    assert [b.filing_date for b in _extract_ceo_buys([ok, bad], now=NOW)] == ["2026-09-25"]


def test_a_stale_trade_filed_late_is_not_this_months_buying():
    late = _r(filingDate="2026-09-20", transactionDate="2026-07-01")     # 81-day lag
    edge = _r(filingDate="2026-09-20", transactionDate="2026-08-21", reportingCik="2")  # 30 → in
    assert [b.reporter for b in _extract_ceo_buys([late, edge], now=NOW)] == ["cik:2"]


def test_a_trade_dated_after_its_filing_is_dropped():
    assert _extract_ceo_buys([_r(filingDate="2026-09-20", transactionDate="2026-09-22")], now=NOW) == []
    assert len(_extract_ceo_buys([_r(filingDate="2026-09-20", transactionDate="2026-09-21")], now=NOW)) == 1


def test_a_missing_filing_date_skips_the_row_no_keep_all_fallback():
    for bad in (None, "", "N/A", "2026-13-40"):
        assert _extract_ceo_buys([_r(filingDate=bad)], now=NOW) == []


def test_an_unparseable_trade_date_keeps_the_row_without_a_trade_date():
    buys = _extract_ceo_buys([_r(transactionDate="soon")], now=NOW)
    assert len(buys) == 1 and buys[0].transaction_date == ""


# ── 4. Extraction: identity + de-duplication ─────────────────────────────────────

def test_reporter_falls_back_to_the_name_then_skips():
    by_name = _extract_ceo_buys([_r(reportingCik=None)], now=NOW)
    assert by_name and by_name[0].reporter == "name:ryan cohen"
    assert _extract_ceo_buys([_r(reportingCik=None, reportingName="")], now=NOW) == []
    assert _extract_ceo_buys([_r(reportingCik="None", reportingName=None)], now=NOW) == []


def test_the_same_line_on_two_filings_counts_once():
    a = _r(filingDate="2026-09-19")
    b = _r(filingDate="2026-09-21")          # same trade, same size/price, re-reported
    buys = _extract_ceo_buys([a, b], now=NOW)
    assert len(buys) == 1 and buys[0].filing_date == "2026-09-19"


def test_identical_lines_on_one_filing_are_separate_fills():
    buys = _extract_ceo_buys([_r(), _r()], now=NOW)
    assert len(buys) == 2
    assert _aggregate_ceo_buys([_r(), _r()], now=NOW).entries[0].value == pytest.approx(5_000_000.0)


def test_distinct_fills_on_one_day_all_count():
    rows = [_r(securitiesTransacted=1000, price=25.0), _r(securitiesTransacted=2000, price=25.1),
            _r(securitiesTransacted=3000, price=24.9)]
    group = _aggregate_ceo_buys(rows, now=NOW)
    assert group.entries[0].value == pytest.approx(25_000 + 50_200 + 74_700)


def test_an_amendment_supersedes_the_original():
    original = _r(securitiesTransacted=10_000, formType="4", filingDate="2026-09-19")
    amended = _r(securitiesTransacted=12_000, formType="4/A", filingDate="2026-09-21")
    buys = _extract_ceo_buys([original, amended], now=NOW)
    assert [(b.shares, b.form_type) for b in buys] == [(12_000, "4/A")]


def test_the_latest_of_two_amendments_wins():
    rows = [_r(securitiesTransacted=10_000, formType="4", filingDate="2026-09-18"),
            _r(securitiesTransacted=11_000, formType="4/A", filingDate="2026-09-19"),
            _r(securitiesTransacted=12_000, formType="4/A", filingDate="2026-09-21")]
    assert [b.shares for b in _extract_ceo_buys(rows, now=NOW)] == [12_000]


def test_a_partial_amendment_adding_an_omitted_line_keeps_the_originals():
    """The review's repro: a 4/A that adds ONE omitted fill used to wipe the day's real buys
    ($3.8M → $51K) and drop the ticker off the card."""
    rows = [_r(securitiesTransacted=100_000, price=25.0, filingDate="2026-09-19"),
            _r(securitiesTransacted=50_000, price=26.0, filingDate="2026-09-19"),
            _r(securitiesTransacted=2_000, price=25.5, filingDate="2026-09-21", formType="4/A")]
    shares = sorted(b.shares for b in _extract_ceo_buys(rows, now=NOW))
    assert shares == [2_000, 50_000, 100_000]
    assert _aggregate_ceo_buys(rows, now=NOW).entries[0].value == pytest.approx(2_500_000 + 1_300_000 + 51_000)


def test_a_partial_amendment_correcting_one_line_replaces_only_that_line():
    rows = [_r(securitiesTransacted=100_000, price=25.0, filingDate="2026-09-19"),
            _r(securitiesTransacted=50_000, price=26.0, filingDate="2026-09-19"),
            _r(securitiesTransacted=100_000, price=25.1, filingDate="2026-09-21", formType="4/A")]
    got = sorted((b.shares, b.price, b.form_type) for b in _extract_ceo_buys(rows, now=NOW))
    assert got == [(50_000, 26.0, "4"), (100_000, 25.1, "4/A")]


def test_an_amendment_only_replaces_its_own_trade_day():
    rows = [_r(transactionDate="2026-09-15", filingDate="2026-09-17", securitiesTransacted=1000),
            _r(transactionDate="2026-09-18", filingDate="2026-09-19", securitiesTransacted=2000),
            _r(transactionDate="2026-09-18", filingDate="2026-09-21", securitiesTransacted=2500, formType="4/A")]
    shares = sorted(b.shares for b in _extract_ceo_buys(rows, now=NOW))
    assert shares == [1000, 2500]


def test_direct_and_indirect_holdings_both_count():
    rows = [_r(directOrIndirect="D"), _r(directOrIndirect="I")]
    assert len(_extract_ceo_buys(rows, now=NOW)) == 2


def test_co_ceos_sum_into_one_ticker():
    rows = [_r(reportingCik="1", typeOfOwner="officer: Co-CEO"),
            _r(reportingCik="2", typeOfOwner="officer: Co-Chief Executive Officer", reportingName="ROE JANE")]
    group = _aggregate_ceo_buys(rows, now=NOW)
    assert len(group.entries) == 1 and group.entries[0].value == pytest.approx(5_000_000.0)


# ── 5. Ranking ───────────────────────────────────────────────────────────────────────

def test_ranks_by_total_dollars_with_contiguous_ranks():
    rows = [_r(symbol="UBER", securitiesTransacted=100_000, price=100.0, reportingCik="3"),   # $10.0M
            _r(symbol="GME", securitiesTransacted=1_870_800, price=25.0, reportingCik="1"),  # $46.77M
            _r(symbol="FOX", securitiesTransacted=205_400, price=50.0, reportingCik="2")]    # $10.27M
    group = _aggregate_ceo_buys(rows, now=NOW)
    assert _syms(group) == ["GME", "FOX", "UBER"]
    assert [e.rank for e in group.entries] == [1, 2, 3]
    assert group.kind == "ceo"


def test_the_100k_floor_boundary():
    below = _r(symbol="AAA", securitiesTransacted=1, price=99_999.99, reportingCik="1")
    at = _r(symbol="BBB", securitiesTransacted=1, price=100_000.0, reportingCik="2")
    assert _syms(_aggregate_ceo_buys([below, at], now=NOW)) == ["BBB"]


def test_floor_is_on_the_ticker_total_not_the_row():
    rows = [_r(securitiesTransacted=2000, price=30.0, reportingCik="1"),     # $60K
            _r(securitiesTransacted=2000, price=30.0, reportingCik="2")]     # $60K → $120K total
    assert _syms(_aggregate_ceo_buys(rows, now=NOW)) == ["GME"]


def test_ties_break_on_latest_filing_then_symbol():
    rows = [_r(symbol="ZZZ", filingDate="2026-09-20", reportingCik="1"),
            _r(symbol="AAA", filingDate="2026-09-20", reportingCik="2"),
            _r(symbol="MMM", filingDate="2026-09-22", transactionDate="2026-09-21", reportingCik="3")]
    assert _syms(_aggregate_ceo_buys(rows, now=NOW)) == ["MMM", "AAA", "ZZZ"]


def test_as_of_is_the_latest_qualifying_filing_over_all_buys():
    rows = [_r(filingDate="2026-09-15", transactionDate="2026-09-14"),
            _r(symbol="TINY", securitiesTransacted=1, price=10.0, reportingCik="2",
               filingDate="2026-09-22", transactionDate="2026-09-22")]       # below the floor
    group = _aggregate_ceo_buys(rows, now=NOW)
    assert _syms(group) == ["GME"] and group.as_of_date == "2026-09-22"


def test_top_n_truncates():
    rows = [_r(symbol=f"T{i:02d}"[:4].replace("0", "Q"), reportingCik=str(i),
               securitiesTransacted=100_000 + i) for i in range(15)]
    group = _aggregate_ceo_buys(rows, now=NOW, top_n=10)
    assert len(group.entries) == 10


def test_names_are_filled_only_from_the_supplied_map():
    buys = _extract_ceo_buys([_r()], now=NOW)
    assert _rank_ceo_buys(buys).entries[0].name == ""
    assert _rank_ceo_buys(buys, names={"GME": "GameStop Corp."}).entries[0].name == "GameStop Corp."


def test_payload_is_strict_json_safe():
    group = _aggregate_ceo_buys([_r(), _r(symbol="FOX", reportingCik="2", price=float("nan"))], now=NOW)
    blob = SignalsGroupResponse(ceo=group).model_dump_json()
    json.loads(blob)
    assert "NaN" not in blob and "Infinity" not in blob
    assert all(math.isfinite(e.value) for e in group.entries)


# ── 6. The build (fake FMP + quotes) ────────────────────────────────────────────────

class _FakeFMP:
    def __init__(self, rows=None, quotes=None, raise_on_fetch=None):
        self.rows = rows or []
        self.quotes = quotes or {}
        self.raise_on_fetch = raise_on_fetch
        self.calls = []

    async def get_insider_trades_since(self, since_date, **kwargs):
        self.calls.append((since_date, kwargs))
        if self.raise_on_fetch is not None:
            raise self.raise_on_fetch
        return self.rows

    async def get_batch_quotes_bulk(self, symbols):
        return [self.quotes[s] for s in symbols if s in self.quotes]

    async def get_company_profile(self, sym):
        q = self.quotes.get(sym) or {}
        return {"companyName": q.get("name", ""), "price": q.get("price"), "marketCap": q.get("marketCap")}


def _q(sym, *, price=25.0, cap=5e9, exch="NYSE", name=None):
    return {"symbol": sym, "price": price, "marketCap": cap, "exchange": exch, "name": name or f"{sym} Inc."}


def _svc(fake):
    s = ssvc.SignalsService()
    s.fmp = fake  # type: ignore[assignment]
    s.price = PriceFromFMPFake(fake)
    return s


def _today_rows():
    """Fresh rows relative to the real clock (the build reads datetime.now)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return today


@pytest.mark.asyncio
async def test_build_ceo_gates_exchange_and_cap_and_fills_names():
    d = _today_rows()
    rows = [_r(symbol="GME", filingDate=d, transactionDate=d, reportingCik="1"),
            _r(symbol="OTCX", filingDate=d, transactionDate=d, reportingCik="2"),
            _r(symbol="TINY", filingDate=d, transactionDate=d, reportingCik="3")]
    fake = _FakeFMP(rows, {"GME": _q("GME", name="GameStop Corp."), "OTCX": _q("OTCX", exch="OTC"),
                           "TINY": _q("TINY", cap=50e6, exch="NASDAQ")})
    g = await _svc(fake)._build_ceo()
    assert _syms(g) == ["GME"] and g.entries[0].name == "GameStop Corp." and g.kind == "ceo"


@pytest.mark.asyncio
async def test_build_ceo_requests_p_purchase_since_30_days_market_wide():
    fake = _FakeFMP([_r(typeOfOwner="director")], {})      # non-empty, nothing qualifies
    before = (datetime.now(timezone.utc) - ssvc.timedelta(days=30)).strftime("%Y-%m-%d")
    assert await _svc(fake)._build_ceo() is None
    after = (datetime.now(timezone.utc) - ssvc.timedelta(days=30)).strftime("%Y-%m-%d")
    since, kwargs = fake.calls[0]
    assert since in {before, after}        # bracketed: a UTC midnight mid-test cannot flake it
    assert kwargs["transaction_type"] == "P-Purchase"
    assert "symbol" not in kwargs or kwargs["symbol"] is None


@pytest.mark.asyncio
async def test_an_empty_market_wide_feed_is_an_outage_not_an_empty_card():
    """30 market-wide days are never empty (~2,000 rows live): `[]` must mark the build
    degraded, not persist "no CEO bought anything" for 24 h."""
    with pytest.raises(FMPUnavailableException):
        await _svc(_FakeFMP([], {}))._build_ceo()


@pytest.mark.asyncio
async def test_a_row_worth_more_than_a_tenth_of_the_company_is_dropped():
    d = _today_rows()
    rows = [_r(symbol="GME", filingDate=d, transactionDate=d, securitiesTransacted=40_000_000,
               price=25.0, reportingCik="1"),                                   # $1B vs $5B cap
            _r(symbol="FOX", filingDate=d, transactionDate=d, reportingCik="2")]  # $2.5M
    fake = _FakeFMP(rows, {"GME": _q("GME", price=25.0, cap=5e9), "FOX": _q("FOX", price=25.0, cap=5e9)})
    assert _syms(await _svc(fake)._build_ceo()) == ["FOX"]


@pytest.mark.asyncio
async def test_price_band_drops_a_unit_error_and_reranks():
    d = _today_rows()
    rows = [_r(symbol="GME", filingDate=d, transactionDate=d, price=2500.0, reportingCik="1"),  # $250M, ×100 off
            _r(symbol="FOX", filingDate=d, transactionDate=d, price=50.0, reportingCik="2")]    # $5M
    fake = _FakeFMP(rows, {"GME": _q("GME", price=25.0), "FOX": _q("FOX", price=50.0)})
    g = await _svc(fake)._build_ceo()
    assert _syms(g) == ["FOX"]


@pytest.mark.asyncio
async def test_a_fetch_failure_propagates_rather_than_reading_as_empty():
    fake = _FakeFMP(raise_on_fetch=FMPPartialPageException(
        "x", endpoint="insider-trading/search", pages_total=2, pages_failed=1))
    with pytest.raises(FMPPartialPageException):
        await _svc(fake)._build_ceo()


@pytest.mark.asyncio
async def test_not_entitled_is_an_honest_none():
    fake = _FakeFMP(raise_on_fetch=FMPNotEntitledException("402"))
    assert await _svc(fake)._build_ceo() is None


@pytest.mark.asyncio
async def test_candidates_with_zero_quotes_is_an_outage_not_an_empty_card():
    d = _today_rows()
    fake = _FakeFMP([_r(filingDate=d, transactionDate=d)], quotes={})
    with pytest.raises(FMPUnavailableException):
        await _svc(fake)._build_ceo()


@pytest.mark.asyncio
async def test_none_when_nothing_clears_the_gate():
    d = _today_rows()
    fake = _FakeFMP([_r(filingDate=d, transactionDate=d)], {"GME": _q("GME", cap=10e6)})
    assert await _svc(fake)._build_ceo() is None


# ── 7. The cache rule: a degraded build is never persisted ─────────────────────────

def _group(kind, sym="NVDA"):
    return SignalGroupResponse(kind=kind, entries=[SignalRowResponse(rank=1, symbol=sym, value=3.0)])


@pytest.fixture
def fresh_cache():
    ssvc.SignalsService._cache.clear()
    ssvc.SignalsService._inflight.clear()
    ssvc.SignalsService._degraded_keys.clear()
    yield
    ssvc.SignalsService._cache.clear()
    ssvc.SignalsService._inflight.clear()
    ssvc.SignalsService._degraded_keys.clear()


@pytest.mark.asyncio
async def test_build_marks_the_steps_that_raised(fresh_cache):
    s = ssvc.SignalsService()

    async def ok_c():
        return _group("congress")

    async def raise_():
        raise FMPUnavailableException("down")

    async def none_():
        return None

    s._build_congress, s._build_whale, s._build_earnings, s._build_ceo = ok_c, none_, raise_, raise_  # type: ignore
    result, failed = await s._build()
    assert failed == frozenset({"earnings", "ceo"})
    assert result.congress is not None and result.ceo is None and result.whale is None


@pytest.mark.asyncio
async def test_a_degraded_build_is_served_from_memory_briefly_and_never_persisted(monkeypatch, fresh_cache):
    s = ssvc.SignalsService()
    writes = []
    monkeypatch.setattr(s, "_read_supabase_cache", lambda: None)
    monkeypatch.setattr(s, "_write_supabase_cache", lambda r: writes.append(r))
    builds = {"n": 0}

    async def degraded():
        builds["n"] += 1
        return SignalsGroupResponse(congress=_group("congress")), frozenset({"ceo"})

    monkeypatch.setattr(s, "_build", degraded)
    r = await s.get_signals()
    assert r.congress is not None and writes == []
    assert ssvc._SIGNALS_CACHE_KEY in ssvc.SignalsService._degraded_keys

    await s.get_signals()                        # still inside 5 min → memory
    assert builds["n"] == 1

    ts, cached = ssvc.SignalsService._cache[ssvc._SIGNALS_CACHE_KEY]
    ssvc.SignalsService._cache[ssvc._SIGNALS_CACHE_KEY] = (ts - ssvc._SIGNALS_DEGRADED_TTL_SECONDS - 1, cached)
    await s.get_signals()                        # past the SHORT ttl (well inside 45 min) → rebuild
    assert builds["n"] == 2


@pytest.mark.asyncio
async def test_a_healthy_build_persists_and_clears_the_degraded_mark(monkeypatch, fresh_cache):
    s = ssvc.SignalsService()
    writes = []
    ssvc.SignalsService._degraded_keys.add(ssvc._SIGNALS_CACHE_KEY)
    monkeypatch.setattr(s, "_read_supabase_cache", lambda: None)
    monkeypatch.setattr(s, "_write_supabase_cache", lambda r: writes.append(r))

    async def healthy():
        return SignalsGroupResponse(ceo=_group("ceo", "GME")), frozenset()

    monkeypatch.setattr(s, "_build", healthy)
    r = await s.get_signals()
    assert r.ceo.entries[0].symbol == "GME"
    assert len(writes) == 1
    assert ssvc._SIGNALS_CACHE_KEY not in ssvc.SignalsService._degraded_keys


@pytest.mark.asyncio
async def test_a_ceo_only_build_counts_as_a_build_with_groups(monkeypatch, fresh_cache):
    """The old `congress or whale or earnings` check would have dropped a CEO-only build."""
    s = ssvc.SignalsService()
    monkeypatch.setattr(s, "_read_supabase_cache", lambda: None)
    monkeypatch.setattr(s, "_write_supabase_cache", lambda r: None)

    async def ceo_only():
        return SignalsGroupResponse(ceo=_group("ceo", "GME")), frozenset()

    monkeypatch.setattr(s, "_build", ceo_only)
    await s.get_signals()
    assert ssvc._SIGNALS_CACHE_KEY in ssvc.SignalsService._cache


@pytest.mark.asyncio
async def test_congress_and_whale_failures_now_raise_so_the_build_knows(monkeypatch):
    s = ssvc.SignalsService()

    class _Congress:
        async def get_senate_latest(self, n):
            raise FMPPartialPageException("x", endpoint="senate-latest", pages_total=4, pages_failed=1)

        async def get_house_latest(self, n):
            return []

    s.fmp = _Congress()  # type: ignore[assignment]
    with pytest.raises(FMPPartialPageException):
        await s._build_congress()

    def _boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(ssvc, "get_supabase", _boom)
    with pytest.raises(RuntimeError):
        await s._build_whale()


def test_the_cache_key_was_bumped_for_the_new_card():
    assert ssvc._SIGNALS_CACHE_KEY == "signals_v4"
    assert ssvc._SIGNAL_STEPS == ("congress", "whale", "earnings", "ceo")
    assert set(ssvc._SIGNAL_STEPS) == set(SignalsGroupResponse.model_fields)


# ── 8. The drill-down ───────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_detail():
    ssvc.SignalsService._detail_cache.clear()
    ssvc.SignalsService._detail_inflight.clear()
    yield
    ssvc.SignalsService._detail_cache.clear()
    ssvc.SignalsService._detail_inflight.clear()


@pytest.mark.asyncio
async def test_detail_groups_by_ceo_and_trade_day_and_matches_the_card(fresh_detail):
    d = _today_rows()
    rows = [
        _r(filingDate=d, transactionDate=d, securitiesTransacted=1000, price=25.0),
        _r(filingDate=d, transactionDate=d, securitiesTransacted=3000, price=25.2),  # same CEO, same day
        _r(filingDate=d, transactionDate=d, reportingCik="2", reportingName="ROE JANE",
           typeOfOwner="officer: Co-CEO", securitiesTransacted=500, price=25.0),
        _r(filingDate=d, transactionDate=d, typeOfOwner="officer: CFO", reportingCik="3"),  # not a CEO
        _r(symbol="FOX", filingDate=d, transactionDate=d, reportingCik="4"),                # other ticker
    ]
    fake = _FakeFMP(rows, {"GME": _q("GME", price=25.0)})
    s = _svc(fake)
    detail = await s.get_ticker_detail("ceo", "gme")
    assert detail.kind == "ceo" and detail.symbol == "GME"
    names = [h.name for h in detail.holders]
    assert names == ["Ryan Cohen", "Jane Roe"]                     # $100.6K before $12.5K
    ryan = detail.holders[0]
    assert ryan.amount_est == pytest.approx(25_000 + 75_600)
    assert ryan.shares == 4000 and ryan.whale_id is None and ryan.action == "BOUGHT"
    assert ryan.subtitle == "Chief Executive Officer" and detail.holders[1].subtitle == "Co-CEO"
    assert ryan.transaction_date == d and ryan.disclosure_date == d
    since, kwargs = fake.calls[0]
    assert kwargs["symbol"] == "GME" and kwargs["transaction_type"] == "P-Purchase"
    card = _aggregate_ceo_buys([r for r in rows if r["symbol"] == "GME"], now=datetime.now(timezone.utc))
    assert sum(h.amount_est for h in detail.holders) == pytest.approx(card.entries[0].value)


@pytest.mark.asyncio
async def test_detail_applies_the_price_band(fresh_detail):
    d = _today_rows()
    rows = [_r(filingDate=d, transactionDate=d, price=2500.0)]
    detail = await _svc(_FakeFMP(rows, {"GME": _q("GME", price=25.0)})).get_ticker_detail("ceo", "GME")
    assert detail.holders == []


class _NoProfileFMP(_FakeFMP):
    async def get_company_profile(self, sym):
        raise FMPUnavailableException("profile down")


@pytest.mark.asyncio
async def test_detail_falls_back_to_the_quote_when_the_profile_fails(fresh_detail):
    """The profile price is the plausibility band's reference here, not decoration: with it
    gone the band was OFF and the unit-error row the card rejected was listed — and cached."""
    d = _today_rows()
    rows = [_r(filingDate=d, transactionDate=d, price=2500.0, reportingCik="1"),     # ×100 off
            _r(filingDate=d, transactionDate=d, price=25.0, reportingCik="2", reportingName="ROE JANE")]
    detail = await _svc(_NoProfileFMP(rows, {"GME": _q("GME", price=25.0)})).get_ticker_detail("ceo", "GME")
    assert [h.name for h in detail.holders] == ["Jane Roe"]


@pytest.mark.asyncio
async def test_detail_without_any_reference_price_is_not_cached(fresh_detail):
    d = _today_rows()
    detail = await _svc(_NoProfileFMP([_r(filingDate=d, transactionDate=d)], {})).get_ticker_detail("ceo", "GME")
    assert detail.holders == []
    assert "ceo:GME" not in ssvc.SignalsService._detail_cache


@pytest.mark.asyncio
async def test_detail_applies_the_market_cap_share(fresh_detail):
    d = _today_rows()
    rows = [_r(filingDate=d, transactionDate=d, securitiesTransacted=40_000_000, price=25.0)]   # $1B
    detail = await _svc(_FakeFMP(rows, {"GME": _q("GME", price=25.0, cap=5e9)})).get_ticker_detail("ceo", "GME")
    assert detail.holders == []


def test_the_detail_page_is_large_enough_for_a_busy_ticker():
    """100 × 5 overflowed on a name with many insider buys and emptied the drill-down."""
    assert ssvc._CEO_DETAIL_PAGE_SIZE * ssvc._CEO_DETAIL_MAX_PAGES >= 5_000


@pytest.mark.asyncio
async def test_a_detail_failure_is_not_cached(fresh_detail):
    fake = _FakeFMP(raise_on_fetch=FMPUnavailableException("down"), quotes={"GME": _q("GME")})
    detail = await _svc(fake).get_ticker_detail("ceo", "GME")
    assert detail.holders == []
    assert "ceo:GME" not in ssvc.SignalsService._detail_cache, "a failure must not pin an empty screen"


@pytest.mark.asyncio
async def test_an_honest_empty_detail_is_cached(fresh_detail):
    detail = await _svc(_FakeFMP([], {"GME": _q("GME")})).get_ticker_detail("ceo", "GME")
    assert detail.holders == []
    assert "ceo:GME" in ssvc.SignalsService._detail_cache
