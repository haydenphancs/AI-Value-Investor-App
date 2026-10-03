"""
The per-fetch insider rules shared by the report, the Holders tab and the alerts
(`_insider_common`: `is_equity_line`, `filter_issuer_rows`, `supersede_form4_amendments`,
`prepare_insider_rows`, `insider_window_cutoff`) and their wiring (2026-10-03).

The defect that started it: NYAX files every Form 4 as "Ordinary Shares". The report table
and the Holders chart + list kept a row only when its security name contained "common
stock", so the Ticker Report read "Buys 0 / Neutral" while the Home CEO Buys card (which
uses `is_common_stock`) listed the CEO's ~$8.8M of purchases. The audit behind the fix also
found Form 4/A amendments counted twice, other issuers' rows in the per-symbol feed
(BRK-B read "Net Buying $212.9M"), and a fail-open fetch cached as "no insider activity".

Rows below are shaped like the live NYAX / BRK-B FMP rows; dates are relative to now (every
builder windows on the trailing 365 days). No network, no Supabase.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.fmp import (
    EmptyAfterFailure,
    FMPClient,
    FMPNotEntitledException,
    FMPPartialPageException,
)
from app.services import holders_service as hs
from app.services._insider_common import (
    filter_issuer_rows,
    insider_row_date,
    insider_window_cutoff,
    is_equity_line,
    issuer_roster,
    prepare_insider_rows,
    supersede_form4_amendments,
)
from app.services.agents import ticker_report_data_collector as trdc
from app.services.agents.ticker_report_data_collector import (
    _build_insider_sections,
    _role_rank,
    _settle_holders_result,
    _settle_pass1_result,
)
from app.services.holders_service import HoldersService
from app.services.signals_service import _extract_ceo_buys

NYAX_CIK = "0001901279"
_CEO = ("NECHMAD YAIR", "0001903011", "officer: CEO, Co Founder & Chairman")
_CTO = ("BEN-AVI DAVID", "0001903020", "director, officer: CTO and Co Founder")
_CFO = ("MANOR SAGIT", "0001903030", "officer: CFO")


def _day(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _row(who, shares, price, traded, filed, *, tx="P-Purchase", form="4", url="acc-1",
         security="Ordinary Shares", cik=NYAX_CIK, symbol="NYAX", own="D"):
    name, reporter_cik, title = who
    return {
        "symbol": symbol, "companyCik": cik, "reportingName": name,
        "reportingCik": reporter_cik, "typeOfOwner": title, "transactionType": tx,
        "securityName": security, "securitiesTransacted": shares, "price": price,
        "transactionDate": traded, "filingDate": filed, "formType": form, "url": url,
        "directOrIndirect": own, "acquisitionOrDisposition": "A" if tx.startswith("P") else "D",
    }


def _lines(rows):
    return sorted((r["securitiesTransacted"], r["price"], r["transactionDate"]) for r in rows)


# ── is_equity_line ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["", "   ", None, 123, 4.5])
def test_an_unlabeled_row_is_equity_on_both_modes(name):
    """Form 3 holdings rows arrive blank; the per-ticker surfaces always counted them, and
    a number must not crash the old `.lower()` path."""
    assert is_equity_line(name) is True
    assert is_equity_line(name, strict=False) is True


@pytest.mark.parametrize("name,strict,alert", [
    ("Ordinary Shares", True, True),
    ("Common Stock", True, True),
    ("Class C Capital Stock", True, True),
    ("American Depositary Shares", False, True),     # off the share charts, on the alerts
    ("Class A Limited Voting Shares", True, True),
    ("Exchangeable Shares", False, True),            # unknown equity label: alerts keep it
    ("7.00% Subordinated Convertible Notes due 2031", False, False),
    ("Series A Mandatorily Convertible Preferred Stock", False, False),
    ("Common Stock Purchase Warrant", False, False),
    ("Stock Option (Right to Buy)", False, False),
])
def test_strict_vs_alert_modes(name, strict, alert):
    assert is_equity_line(name) is strict
    assert is_equity_line(name, strict=False) is alert


# ── the window ──────────────────────────────────────────────────────────────────────

def test_the_cutoff_is_one_date_string_and_a_filing_time_is_ignored():
    now = datetime(2026, 10, 3, 23, 59, tzinfo=timezone.utc)
    assert insider_window_cutoff(now) == "2025-10-03"
    assert insider_row_date({"filingDate": "2026-09-22 16:05:00"}) == "2026-09-22"
    assert insider_row_date({"transactionDate": "2026-09-21", "filingDate": "2026-09-22"}) == "2026-09-21"
    assert insider_row_date({"transactionDate": None, "filingDate": 7}) == ""


def test_a_trade_on_the_cutoff_day_is_counted_by_the_table_and_the_chart():
    """Two sites compared a datetime carrying the current time of day, two compared the date
    string: a trade dated exactly on the cutoff day was in the list and the summary card but
    not in the bars or the report's table."""
    edge = insider_window_cutoff()
    rows = [_row(_CEO, 1000, 40.0, edge, edge)]
    _, vital = _build_insider_sections(rows)
    chart = HoldersService._build_insider_smart_money(object.__new__(HoldersService), rows, {}, None)
    assert vital["buy_count"] == 1
    assert sum(p.buy_volume for p in chart.flow_data) == pytest.approx(0.001)


# ── filter_issuer_rows ──────────────────────────────────────────────────────────────

def test_other_issuers_rows_are_dropped_and_counted():
    """BRK-B's per-symbol feed carries Berkshire's 10%-owner purchases of OTHER companies'
    stock (companyCik 0000920760): counted, they made the report read "Net Buying $212.9M"
    against $0.5M bought / $20M sold by Berkshire's own insiders."""
    own = _row(_CFO, 100, 500.0, _day(10), _day(9), cik="0001067983", symbol="BRK-B")
    foreign = [_row(("BERKSHIRE HATHAWAY INC", "0001067983", "10 percent owner"), 1_000_000,
                    80.0, _day(10), _day(9), cik="0000920760", symbol="BRK-B",
                    security="Class A Common Stock") for _ in range(3)]
    kept, dropped = filter_issuer_rows([own, *foreign], "1067983")   # zero padding ignored
    assert kept == [own]
    assert dropped == {"920760": 3}


@pytest.mark.parametrize("issuer", [None, "", "n/a", 0, True])
def test_an_unknown_issuer_keeps_every_row(issuer):
    rows = [_row(_CFO, 1, 1.0, _day(1), _day(1), cik="0000000042")]
    assert filter_issuer_rows(rows, issuer) == (rows, {})


def test_a_row_without_a_company_cik_is_kept_and_junk_is_skipped():
    blank = _row(_CFO, 1, 1.0, _day(1), _day(1), cik=None)
    kept, dropped = filter_issuer_rows([blank, "junk", None], 1901279)   # an int CIK works
    assert kept == [blank] and dropped == {}
    assert filter_issuer_rows("not a list", NYAX_CIK) == ([], {})


def test_the_roster_drops_other_issuers_and_dedupes_by_person():
    roster = [
        {"owner": "BERKSHIRE HATHAWAY INC", "title": "10 percent owner", "companyCik": "0000920760"},
        {"owner": "ABEL GREGORY", "title": "director", "companyCik": "0001067983"},
        {"owner": "Abel, Gregory", "title": "director", "companyCik": None},   # same person
    ]
    out = issuer_roster(roster, "0001067983")
    assert [r["owner"] for r in out] == ["ABEL GREGORY"]


# ── supersede_form4_amendments ──────────────────────────────────────────────────────

def test_the_nyax_cto_4a_is_counted_once():
    """Live shape: the CTO's 4/A filed 09-29 repeats three lines of his 09-24 Form 4 and adds
    500 @ 44.0302. Counted as filed, his buys read 6,222 shares; filed truthfully, 3,868."""
    d22, d23, d24, d17 = _day(11), _day(10), _day(9), _day(16)
    f17, f24, f29 = _day(15), _day(9), _day(4)
    rows = [
        _row(_CTO, 500, 44.0302, d24, f29, form="4/A", url="acc-873"),
        _row(_CTO, 100, 42.0906, d24, f29, form="4/A", url="acc-873"),
        _row(_CTO, 700, 43.0726, d23, f29, form="4/A", url="acc-873"),
        _row(_CTO, 1554, 43.182, d22, f29, form="4/A", url="acc-873"),
        _row(_CTO, 100, 42.0906, d24, f24, url="acc-864"),
        _row(_CTO, 700, 43.0726, d23, f24, url="acc-864"),
        _row(_CTO, 1554, 43.182, d22, f24, url="acc-864"),
        _row(_CTO, 1014, 42.9098, d17, f17, url="acc-850"),
    ]
    kept = supersede_form4_amendments(rows)
    assert sum(r["securitiesTransacted"] for r in kept) == 3868
    assert len(kept) == 5
    assert [r for r in rows if r in kept] == kept, "input order (newest first) must hold"


def test_a_full_restatement_replaces_a_corrected_sale():
    """Live shape (NYAX, June): a 4/A corrected a 13,596-share sale to 12,180 and repeated
    the next day's line — both originals counted on top of it inflated sells by 60%."""
    rows = [
        _row(_CTO, 12180, 74.94, _day(120), _day(115), tx="S-Sale", form="4/A", url="acc-547"),
        _row(_CTO, 16590, 76.48, _day(119), _day(115), tx="S-Sale", form="4/A", url="acc-547"),
        _row(_CTO, 13596, 74.93, _day(120), _day(118), tx="S-Sale", url="acc-518"),
        _row(_CTO, 16590, 76.48, _day(119), _day(118), tx="S-Sale", url="acc-518"),
    ]
    kept = supersede_form4_amendments(rows)
    assert sorted(r["securitiesTransacted"] for r in kept) == [12180, 16590]
    assert all(r["formType"] == "4/A" for r in kept)


def test_a_partial_amendment_replaces_only_the_line_it_corrects():
    day, filed = _day(30), _day(29)
    rows = [
        _row(_CFO, 200, 11.5, day, _day(20), form="4/A", url="acc-2"),   # corrects 200 @ 11
        _row(_CFO, 100, 10.0, day, filed, url="acc-1"),
        _row(_CFO, 200, 11.0, day, filed, url="acc-1"),
    ]
    kept = supersede_form4_amendments(rows)
    assert sorted((r["securitiesTransacted"], r["price"]) for r in kept) == [(100, 10.0), (200, 11.5)]


def test_the_latest_of_two_amendments_wins():
    day = _day(40)
    rows = [
        _row(_CFO, 300, 12.0, day, _day(30), form="4/A", url="acc-3"),
        _row(_CFO, 250, 12.0, day, _day(35), form="4/A", url="acc-2"),
        _row(_CFO, 200, 12.0, day, _day(39), url="acc-1"),
    ]
    assert [r["securitiesTransacted"] for r in supersede_form4_amendments(rows)] == [300]


def test_identical_lines_count_once_across_filings_but_all_count_on_one_filing():
    day = _day(5)
    two_filings = [_row(_CEO, 1000, 45.0, day, _day(4), url="acc-1"),
                   _row(_CEO, 1000, 45.0, day, _day(3), url="acc-2")]
    kept = supersede_form4_amendments(two_filings)
    assert len(kept) == 1 and kept[0]["url"] == "acc-1"   # the earliest filing
    fills = [_row(_CEO, 1000, 45.0, day, _day(4), url="acc-1") for _ in range(3)]
    assert len(supersede_form4_amendments(fills)) == 3     # separate fills on one filing


def test_different_transaction_types_on_one_day_are_not_merged():
    """Keying on the classification would merge an A-Award with an M-Exempt (both
    "Uninformative Buy") and the identical-line rule could then drop a real row."""
    day = _day(7)
    rows = [_row(_CFO, 5000, 0.0, day, _day(6), tx="A-Award", url="acc-1"),
            _row(_CFO, 5000, 0.0, day, _day(5), tx="M-Exempt", url="acc-2"),
            _row(_CFO, 5000, 40.0, day, _day(6), tx="P-Purchase", url="acc-1"),
            _row(_CFO, 5000, 40.0, day, _day(6), tx="S-Sale", url="acc-1")]
    assert len(supersede_form4_amendments(rows)) == 4


def test_a_reworded_label_on_the_amendment_still_supersedes():
    """NYAX filings say both "Ordinary Shares" and "Ordinary shares"."""
    day = _day(8)
    rows = [_row(_CFO, 90, 20.0, day, _day(2), form="4/A", url="acc-2", security="Ordinary shares"),
            _row(_CFO, 99, 20.0, day, _day(7), url="acc-1", security="Ordinary Shares")]
    assert [r["securitiesTransacted"] for r in supersede_form4_amendments(rows)] == [90]


def test_share_classes_stay_apart():
    day = _day(8)
    rows = [_row(_CFO, 10, 700000.0, day, _day(2), form="4/A", url="acc-2", security="Class A Common Stock"),
            _row(_CFO, 1500, 470.0, day, _day(7), url="acc-1", security="Class B Common Stock")]
    assert len(supersede_form4_amendments(rows)) == 2


def test_rows_it_cannot_judge_pass_through_untouched():
    day = _day(3)
    no_reporter = {"transactionType": "P-Purchase", "securitiesTransacted": 10, "price": 1.0,
                   "transactionDate": day, "formType": "4/A"}
    form3 = _row(_CFO, 0, 0.0, day, day, tx="", form="3", security="")
    form5 = _row(_CFO, 100, 5.0, day, day, tx="S-Sale", form="5")
    nan_shares = _row(_CFO, float("nan"), 5.0, day, day, form="4/A")
    rows = [no_reporter, form3, form5, nan_shares, "junk"]
    assert supersede_form4_amendments(rows) == [no_reporter, form3, form5, nan_shares]
    assert supersede_form4_amendments(None) == []


def test_a_missing_form_type_and_filing_read_as_one_original_filing():
    """Most hand-built fixtures (and some FMP rows) carry neither: identical lines must then
    stay separate fills, exactly as before the rule existed."""
    day = _day(2)
    rows = [{"reportingName": "SMITH JOHN", "transactionType": "P-Purchase",
             "securitiesTransacted": 1000, "price": 100.0, "transactionDate": day}] * 2
    assert len(supersede_form4_amendments(rows)) == 2


def test_the_generic_rule_keeps_the_same_ceo_lines_as_the_ceo_card():
    """`signals_service._extract_ceo_buys` (the Home CEO Buys card) keeps its own tested
    pipeline; on CEO purchase rows both rules must keep the same lines."""
    d1, d2, d3 = _day(9), _day(5), _day(3)
    rows = [
        _row(_CEO, 104511, 44.0884, d1, d1, url="acc-a"),
        _row(_CEO, 8000, 44.71, d2, _day(4), url="acc-b"),
        _row(_CEO, 1000, 45.4865, d2, _day(4), url="acc-b"),
        _row(_CEO, 8000, 44.71, d2, _day(4), url="acc-b"),        # a second fill, same filing
        _row(_CEO, 9616, 46.386, d3, _day(1), url="acc-c"),
        _row(_CEO, 9600, 46.39, d3, _day(0), form="4/A", url="acc-d"),   # corrects acc-c
    ]
    generic = supersede_form4_amendments(rows)
    card = _extract_ceo_buys(rows)
    assert sorted((b.shares, b.price) for b in card) == sorted(
        (float(r["securitiesTransacted"]), float(r["price"])) for r in generic
    )


# ── prepare_insider_rows ────────────────────────────────────────────────────────────

def test_the_pipeline_filters_before_superseding():
    """An option-exercise line of the same size cannot be taken for an amendment of the
    stock line: derivative rows are gone before supersession runs."""
    day = _day(6)
    rows = [
        _row(_CFO, 1500, 0.0, day, _day(1), tx="M-Exempt", form="4/A", url="acc-2",
             security="Stock Option (Right to Buy)"),
        _row(_CFO, 1500, 21.35, day, _day(5), tx="M-Exempt", url="acc-1"),
        _row(_CFO, 9, 9.0, day, _day(5), cik="0000000777"),
    ]
    kept, dropped = prepare_insider_rows(rows, NYAX_CIK)
    assert [r["url"] for r in kept] == ["acc-1"] and dropped == {"777": 1}


# ── the builders, on NYAX-shaped rows ───────────────────────────────────────────────

def _nyax_rows():
    """The CEO's open-market buys, the CTO's 4/A restatement, small officer sales, an
    option-exercise leg and a row from another issuer — all "Ordinary Shares"."""
    rows = [
        _row(_CEO, 18305, 48.5624, _day(3), _day(1), url="acc-30"),
        _row(_CEO, 16227, 47.8893, _day(3), _day(1), url="acc-30"),
        _row(_CEO, 9616, 46.386, _day(3), _day(1), url="acc-30"),
        _row(_CEO, 19000, 44.7953, _day(4), _day(3), url="acc-29"),
        _row(_CEO, 104511, 44.0884, _day(9), _day(9), url="acc-24"),
        _row(_CTO, 100, 42.0906, _day(9), _day(4), form="4/A", url="acc-873"),
        _row(_CTO, 100, 42.0906, _day(9), _day(9), url="acc-864"),
        _row(_CFO, 293, 44.8014, _day(5), _day(4), tx="S-Sale", url="acc-40"),
        _row(_CFO, 1500, 21.352, _day(17), _day(16), tx="M-Exempt", url="acc-41"),
        _row(_CFO, 1500, 21.352, _day(17), _day(16), tx="M-Exempt", url="acc-41",
             security="Stock Option (Right to Buy)"),
        _row(_CEO, 5000, 10.0, _day(6), _day(5), cik="0000000777", url="acc-99"),
    ]
    return rows


def test_ordinary_share_rows_count_identically_on_every_surface():
    """THE regression: before the fix every count below was 0 for NYAX."""
    rows, dropped = prepare_insider_rows(_nyax_rows(), NYAX_CIK)
    assert dropped == {"777": 1}
    data, vital = _build_insider_sections(rows)
    svc = object.__new__(HoldersService)
    chart = svc._build_insider_smart_money(rows, {}, None)
    acts = svc._build_insider_activities(rows, [])
    summary = svc._build_insider_activity_summary(acts)
    card = _extract_ceo_buys(rows)

    ceo_dollars = 18305 * 48.5624 + 16227 * 47.8893 + 9616 * 46.386 + 19000 * 44.7953 + 104511 * 44.0884
    buy_dollars = ceo_dollars + 100 * 42.0906
    assert vital["buy_count"] == 6 and vital["sell_count"] == 1          # the 4/A counted once
    assert data["sentiment"] == "positive"
    assert summary.num_buyers == 2 and summary.num_sellers == 1
    assert chart.summary.total_buy_usd_millions == pytest.approx(buy_dollars / 1e6, rel=1e-3)
    assert sum(p.buy_volume for p in chart.flow_data) == pytest.approx(
        summary.informative_buys_in_millions)
    assert sum(b.dollars for b in card) == pytest.approx(ceo_dollars)
    # The option leg never reaches the list; the stock leg of the exercise does.
    assert [a.transaction_type for a in acts].count("Uninformative Buy") == 1


def test_warrant_lines_are_excluded_everywhere():
    """The old substring rule KEPT "Common Stock Purchase Warrant" (live: $26M PYXS row)."""
    rows = [_row(_CEO, 7_546_766, 3.5, _day(2), _day(1), security="Common Stock Purchase Warrant"),
            _row(_CEO, 1000, 3.5, _day(2), _day(1), security="Common Stock")]  # anti-vacuity
    _, vital = _build_insider_sections(rows)
    svc = object.__new__(HoldersService)
    acts = svc._build_insider_activities(rows, [])
    chart = svc._build_insider_smart_money(rows, {}, None)
    assert vital["buy_count"] == 1 and len(acts) == 1
    assert sum(p.buy_volume for p in chart.flow_data) == pytest.approx(0.001)


def test_activity_titles_are_cleaned_and_fall_back_to_the_row():
    rows = [_row(_CEO, 1000, 40.0, _day(2), _day(1))]
    svc = object.__new__(HoldersService)
    assert svc._build_insider_activities(rows, [])[0].title == "CEO, Co Founder & Chairman"
    roster = [{"owner": "NECHMAD YAIR", "title": "director, officer: Chief Executive Officer"}]
    assert svc._build_insider_activities(rows, roster)[0].title == "director, Chief Executive Officer"


# ── the report's honest "unavailable" ───────────────────────────────────────────────

def test_an_unavailable_insider_section_is_unmeasured_not_neutral():
    """No zero ROWS: the PDF, report-chat grounding and app builds older than the flag would
    print or narrate "Buys 0 / Sells 0" as measured. The caption every shipped build prints
    carries the message instead."""
    data, vital = _build_insider_sections([], unavailable=True)
    assert data["unavailable"] is True
    assert data["transactions"] == []
    assert data["timeframe"] == "Insider data couldn't be loaded"
    assert vital["score"]["value"] is None and vital["net_activity"] == "Unavailable"
    measured, _ = _build_insider_sections([])
    assert "unavailable" not in measured


def _out():
    """The attributes the pass-1 settle helpers write (the real dataclass needs every
    collected field to construct)."""
    from types import SimpleNamespace
    return SimpleNamespace(insider_trades=[], insider_unavailable=False, degraded_sections=[],
                           holders_response=None)


def test_a_failed_insider_fetch_marks_the_report_and_blocks_the_caches():
    out = _out()
    _settle_pass1_result(out, "insider_trades", RuntimeError("429"), [], "NYAX")
    assert out.insider_trades == [] and out.insider_unavailable is True
    assert out.degraded_sections == ["insider_trades:RuntimeError"]


def test_a_lost_page_is_not_frozen_into_the_report():
    out = _out()
    exc = FMPPartialPageException("lost", endpoint="insider-trading/search", pages_total=2,
                                  pages_failed=1, partial=[{"x": 1}])
    _settle_pass1_result(out, "insider_trades", exc, [], "NYAX")
    assert out.insider_trades == [] and out.insider_unavailable is True
    assert out.degraded_sections == ["insider_trades:FMPPartialPageException"]


def test_a_page_cap_hit_is_company_state_and_stays_cacheable():
    out = _out()
    exc = FMPPartialPageException("cap", endpoint="insider-trading/search", pages_total=5,
                                  pages_failed=0, partial=[{"a": 1}, "junk"])
    _settle_pass1_result(out, "insider_trades", exc, [], "NYAX")
    assert out.insider_trades == [{"a": 1}]
    assert out.insider_unavailable is False and out.degraded_sections == []


def test_rows_land_as_rows():
    out = _out()
    _settle_pass1_result(out, "insider_trades", [{"a": 1}], [], "NYAX")
    assert out.insider_trades == [{"a": 1}] and out.degraded_sections == []


@pytest.mark.parametrize("result,blocking", [
    ((object(), []), None),
    ((object(), ["Inst ownership summary"]), None),     # a source the report does not copy
    ((object(), ["Senate latest"]), "holders_response:Senate latest"),   # Hidden Signals copies congress
    ((object(), ["Insider trading"]), "holders_response:Insider trading"),
    (ValueError("Invalid ticker symbol: 'BRK.B'"), None),   # repeats on every rebuild
    (FMPNotEntitledException("not on the plan"), None),
    ((object(), ["Historical prices", "Quote"]), "holders_response:Historical prices"),
    ((object(), "garbage"), "holders_response:status_malformed"),
    (object(), "holders_response:status_unknown"),
    (RuntimeError("boom"), "holders_response:RuntimeError"),
])
def test_a_degraded_holders_build_is_used_but_never_cached(result, blocking):
    out = _out()
    _settle_holders_result(out, result, "NYAX")
    assert out.degraded_sections == ([blocking] if blocking else [])
    if isinstance(result, BaseException):
        assert out.holders_response is None


@pytest.mark.parametrize("title,expected", [
    ("officer: CEO, Co Founder & Chairman", 1),     # was 5 ("chair") — below CFO/COO/President
    ("director, officer: President and CEO", 1),
    ("officer: CEO NAYX North America", 99),        # a segment CEO is not THE CEO
    ("officer: CFO", 2),
    ("officer: President", 4),
])
def test_key_management_ranks_the_issuer_ceo_first(title, expected):
    from app.services._insider_common import is_ceo_role
    cleaned = trdc._clean_role_title(title)
    assert _role_rank(cleaned, "", ceo=is_ceo_role(title)) == expected


# ── FMP client ──────────────────────────────────────────────────────────────────────

def test_a_failed_single_page_fetch_is_marked_not_empty(monkeypatch):
    """Tracking caches a MEASURED empty answer for 10 minutes and checks `fetch_failed` to
    tell an outage apart; the client used to return a bare [] on a 429."""
    from app.integrations.fmp import FMPRateLimitException

    async def boom(self, endpoint, params=None, **kw):
        raise FMPRateLimitException("429 for https://x?apikey=SECRET")

    monkeypatch.setattr(FMPClient, "_make_request", boom)
    client = object.__new__(FMPClient)
    out = asyncio.run(client.get_insider_trading("NYAX", limit=30))
    assert isinstance(out, EmptyAfterFailure) and out.fetch_failed and out == []
    assert "SECRET" not in out.reason
    roster = asyncio.run(client.get_insider_roster("NYAX"))
    assert getattr(roster, "fetch_failed", False), "the roster keeps the failure marker"


def test_the_roster_carries_the_issuer_cik(monkeypatch):
    async def page(self, endpoint, params=None, **kw):
        return [
            {"reportingName": "BERKSHIRE HATHAWAY INC", "typeOfOwner": "10 percent owner",
             "securitiesOwned": 5, "companyCik": "0000920760"},
            {"reportingName": "BERKSHIRE HATHAWAY INC", "typeOfOwner": "10 percent owner",
             "securitiesOwned": 9, "companyCik": "0001067983"},
            {"reportingName": "ABEL GREGORY", "typeOfOwner": "director",
             "securitiesOwned": 7, "companyCik": "0001067983"},
        ]

    monkeypatch.setattr(FMPClient, "_make_request", page)
    roster = asyncio.run(object.__new__(FMPClient).get_insider_roster("BRK-B"))
    assert [(r["owner"], r["companyCik"]) for r in roster] == [
        ("BERKSHIRE HATHAWAY INC", "0000920760"),
        ("BERKSHIRE HATHAWAY INC", "0001067983"),
        ("ABEL GREGORY", "0001067983"),
    ]
    assert [r["owner"] for r in issuer_roster(roster, "0001067983")] == [
        "BERKSHIRE HATHAWAY INC", "ABEL GREGORY"]


# ── wiring: the real get_holders_with_status path ───────────────────────────────────

class _FMP:
    def __init__(self, insider, *, summary=None, profile=None):
        self.insider, self.summary, self.profile = insider, summary or {}, profile
        self.profile_calls = 0

    async def get_shares_float(self, t): return {"freeFloat": 80.0, "outstandingShares": 37.0e6}
    async def get_institutional_holder(self, t, limit=20): return []
    async def get_institutional_ownership_summary(self, t): return self.summary
    async def get_institutional_ownership_for_quarter(self, t, y, q, strict=False): return None
    async def get_insider_trades_since(self, since, *, symbol=None, page_size=1000,
                                       max_pages=5, transaction_type=None):
        if isinstance(self.insider, BaseException):
            raise self.insider
        return self.insider
    async def get_company_profile(self, t):
        self.profile_calls += 1
        if isinstance(self.profile, BaseException):
            raise self.profile
        return self.profile or {}
    async def get_insider_roster(self, t): return []
    async def get_historical_prices(self, t, from_date=None, to_date=None): return []
    async def get_senate_latest(self, limit=1000): return []
    async def get_house_latest(self, limit=1000): return []
    async def get_senate_disclosure(self, t): return []
    async def get_house_disclosure(self, t): return []
    async def get_stock_price_quote(self, t): return {"price": 49.0}


class _CA:
    async def get_split_rows(self, *a, **k): return []
    async def has_unclassified_adjustment(self, *a, **k): return False


class _Supabase:
    def __init__(self): self.upserts = []
    def table(self, _): return self
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def upsert(self, payload, **k): self.upserts.append(payload); return self
    def execute(self):
        class _R: data = []
        return _R()


@pytest.fixture(autouse=True)
def _fresh_holders_tiers(monkeypatch):
    """Each test gets its own holders 5-min tier and in-flight map, restored afterwards."""
    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_inflight", {})
    monkeypatch.setattr(hs, "_background_tasks", set())


def _wired(fmp):
    """A HoldersService on fakes, with a cold 5-min tier (the dicts are this test's own)."""
    from tests._price_fakes import PriceFromFMPFake
    hs._cache.clear(); hs._inflight.clear()
    svc = object.__new__(HoldersService)
    svc.fmp, svc.price, svc.corporate_actions = fmp, PriceFromFMPFake(fmp), _CA()
    svc.supabase = _Supabase()
    return svc


async def _drain():
    """Wait for the background holders upsert deterministically (not a fixed sleep)."""
    await asyncio.gather(*list(hs._background_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_holders_counts_nyax_through_the_real_build_and_persists_it():
    fmp = _FMP(_nyax_rows(), summary={"cik": NYAX_CIK, "ownershipPercent": 40.0})
    svc = _wired(fmp)
    resp, degraded = await svc.get_holders_with_status("NYAX")
    assert degraded == []
    assert fmp.profile_calls == 0, "the CIK comes from the summary already fetched"
    summary = resp.recent_activities.insider_activities.summary
    assert summary.num_buyers == 2 and summary.num_sellers == 1
    assert resp.recent_activities.insider_activities.unavailable is None
    assert resp.insider_data.unavailable is None
    buy_dollars = (18305 * 48.5624 + 16227 * 47.8893 + 9616 * 46.386 + 19000 * 44.7953
                   + 104511 * 44.0884 + 100 * 42.0906)
    assert resp.insider_data.summary.total_buy_usd_millions == pytest.approx(buy_dollars / 1e6, rel=1e-3)
    names = {a.name for a in resp.recent_activities.insider_activities.activities}
    assert "Yair Nechmad" in names
    await _drain()
    assert svc.supabase.upserts and svc.supabase.upserts[0]["response_json"]["payload_version"] == 2
    # a 5-minute hit reports the same (clean) status
    assert (await svc.get_holders_with_status("NYAX"))[1] == []


@pytest.mark.asyncio
async def test_a_failed_insider_fetch_is_served_but_never_pinned():
    fmp = _FMP(RuntimeError("429"), summary={"cik": NYAX_CIK})
    svc = _wired(fmp)
    resp, degraded = await svc.get_holders_with_status("NYAX")
    assert "Insider trading" in degraded
    assert resp.recent_activities.insider_activities.activities == []
    # ...and SAYS so: an empty list from a failed fetch is not "no insider transactions".
    assert resp.recent_activities.insider_activities.unavailable is True
    assert resp.insider_data.unavailable is True
    assert not hs._background_tasks, "a degraded build must not schedule the 24h upsert"
    await _drain()
    assert svc.supabase.upserts == []
    # the 5-minute tier keeps the status, so a second caller cannot launder it
    assert "Insider trading" in (await svc.get_holders_with_status("NYAX"))[1]
    assert isinstance(await svc.get_holders("NYAX"), type(resp))


@pytest.mark.asyncio
async def test_a_lost_page_serves_what_arrived_and_a_cap_hit_persists():
    lost = FMPPartialPageException("lost", endpoint="e", pages_total=2, pages_failed=1,
                                   partial=_nyax_rows())
    resp, degraded = await _wired(_FMP(lost, summary={"cik": NYAX_CIK})).get_holders_with_status("NYAX")
    assert degraded == ["Insider trading"]
    assert resp.recent_activities.insider_activities.summary.num_buyers == 2

    cap = FMPPartialPageException("cap", endpoint="e", pages_total=5, pages_failed=0,
                                  partial=_nyax_rows())
    svc = _wired(_FMP(cap, summary={"cik": NYAX_CIK}))
    _, degraded = await svc.get_holders_with_status("NYAX")
    assert degraded == []
    await _drain()
    assert svc.supabase.upserts


@pytest.mark.asyncio
async def test_the_cik_falls_back_to_the_profile_and_a_failed_lookup_is_degraded():
    fmp = _FMP(_nyax_rows(), profile={"cik": NYAX_CIK})
    resp, degraded = await _wired(fmp).get_holders_with_status("NYAX")
    assert fmp.profile_calls == 1 and degraded == []
    assert resp.recent_activities.insider_activities.summary.num_buyers == 2

    broken = _FMP(_nyax_rows(), profile=RuntimeError("profile 503"))
    _, degraded = await _wired(broken).get_holders_with_status("NYAX")
    assert degraded == ["Issuer CIK"]


def test_finite_guard_on_amount_rounding():
    """NaN prices never reach a rounded line key (a NaN never equals itself)."""
    day = _day(2)
    rows = [_row(_CFO, 10, float("nan"), day, _day(1), form="4/A", url="b"),
            _row(_CFO, 10, float("nan"), day, _day(1), url="a")]
    kept = supersede_form4_amendments(rows)
    assert len(kept) == 1 and math.isnan(kept[0]["price"])


# ── round 2 (adversarial review of this fix, 2026-10-03) ─────────────────────────────

def test_a_one_line_footnote_4a_does_not_wipe_a_multi_line_day():
    """The CEO card's partial rule removes EVERY original sharing a size or price with any
    amended line. On a day of round-lot sales, a 4/A re-filing one line unchanged cut five
    lines to two. One-to-one matching keeps all five."""
    day, filed = _day(20), _day(19)
    originals = [_row(_CFO, 1000, 50.10, day, filed, tx="S-Sale", url="a"),
                 _row(_CFO, 1000, 50.35, day, filed, tx="S-Sale", url="a"),
                 _row(_CFO, 1000, 50.80, day, filed, tx="S-Sale", url="a"),
                 _row(_CFO, 2500, 50.10, day, filed, tx="S-Sale", url="a"),
                 _row(_CFO, 700, 51.00, day, filed, tx="S-Sale", url="a")]
    footnote = _row(_CFO, 1000, 50.10, day, _day(10), tx="S-Sale", form="4/A", url="b")
    kept = supersede_form4_amendments([footnote, *originals])
    assert sum(r["securitiesTransacted"] for r in kept) == 6200
    assert len(kept) == 5 and footnote in kept


def test_a_zero_price_line_matches_nothing_by_price():
    """$0 awards share a "price" without being the same line: a 4/A adding a 1,200-share
    grant must not consume a 3,000-share one."""
    day = _day(30)
    rows = [_row(_CFO, 1200, 0.0, day, _day(10), tx="A-Award", form="4/A", url="b"),
            _row(_CFO, 5000, 0.0, day, _day(29), tx="A-Award", url="a"),
            _row(_CFO, 3000, 0.0, day, _day(29), tx="A-Award", url="a")]
    assert sorted(r["securitiesTransacted"] for r in supersede_form4_amendments(rows)) == [1200, 3000, 5000]


def test_a_correction_consumes_only_its_nearest_original():
    day = _day(30)
    rows = [_row(_CFO, 1000, 50.12, day, _day(10), tx="S-Sale", form="4/A", url="b"),
            _row(_CFO, 1000, 50.10, day, _day(29), tx="S-Sale", url="a"),
            _row(_CFO, 1000, 58.00, day, _day(29), tx="S-Sale", url="a"),
            _row(_CFO, 1000, 61.00, day, _day(29), tx="S-Sale", url="a")]
    kept = supersede_form4_amendments(rows)
    assert sorted(r["price"] for r in kept) == [50.12, 58.0, 61.0]


def test_two_amendments_on_one_day_both_count_like_the_ceo_card():
    day, filed = _day(12), _day(11)
    rows = [_row(_CEO, 100, 40.0, day, filed, url="a"),
            _row(_CEO, 200, 41.0, day, filed, url="a"),
            _row(_CEO, 300, 42.0, day, filed, url="a"),
            _row(_CEO, 150, 40.0, day, _day(5), form="4/A", url="acc-AAA"),
            _row(_CEO, 250, 41.0, day, _day(5), form="4/A", url="acc-BBB")]
    generic = sorted((r["securitiesTransacted"], r["price"]) for r in supersede_form4_amendments(rows))
    card = sorted((b.shares, b.price) for b in _extract_ceo_buys(rows))
    assert generic == [(150, 40.0), (250, 41.0), (300, 42.0)]
    assert [(int(s), p) for s, p in card] == generic


def test_reporter_identity_is_the_normalised_cik():
    from app.services._insider_common import insider_reporter_key
    same = [{"reportingCik": c} for c in (1903011, "0001903011", 1903011.0)]
    assert {insider_reporter_key(r) for r in same} == {"cik:1903011"}
    assert insider_reporter_key({"reportingCik": "0000000000", "reportingName": "DOE JANE"}) \
        == "name:jane doe"


def test_a_non_string_trade_date_falls_back_to_the_filing_date():
    assert insider_row_date({"transactionDate": 20260922, "filingDate": "2026-09-23"}) == "2026-09-23"


@pytest.mark.asyncio
async def test_the_holders_list_is_windowed_to_the_last_12_months():
    """A 1000-row page reaches back years; the list sits under a "Last 12 Months" label."""
    rows = _nyax_rows() + [_row(_CFO, 77, 30.0, _day(900), _day(899), tx="S-Sale", url="old")]
    resp, _ = await _wired(_FMP(rows, summary={"cik": NYAX_CIK})).get_holders_with_status("NYAX")
    cutoff = insider_window_cutoff()
    acts = resp.recent_activities.insider_activities.activities
    assert acts and all(a.date >= cutoff for a in acts)


@pytest.mark.asyncio
async def test_a_failed_roster_keeps_the_build_out_of_the_24h_tier():
    class _NoRoster(_FMP):
        async def get_insider_roster(self, t):
            return EmptyAfterFailure("roster 429")

    svc = _wired(_NoRoster(_nyax_rows(), summary={"cik": NYAX_CIK}))
    _, degraded = await svc.get_holders_with_status("NYAX")
    assert degraded == ["Insider roster"]
    await _drain()
    assert svc.supabase.upserts == []


@pytest.mark.asyncio
async def test_an_unlicensed_insider_path_is_not_an_outage():
    svc = _wired(_FMP(FMPNotEntitledException("insider-trading/search"), summary={"cik": NYAX_CIK}))
    resp, degraded = await svc.get_holders_with_status("NYAX")
    assert degraded == [] and resp.recent_activities.insider_activities.unavailable is None


@pytest.mark.asyncio
async def test_an_unusable_summary_cik_falls_through_to_the_profile():
    fmp = _FMP(_nyax_rows(), summary={"cik": "0000000000"}, profile={"cik": NYAX_CIK})
    resp, degraded = await _wired(fmp).get_holders_with_status("NYAX")
    assert fmp.profile_calls == 1 and degraded == []
    names = {a.name for a in resp.recent_activities.insider_activities.activities}
    assert names == {"Yair Nechmad", "David Ben-avi", "Sagit Manor"}   # the CIK-777 row dropped


def test_a_failed_roster_blocks_the_report_caches_and_not_entitled_does_not():
    out = _out()
    out.insider_roster = []
    _settle_pass1_result(out, "insider_roster", EmptyAfterFailure("429"), [], "NYAX")
    assert out.insider_roster == [] and out.degraded_sections == ["insider_roster:fetch_failed"]

    out = _out()
    _settle_pass1_result(out, "insider_trades", FMPNotEntitledException("x"), [], "NYAX")
    assert out.insider_unavailable is False and out.degraded_sections == []


# ── the Overview ownership snapshot ─────────────────────────────────────────────────

class _SnapSupabase:
    def __init__(self): self.upserts = []
    def table(self, _): return self
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def upsert(self, payload, **k): self.upserts.append(payload); return self
    def execute(self):
        class _R: data = []
        return _R()


@pytest.mark.asyncio
@pytest.mark.parametrize("degraded,persisted", [([], True), (["Insider trading"], False)])
async def test_the_snapshot_pins_only_a_complete_holders_build(monkeypatch, degraded, persisted):
    from app.schemas.holders import HoldersResponse, SmartMoneyDataSchema
    from app.services import ownership_snapshot_service as oss

    holders = HoldersResponse(
        symbol="NYAX",
        insider_data=SmartMoneyDataSchema(tab="Insider", unavailable=True if degraded else None),
    )

    class _Holders:
        async def get_holders_with_status(self, ticker):
            return holders, list(degraded)

    monkeypatch.setattr(hs, "get_holders_service", lambda: _Holders())
    monkeypatch.setattr(oss, "_cache", {})
    monkeypatch.setattr(oss, "_inflight", {})
    svc = object.__new__(oss.OwnershipSnapshotService)
    svc.supabase = _SnapSupabase()
    snap = await svc.get_ownership_snapshot("NYAX")
    await asyncio.sleep(0.05)   # run_in_executor upsert
    assert bool(svc.supabase.upserts) is persisted
    value = {m.name: m.value for m in snap.metrics}["Insider Activity (12M)"]
    assert value == ("Neutral" if persisted else "—")
    assert oss._cache, "the snapshot is still served from the 5-minute tier"


# ── the report's narrative never reads placeholder zeros ────────────────────────────

def test_the_narrative_says_unavailable_not_no_transactions():
    from app.services.agents import narrative_prompts as np_
    from app.services.agents.persona_config import get_persona_config

    data, vital = _build_insider_sections([], unavailable=True)
    shell = {"insider_data": data, "key_management": {"top_holders": []},
             "_scoring_inputs": {"insider": dict(vital)}}
    prompt = np_._key_management_insight_prompt(get_persona_config("warren_buffett"), "E", shell)
    assert "UNAVAILABLE" in prompt and "NO transactions" not in prompt
    digest = "\n".join(np_._digest_insider({"insider_data": data}))
    assert "unavailable" in digest and "Buys 0" not in digest
    labels = {j.label for j in np_.build_narrative_jobs(
        get_persona_config("warren_buffett"), "E", shell)}
    assert "insider_key_insight" not in labels
    measured, mvital = _build_insider_sections([])
    shell = {"insider_data": measured, "_scoring_inputs": {"insider": dict(mvital)}}
    assert "insider_key_insight" in {j.label for j in np_.build_narrative_jobs(
        get_persona_config("warren_buffett"), "E", shell)}
