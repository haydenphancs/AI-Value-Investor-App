"""The plain bugs behind Berkshire's 39.6 (report 2af8e4f4, 2026-11-05), and what the reviews
of the fix found.

The rollup was right; these inputs were not:
  * a Form 4 row FMP left at price 0 counted as a buy worth $0, so the CEO buying about $15M of
    Class A read as nothing and the report said "$20.0M sold against $501K bought";
  * Class A and Class B were added 1:1, and Buffett's 188,290 Class A at the BRK-B price read
    0.0088% and $109M, about 1,500 times too small, on two screens;
  * the forecast window kept an already reported year as a "forecast" whenever fewer than four
    forward years existed (BRK-B, TER, GME);
  * the style score read fiscal-year ROE 9.3 beside a card scoring TTM 11.86.

Outliers on purpose: a priced Class A row (must NOT be multiplied), a price-less sell, a window
with no closes at all, a report on the other class, a 52/53-week filer, a split inside the
window, an ADR, joint filers, a filer behind on its reports. No network.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import _insider_common as ic
from app.services import _insider_holdings as ih
from app.services import chat_ownership_tool as chat
from app.services import ownership_snapshot_service as snapshot
from app.services.agents import narrative_prompts as np_
from app.services.agents import ticker_report_data_collector as C
from app.services.agents.persona_config import get_persona_config
from app.services.holders_service import HoldersService
from app.schemas.holders import DailyPricePointSchema

TODAY = datetime.now(timezone.utc).date()
Y = TODAY.year     # a December year end of this year is always still ahead


def _day(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _row(kind="P-Purchase", shares=100, price=10.0, security="Class B Common Stock", days_ago=30):
    return {
        "transactionType": kind,
        "securitiesTransacted": shares,
        "price": price,
        "securityName": security,
        "transactionDate": _day(days_ago),
        "reportingName": "ABEL GREGORY",
    }


def _closes(close=500.0, days=60):
    return [{"date": _day(i), "close": close} for i in range(days)]


def _close_on(close=500.0):
    """What production hands the builders when every row may take a close."""
    return ic.priced_close_lookup([(_day(i), close) for i in range(60)], [])


def _dollars(text: str) -> float:
    return float(text.lstrip("~$").rstrip("KMB")) * {"K": 1e3, "M": 1e6, "B": 1e9}[text[-1]]


# ── share classes ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("symbol, security, expected", [
    ("BRK-B", "Class A Common Stock", 1500.0),
    ("BRK-B", "Class B Common Stock", 1.0),
    ("BRK-A", "Class B Common Stock", 1 / 1500),
    ("BRK-A", "Class A Common Stock", 1.0),
    ("brk-b", "CLASS A COMMON STOCK", 1500.0),
    ("BH", "Class A Common Stock", 5.0),
    ("BH-A", "Class B Common Stock", 0.2),
    ("BRK-B", "Common Stock", 1.0),                  # no class named: never guess a 1,500x
    ("BRK-B", None, 1.0),
    ("BRK-B", "Series A Preferred", 1.0),
    ("ERIE", "Class B Common Stock", 2400.0),        # Erie's 10-K: 1 Class B = 2,400 Class A
    ("ERIE", "Class A Common Stock", 1.0),
    ("GOOGL", "Class A Common Stock", 1.0),          # an issuer with no row is 1:1
    ("", "Class A Common Stock", 1.0),
    (None, "Class A Common Stock", 1.0),
])
def test_share_class_multiple(symbol, security, expected):
    assert ic.share_class_multiple(symbol, security) == pytest.approx(expected)


# ── closes ───────────────────────────────────────────────────────────────────

def test_close_lookup_finds_the_last_close_on_or_before_the_day():
    close_on = ic.close_lookup([("2026-10-02", 100.0), ("2026-10-05", 110.0)])
    assert close_on("2026-10-05") == 110.0
    assert close_on("2026-10-04") == 100.0           # a Sunday reads Friday
    assert close_on("2026-10-01") is None            # never a close from after the trade
    assert close_on("2026-10-12") == 110.0           # exactly seven days stale
    assert close_on("2026-10-13") is None            # eight days stale
    assert close_on("") is None and close_on("not a date") is None


@pytest.mark.parametrize("points", [
    [], [("2026-10-05", 0.0)], [("2026-10-05", None)], [("2026-10-05", float("nan"))],
    [(None, 100.0)], [("bad", 100.0)],
])
def test_close_lookup_ignores_unusable_points(points):
    assert ic.close_lookup(points)("2026-10-05") is None


# ── one row's value ──────────────────────────────────────────────────────────

def test_a_priced_class_a_row_is_never_multiplied():
    """Its price is already per Class A share: the multiple would overstate it 1,500 times."""
    row = _row(shares=2, price=750_000.0, security="Class A Common Stock")
    dollars, how = ic.insider_row_value(row, symbol="BRK-B", close_on=lambda _d: 500.0)
    assert dollars == 1_500_000.0 and how == ic.VALUE_FILED


def test_a_priceless_class_a_row_uses_the_class_b_close_and_the_multiple():
    row = _row(shares=2, price=0, security="Class A Common Stock")
    dollars, how = ic.insider_row_value(row, symbol="BRK-B", close_on=lambda _d: 500.0)
    assert dollars == 1_500_000.0 and how == ic.VALUE_ESTIMATED


def test_a_priceless_class_b_row_on_a_class_a_report_converts_down():
    row = _row(shares=1500, price=0, security="Class B Common Stock")
    dollars, how = ic.insider_row_value(row, symbol="BRK-A", close_on=lambda _d: 750_000.0)
    assert dollars == pytest.approx(750_000.0) and how == ic.VALUE_ESTIMATED


@pytest.mark.parametrize("price", [0, None, -5.0, "bad", float("nan")])
def test_a_row_with_no_usable_price_and_no_close_is_unpriced(price):
    row = _row(price=price)
    assert ic.insider_row_value(row, symbol="AAPL", close_on=lambda _d: None) == (
        0.0, ic.VALUE_UNPRICED,
    )
    assert ic.insider_row_value(row) == (0.0, ic.VALUE_UNPRICED)


@pytest.mark.parametrize("shares", [0, None, "bad"])
def test_a_row_with_no_shares_is_worth_nothing_and_is_not_unknown(shares):
    assert ic.insider_row_value(_row(shares=shares)) == (0.0, ic.VALUE_FILED)


# ── which closes may value a row ─────────────────────────────────────────────

def test_a_close_is_refused_before_a_split_inside_the_window():
    """Closes are split-adjusted, a filed share count is not: 5,000 shares bought at $1,200
    before a 10:1 split would read $600K against a real $6.0M, and could flip the verdict."""
    close_on = ic.priced_close_lookup(
        [("2026-05-01", 120.0), ("2026-06-12", 125.0), ("2026-07-01", 130.0)], ["2026-06-12"],
    )
    assert close_on("2026-05-01") is None
    assert close_on("2026-06-12") == 125.0
    assert close_on("2026-07-01") == 130.0


def test_only_the_latest_split_matters():
    close_on = ic.priced_close_lookup(
        [("2026-03-01", 10.0), ("2026-08-01", 10.0)], ["2026-02-01", "2026-06-12"],
    )
    assert close_on("2026-03-01") is None and close_on("2026-08-01") == 10.0


def test_no_close_values_a_row_on_an_adr_or_when_splits_could_not_be_checked():
    points = [("2026-05-01", 120.0)]
    assert ic.priced_close_lookup(points, [], is_adr=True) is None
    assert ic.priced_close_lookup(points, None) is None
    assert ic.priced_close_lookup(points, [])("2026-05-01") == 120.0


@pytest.mark.parametrize("rows, expected", [
    ([_row(price=0)], True),
    ([_row(kind="S-Sale", price=None)], True),
    ([_row(price=10.0)], False),
    ([_row(kind="A-Award", price=0)], False),
    ([_row(kind="G-Gift", price=0)], False),
    ([_row(price=0, shares=0)], False),
    ([], False), (None, False), (["junk"], False),
])
def test_only_an_open_market_row_with_no_price_needs_a_close(rows, expected):
    assert ic.needs_close(rows) is expected


class _Splits:
    def __init__(self, answer):
        self.answer = answer

    async def get_split_rows(self, symbol, from_date=None, to_date=None):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize("answer, expected", [
    ([{"date": "2026-06-12"}, {"date": "2026-01-02"}], ["2026-01-02", "2026-06-12"]),
    ([], []),
    (None, None),                   # could not check is never "no split"
    (RuntimeError("429"), None),
])
def test_a_failed_split_lookup_is_not_no_split(answer, expected):
    assert asyncio.run(ic.insider_split_days(_Splits(answer), "KLAC")) == expected


# ── joint filers ─────────────────────────────────────────────────────────────

def _joint(reporter, **extra):
    row = _row(shares=1_444_402, price=34.5864, security="Common Stock")
    row.update(companyCik="0000084839", reportingCik=reporter, securitiesOwned=146_728_468,
               **extra)
    return row


def test_a_joint_form_4_line_counts_once():
    """Rollins, 2026-10-29: the trust and LOR Inc. each filed the same $50M purchase."""
    data, _ = C._build_insider_sections([_joint("1"), _joint("2")], symbol="ROL")
    assert data["transactions"][0]["count"] == 1
    assert _dollars(data["transactions"][0]["value"]) == pytest.approx(49.96e6, rel=0.01)
    chart = _holders()._build_insider_smart_money([_joint("1"), _joint("2")], {}, symbol="ROL")
    assert chart.summary.total_buy_usd_millions == pytest.approx(49.96, abs=0.01)
    assert len(_holders()._build_insider_activities([_joint("1"), _joint("2")], [])) == 1


@pytest.mark.parametrize("second", [
    {"securitiesOwned": 999},                  # another balance: another person's trade
    {"price": 35.0},
    {"securitiesTransacted": 5},
    {"transactionType": "S-Sale"},
    {"reportingCik": "1"},                     # one reporter's two lines are two lines
])
def test_lines_that_differ_stay_separate(second):
    other = dict(_joint("2"), **second)
    assert len(ic.distinct_trades([_joint("1"), other])) == 2


def test_a_line_with_no_balance_is_never_matched():
    one, two = _joint("1"), _joint("2")
    one["securitiesOwned"] = two["securitiesOwned"] = None
    assert len(ic.distinct_trades([one, two])) == 2


@pytest.mark.parametrize("rows", [None, "junk", [None, "junk"]])
def test_junk_input_to_distinct_trades(rows):
    assert ic.distinct_trades(rows) == []


# ── the report's Insider section ─────────────────────────────────────────────

def _berkshire_window():
    """The real shape: 20 price-less Class A buys, 5 priced Class B buys, 1 large sale."""
    rows = [_row(shares=1, price=0, security="Class A Common Stock") for _ in range(20)]
    rows += [_row(shares=200, price=500.0) for _ in range(5)]
    rows.append(_row(kind="S-Sale", shares=39_700, price=503.09))
    return rows


def test_the_ceos_priceless_buys_count_and_are_marked_estimated():
    data, vital = C._build_insider_sections(
        _berkshire_window(), symbol="BRK-B", close_on=_close_on(500.0),
    )
    buys, sells = data["transactions"]
    # 20 x 1,500 x $500 = $15.0M, plus 5 x 200 x $500 = $500K.
    assert buys["value"] == "~$15.5M", "an estimate must never read as a filed figure"
    assert sells["value"] == "$20.0M", "a side with only filed prices stays unmarked"
    assert buys["shares"] == "31K", "stated in Class B: 20 x 1,500 + 1,000"
    assert vital["estimated_value"] is True and vital["unpriced_rows"] == 0
    assert vital["net_activity"] == "Net Selling"
    # net -$4.47M of $35.47M gross: 5 - 0.126 x 2.
    assert vital["score"]["value"] == pytest.approx(4.7, abs=0.05)


def test_the_same_window_without_the_fix_would_read_almost_all_selling():
    """Anti-vacuity for the test above: rows with no close available stay unknown."""
    _, vital = C._build_insider_sections(_berkshire_window(), symbol="BRK-B")
    assert vital["unpriced_rows"] == 20


def test_a_row_nobody_can_value_blocks_the_verdict():
    """An unknown amount could flip the sign: no score and no "critical" from a partial sum."""
    rows = [_row(shares=500_000, price=0), _row(kind="S-Sale", shares=10_000, price=100.0)]
    data, vital = C._build_insider_sections(rows, symbol="AAPL")
    assert vital["score"]["value"] is None
    assert data["sentiment"] == "neutral" and vital["net_activity"] == "Balanced"
    assert vital["unpriced_rows"] == 1
    assert data["transactions"][0]["value"] == "N/A"
    assert data["transactions"][1]["value"] == "$1.0M", "the filed side stays as filed"


def test_a_side_missing_a_rows_value_has_no_total():
    """A partial sum is a floor, not an estimate: "~$100K" would read as about $100K."""
    rows = [_row(shares=1000, price=100.0), _row(shares=500_000, price=0)]
    data, vital = C._build_insider_sections(rows, symbol="AAPL")
    assert data["transactions"][0]["value"] == "N/A"
    assert vital["score"]["value"] is None


def test_an_unpriced_sale_blocks_the_verdict_too():
    rows = [_row(shares=100_000, price=100.0), _row(kind="S-Sale", shares=500_000, price=0)]
    data, vital = C._build_insider_sections(rows, symbol="AAPL")
    assert vital["unpriced_rows"] == 1 and vital["score"]["value"] is None
    assert data["sentiment"] == "neutral"
    assert data["transactions"][1]["value"] == "N/A"


def test_class_a_sales_convert_like_class_a_buys():
    rows = [
        _row(kind="S-Sale", shares=2, price=750_000.0, security="Class A Common Stock"),
        _row(kind="S-Sale", shares=2, price=0, security="Class A Common Stock"),
    ]
    data, _ = C._build_insider_sections(rows, symbol="BRK-B", close_on=_close_on(500.0))
    sells = data["transactions"][1]
    assert sells["value"] == "~$3.0M" and sells["shares"] == "6K"


def test_a_brk_a_report_keeps_a_class_b_trade_visible():
    rows = [_row(shares=200, price=500.0)]
    data, _ = C._build_insider_sections(rows, symbol="BRK-A")
    assert data["transactions"][0]["shares"] == "0.13", "never 0 beside a real $100K"
    assert data["transactions"][0]["value"] == "$100K"


def test_a_priceless_sell_is_valued_too():
    rows = [_row(kind="S-Sale", shares=2000, price=0), _row(shares=100, price=10.0)]
    data, vital = C._build_insider_sections(rows, symbol="AAPL", close_on=_close_on(100.0))
    assert data["transactions"][1]["value"] == "~$200K"
    assert data["sentiment"] == "negative" and vital["score"]["value"] is not None


def test_an_all_filed_window_is_unchanged():
    rows = [_row(shares=1000, price=100.0), _row(kind="S-Sale", shares=100, price=100.0)]
    data, vital = C._build_insider_sections(rows, symbol="AAPL", close_on=_close_on())
    assert [t["value"] for t in data["transactions"]] == ["$100K", "$10K"]
    assert vital["estimated_value"] is False and vital["unpriced_rows"] == 0


def test_the_insight_prompt_flags_an_estimate_only_when_there_is_one():
    persona = get_persona_config("warren_buffett")
    shell = {"insider_data": {"sentiment": "neutral", "transactions": [{"value": "~$15.5M"}]}}
    assert "estimate from closing prices" in np_._insider_key_insight_prompt(persona, "E", shell)
    shell["insider_data"]["transactions"] = [{"value": "N/A"}]
    assert "value is unknown" in np_._insider_key_insight_prompt(persona, "E", shell)
    shell["insider_data"]["transactions"] = [{"value": "$15.5M"}]
    assert ic.INSIDER_VALUE_NOTE not in np_._insider_key_insight_prompt(persona, "E", shell)


def test_the_thesis_digest_carries_the_same_note():
    def digest(value):
        return " ".join(np_._digest_insider({"insider_data": {
            "sentiment": "neutral", "transactions": [{"type": "Buys", "count": 1, "value": value}],
        }}))

    assert ic.INSIDER_VALUE_NOTE in digest("~$15.5M")
    assert ic.INSIDER_VALUE_NOTE in digest("N/A")
    assert ic.INSIDER_VALUE_NOTE not in digest("$15.5M")


# ── the Holders tab says the same ────────────────────────────────────────────

def _holders():
    return object.__new__(HoldersService)


def _daily(close=500.0):
    return [DailyPricePointSchema(date=_day(i), price=close) for i in range(60)]


def test_holders_and_the_report_state_the_same_totals():
    rows = _berkshire_window()
    report, _ = C._build_insider_sections(rows, symbol="BRK-B", close_on=_close_on())
    chart = _holders()._build_insider_smart_money(
        rows, {}, _daily(), symbol="BRK-B", close_on=_close_on(),
    )
    assert chart.summary.total_buy_usd_millions == pytest.approx(15.5)
    assert report["transactions"][0]["value"] == "~$15.5M"
    assert chart.summary.total_sell_usd_millions == pytest.approx(19.97, abs=0.01)
    assert report["transactions"][1]["value"] == "$20.0M"
    assert chart.summary.total_buy == pytest.approx(0.03, abs=0.01), "millions of Class B"
    assert chart.summary.is_positive is False
    assert chart.summary.usd_estimated is True

    acts = _holders()._build_insider_activities(rows, [], symbol="BRK-B", close_on=_close_on())
    bought = sum(a.change_in_millions for a in acts if a.transaction_type == "Informative Buy")
    assert bought == pytest.approx(0.031), "the list must match the chart"


def test_a_row_the_chart_valued_shows_its_close_on_the_list_never_0():
    row = _row(shares=2, price=0, security="Class A Common Stock")
    act = _holders()._build_insider_activities(
        [row], [], symbol="BRK-B", close_on=_close_on(500.0),
    )[0]
    assert act.price_at_transaction == 500.0 and act.price_estimated is True
    assert act.change_in_millions == pytest.approx(0.003)


def test_a_filed_price_is_never_marked_estimated():
    act = _holders()._build_insider_activities(
        [_row(shares=100, price=10.0)], [], close_on=_close_on(),
    )[0]
    assert act.price_at_transaction == 10.0 and act.price_estimated is None


def test_withheld_dollars_carry_no_estimate_flag_even_with_an_estimated_row():
    rows = [_row(shares=100, price=0, days_ago=30), _row(shares=100, price=0, days_ago=200)]
    close_on = ic.priced_close_lookup([(_day(30), 500.0)], [])
    chart = _holders()._build_insider_smart_money(rows, {}, close_on=close_on)
    assert chart.summary.total_buy_usd_millions is None and chart.summary.usd_estimated is None


def test_all_filed_dollars_carry_no_estimate_flag():
    chart = _holders()._build_insider_smart_money(
        [_row(shares=100, price=10.0)], {}, close_on=_close_on(),
    )
    assert chart.summary.usd_estimated is None


def test_estimated_and_class_a_sales_reach_the_holders_chart():
    rows = [
        _row(kind="S-Sale", shares=2, price=0, security="Class A Common Stock"),
        _row(kind="S-Sale", shares=1, price=750_000.0, security="Class A Common Stock"),
    ]
    chart = _holders()._build_insider_smart_money(
        rows, {}, symbol="BRK-B", close_on=_close_on(500.0),
    )
    assert chart.summary.total_sell_usd_millions == pytest.approx(2.25)
    assert sum(p.sell_volume for p in chart.flow_data) == pytest.approx(0.0045)
    assert chart.summary.is_positive is False and chart.summary.usd_estimated is True
    acts = _holders()._build_insider_activities(rows, [], symbol="BRK-B", close_on=_close_on(500.0))
    assert sorted(a.change_in_millions for a in acts) == pytest.approx([-0.003, -0.0015])


def test_a_brk_a_chart_keeps_a_small_class_b_sale():
    rows = [_row(kind="S-Sale", shares=200, price=500.0)]
    chart = _holders()._build_insider_smart_money(rows, {}, symbol="BRK-A")
    assert sum(p.sell_volume for p in chart.flow_data) > 0
    act = _holders()._build_insider_activities(rows, [], symbol="BRK-A")[0]
    assert act.change_in_millions < 0 and act.price_at_transaction == 750_000.0


def test_a_class_a_activity_keeps_shares_times_price_true():
    row = _row(shares=2, price=750_000.0, security="Class A Common Stock")
    act = _holders()._build_insider_activities([row], [], symbol="BRK-B")[0]
    assert act.change_in_millions == pytest.approx(0.003)
    assert act.price_at_transaction == pytest.approx(500.0)
    assert act.change_in_millions * 1e6 * act.price_at_transaction == pytest.approx(1_500_000.0)


def test_an_unpriced_row_falls_back_to_the_share_verdict_in_holders():
    rows = [_row(shares=500_000, price=0), _row(kind="S-Sale", shares=1000, price=100.0)]
    chart = _holders()._build_insider_smart_money(rows, {}, None, symbol="AAPL")
    assert chart.summary.net_flow_usd_millions is None
    assert chart.summary.is_positive is True, "judged on the shares, the only known figure"
    assert chart.summary.usd_estimated is None, "no dollars, so nothing to mark"


def test_an_unpriced_row_withholds_the_dollars_and_keeps_a_small_share_net():
    """2 dp of millions read a real 4,000-share net as 0: the badge prints this figure."""
    rows = [_row(shares=3000, price=0), _row(kind="S-Sale", shares=7000, price=10.0)]
    chart = _holders()._build_insider_smart_money(rows, {}, symbol="AAPL")
    assert chart.summary.total_sell_usd_millions is None
    assert chart.summary.total_net_flow == pytest.approx(-0.004)
    assert chart.summary.is_positive is False


def test_with_dollars_the_share_totals_keep_their_two_places():
    chart = _holders()._build_insider_smart_money([_row(shares=3333, price=10.0)], {})
    assert chart.summary.total_buy == 0.0


def test_another_issuer_is_unchanged_in_holders():
    rows = [_row(shares=1000, price=100.0, security="Class A Common Stock")]
    chart = _holders()._build_insider_smart_money(rows, {}, _daily(), symbol="GOOGL")
    assert chart.summary.total_buy_usd_millions == pytest.approx(0.1)


# ── Key Management and the Holders roster ────────────────────────────────────

def _buffett(**extra):
    return {
        "owner": "BUFFETT WARREN E", "display_name": "Warren E. Buffett",
        "title": "director, 10 percent owner", "typeOfOwner": "director, 10 percent owner",
        "numberOfShares": 188_290, "security": "Class A Common Stock", **extra,
    }


def test_buffett_is_valued_in_class_b_terms_and_his_share_count_names_its_class():
    km = C._build_key_management(
        [_buffett()], {}, 579.09, shares_outstanding=2_140_700_000, symbol="BRK-B",
    )
    row = (km["top_holders"] + km["officers"])[0]
    assert row["ownership_value"] == "$163.6B"
    assert row["percent_owned"] == pytest.approx(13.19, abs=0.01)
    assert row["ownership"] == "188K Class A", "188K beside $163.6B would not add up"


def test_a_class_b_holder_and_other_issuers_are_unchanged():
    km = C._build_key_management(
        [_buffett(security="Class B Common Stock", numberOfShares=1000)], {}, 579.09,
        shares_outstanding=2_140_700_000, symbol="BRK-B",
    )
    row = (km["top_holders"] + km["officers"])[0]
    assert row["ownership"] == "1K" and row["ownership_value"] == "$579K"
    other = C._build_key_management([_buffett()], {}, 100.0, shares_outstanding=1e9, symbol="GOOGL")
    assert (other["top_holders"] + other["officers"])[0]["ownership"] == "188K"


def test_a_13g_override_is_left_as_filed():
    """A 13G names no class, so the multiple must not touch it."""
    owners = [{"typeOfReportingPerson": "IN", "soleVotingPower": 1_000_000, "cik": "1",
               "percentOfClass": 40.0, "filingDate": "2026-01-01",
               "nameOfReportingPerson": "Warren E Buffett"}]
    km = C._build_key_management(
        [_buffett()], {}, 100.0, beneficial_owners=owners,
        shares_outstanding=1e9, symbol="BRK-B",
    )
    assert km["top_holders"][0]["ownership_value"] == "$100.0M"


def test_the_holders_roster_agrees_with_key_management():
    out = _holders()._build_top_insiders(
        [_buffett()], current_price=579.09, outstanding_shares=2_140_700_000, symbol="BRK-B",
    )
    assert out[0].value_in_millions == pytest.approx(163_558.0, abs=50)
    assert out[0].percent_ownership == pytest.approx(13.19, abs=0.01)


def test_an_erie_class_b_holder_is_valued_in_class_a_terms():
    km = C._build_key_management(
        [_buffett(security="Class B Common Stock", numberOfShares=1000)], {}, 350.0,
        shares_outstanding=52_000_000, symbol="ERIE",
    )
    row = (km["top_holders"] + km["officers"])[0]
    assert row["ownership_value"] == "$840.0M"


def test_the_holders_roster_ranks_on_the_converted_value():
    """1 Class A is $1.5M here and 1,000 Class B is $1M: raw share counts rank them backwards."""
    roster = [
        _buffett(owner="A", display_name="A", numberOfShares=1000,
                 security="Class B Common Stock"),
        _buffett(owner="B", display_name="B", numberOfShares=1),
    ]
    out = _holders()._build_top_insiders(roster, current_price=1000.0, symbol="BRK-B")
    assert [i.name for i in out] == ["B", "A"]
    assert [i.value_in_millions for i in out] == pytest.approx([1.5, 1.0])


# ── the forecast window ──────────────────────────────────────────────────────

def _estimates(years, rev=100e9, step=5e9, end="12-31"):
    return [
        {"date": f"{y}-{end}", "revenueAvg": rev + i * step, "epsAvg": 10.0 + i,
         "numAnalystsRevenue": 3, "numAnalystsEps": 3}
        for i, y in enumerate(years)
    ]


def _income(years, end="12-31"):
    return [{"date": f"{y}-{end}", "fiscalYear": str(y), "revenue": 95e9, "epsDiluted": 22.35}
            for y in years]


NOW = date(2026, 11, 5)


def test_a_reported_year_is_never_a_forecast():
    """BRK-B: FY2025 was reported, and only FY2026-28 are forward."""
    visible, anchor = C._select_visible_forecast_window(
        _estimates(range(2019, 2029)), NOW, income=_income(range(2021, 2026)),
    )
    assert [e["date"][:4] for e in visible] == ["2026", "2027", "2028"]
    assert anchor is None


def test_two_stale_rows_both_go():
    visible, _ = C._select_visible_forecast_window(
        _estimates(range(2023, 2028)), NOW, income=_income([2022, 2023, 2024, 2025]),
    )
    assert [e["date"][:4] for e in visible] == ["2026", "2027"]


def test_four_forward_years_are_untouched():
    visible, anchor = C._select_visible_forecast_window(
        _estimates(range(2024, 2032)), NOW, income=_income([2024, 2025]),
    )
    assert [e["date"][:4] for e in visible] == ["2026", "2027", "2028", "2029"]
    assert anchor["date"][:4] == "2025"


def test_two_unreported_years_keep_the_four_newest():
    """A company two years behind on its filings: every row is forward, the chart holds four."""
    visible, _ = C._select_visible_forecast_window(
        _estimates(range(2024, 2029)), NOW, income=_income([2022, 2023]),
    )
    assert [e["date"][:4] for e in visible] == ["2025", "2026", "2027", "2028"]


def test_a_failed_income_read_keeps_only_rows_still_ahead():
    """It must not bring a reported year back as a forecast."""
    visible, anchor = C._select_visible_forecast_window(
        _estimates(range(2019, 2029)), NOW, income=[],
    )
    assert [e["date"][:4] for e in visible] == ["2026", "2027", "2028"]
    assert anchor["date"][:4] == "2025"


def test_no_income_argument_keeps_the_old_fallback():
    visible, _ = C._select_visible_forecast_window(_estimates(range(2019, 2029)), NOW)
    assert [e["date"][:4] for e in visible] == ["2025", "2026", "2027", "2028"]


def test_an_all_past_list_keeps_its_last_four_even_with_a_failed_income_read():
    visible, anchor = C._select_visible_forecast_window(
        _estimates(range(2015, 2022)), NOW, income=[],
    )
    assert [e["date"][:4] for e in visible] == ["2018", "2019", "2020", "2021"]
    assert anchor["date"][:4] == "2017"


def test_a_year_ended_but_not_yet_reported_is_still_a_forecast():
    visible, _ = C._select_visible_forecast_window(
        _estimates([2025, 2026, 2027], end="09-30"), NOW, income=_income([2024], end="09-30"),
    )
    assert [e["date"][:4] for e in visible] == ["2025", "2026", "2027"]


def test_a_52_53_week_filer_cannot_slip_a_reported_year_through():
    """FY2025 ends 2026-01-31: its date looks like 2026, and only the fiscal year tells."""
    income = [{"date": "2026-02-01", "fiscalYear": "2025", "revenue": 95e9, "epsDiluted": 1.0}]
    estimates = _estimates([2026, 2027, 2028], end="01-31")
    estimates[0]["date"] = "2026-02-03"     # after the statement's period end: a date keeps it
    visible, _ = C._select_visible_forecast_window(estimates, NOW, income=income)
    assert [e["date"][:4] for e in visible] == ["2027", "2028"]


@pytest.mark.parametrize("years", [[Y - 2, Y - 1], [Y - 3, Y - 2, Y - 1]])
def test_no_forward_rows_means_no_projections(years):
    partial = C._build_revenue_forecast_partial(
        _estimates(years), None, None, income=_income([Y - 2, Y - 1]),
    )
    assert partial["projections"] == []


def test_the_first_bar_reads_against_the_last_reported_revenue_only():
    partial = C._build_revenue_forecast_partial(
        _estimates(range(Y - 7, Y + 3)), 3.0, 2.0, income=_income(range(Y - 5, Y)),
    )
    first = partial["projections"][0]
    assert first["period"] == str(Y)
    assert first["revenue_yoy_pct"] == pytest.approx((135e9 - 95e9) / 95e9 * 100, abs=0.1)
    assert first["eps_yoy_pct"] is None, "GAAP against consensus is not a growth rate"


def test_the_first_bar_prints_no_yoy_across_a_gap_year():
    """A filer two years behind: its first bar against the last reported year is a two-year
    change, not a YoY. The timeline refuses it too."""
    partial = C._build_revenue_forecast_partial(
        _estimates(range(Y - 2, Y + 3)), 3.0, 2.0, income=_income([Y - 4, Y - 3]),
    )
    assert partial["projections"][0]["period"] == str(Y - 1)
    assert partial["projections"][0]["revenue_yoy_pct"] is None
    assert partial["projections"][1]["revenue_yoy_pct"] is not None


def test_the_first_bar_prefers_its_estimate_anchor_over_the_reported_year():
    partial = C._build_revenue_forecast_partial(
        _estimates(range(Y - 1, Y + 7)), 3.0, 2.0, income=_income([Y - 2, Y - 1]),
    )
    first = partial["projections"][0]
    assert first["period"] == str(Y)
    assert first["revenue_yoy_pct"] == pytest.approx(5.0)


def test_the_cagr_uses_only_the_forward_years_the_chart_shows():
    computed = _metrics(estimates=_estimates(range(Y - 7, Y + 3)), income=_income(range(Y - 5, Y)))
    assert computed["revenue_cagr"] == C._safe_cagr(135e9, 145e9, 3)
    assert computed["eps_cagr"] == C._safe_cagr(17.0, 19.0, 3)


def test_one_forward_year_has_no_cagr():
    computed = _metrics(estimates=_estimates(range(Y - 7, Y + 1)), income=_income(range(Y - 5, Y)))
    assert computed["revenue_cagr"] is None and computed["eps_cagr"] is None


@pytest.mark.parametrize("bars", [1, 2])
def test_the_insight_never_prints_the_no_rate_placeholder(bars):
    """0.0 is how "no rate" arrives: one forward year, or EPS crossing from a loss to a profit
    with two or more."""
    persona = get_persona_config("warren_buffett")
    report = {"revenue_forecast": {"cagr": 0.0, "eps_growth": 0.0, "projections": [
        {"period": str(2026 + i), "revenue_label": "$1B", "eps_label": "$1"} for i in range(bars)
    ]}}
    assert "+0%" not in np_._revenue_forecast_insight_prompt(persona, "E", report)


def test_the_digest_states_a_real_rate_and_drops_the_placeholder():
    assert np_._digest_forecast({"revenue_forecast": {"cagr": 0.0, "eps_growth": 0.0}}) == []
    text = " ".join(np_._digest_forecast({"revenue_forecast": {"cagr": 3.2, "eps_growth": 0.0}}))
    assert "revenue CAGR +3.2%" in text and "EPS" not in text


# ── ROE on the card's period ─────────────────────────────────────────────────

def _metrics(**fields):
    out = C.CollectedTickerData(ticker="BRK-B", persona_key="warren_buffett", profile={})
    out.ratios = [{"returnOnEquity": 0.0933, "returnOnAssets": 0.05}]
    out.key_metrics = [{"returnOnEquity": 0.0933, "returnOnAssets": 0.05,
                        "returnOnInvestedCapital": 0.041}]
    for key, value in fields.items():
        setattr(out, key, value)
    C.TickerReportDataCollector()._compute_metrics(out)
    return out.computed


def test_roe_reads_the_cards_period():
    computed = _metrics(focal_km_ttm={"returnOnEquityTTM": 0.1186, "returnOnAssetsTTM": 0.0679})
    assert computed["roe"] == 11.9 and computed["roa"] == 6.8


def test_roe_falls_back_to_the_fiscal_year_without_a_ttm_row():
    assert _metrics()["roe"] == 9.3
    assert _metrics(focal_km_ttm={"returnOnEquityTTM": None})["roe"] == 9.3
    assert _metrics(focal_km_ttm={"returnOnEquityTTM": float("nan")})["roe"] == 9.3


@pytest.mark.parametrize("ttm, expected", [(-0.25, -25.0), (0.0, 0.0)])
def test_a_loss_or_a_real_zero_is_a_reading(ttm, expected):
    assert _metrics(focal_km_ttm={"returnOnEquityTTM": ttm})["roe"] == pytest.approx(expected)


def _pass_two(monkeypatch, km):
    from tests.test_ticker_report_collector_math import _PeerFmp, _stub_pass_two
    coll, C2 = _stub_pass_two(monkeypatch)
    coll.fmp = _PeerFmp(**km)
    out = C2.CollectedTickerData(ticker="AVGO", persona_key="warren_buffett", profile={})
    return coll, out


def test_the_ttm_row_reaches_the_metrics_when_there_are_peers(monkeypatch):
    coll, out = _pass_two(monkeypatch, {"km_ttm": [{
        "returnOnInvestedCapitalTTM": 0.05, "returnOnEquityTTM": 0.1186,
    }]})
    out.peer_tickers = ["CB"]
    asyncio.run(coll._fetch_dependent(out))
    assert out.focal_km_ttm["returnOnEquityTTM"] == 0.1186
    assert out.focal_roic_ttm == 0.05


def test_a_failed_ttm_read_leaves_the_annual_figures(monkeypatch):
    coll, out = _pass_two(monkeypatch, {"km_raises": True})
    out.peer_tickers = ["CB"]
    asyncio.run(coll._fetch_dependent(out))
    assert out.focal_km_ttm == {}


def test_a_company_with_no_peers_spends_no_call_and_keeps_the_annual_figures(monkeypatch):
    coll, out = _pass_two(monkeypatch, {})
    asyncio.run(coll._fetch_dependent(out))
    assert coll.fmp.calls == [] and out.focal_km_ttm == {}


def test_roic_reads_the_ttm_figure_the_peers_are_scored_on():
    assert _metrics(focal_km_ttm={"returnOnInvestedCapitalTTM": 0.07})["roic"] == 7.0
    assert _metrics()["roic"] == 4.1


@pytest.mark.parametrize("ttm, expected", [(0.0, 0.0), (float("nan"), 4.1), (None, 4.1)])
def test_a_real_zero_roic_is_a_reading_and_nan_is_not(ttm, expected):
    assert _metrics(focal_km_ttm={"returnOnInvestedCapitalTTM": ttm})["roic"] == expected


def test_the_peer_tables_roce_fallback_never_becomes_the_style_scores_roic():
    """`focal_roic_ttm` falls back to ROCE, which is not ROIC: 31.0 against a real 4.1."""
    assert _metrics(focal_roic_ttm=0.31)["roic"] == 4.1


def test_the_annual_roe_fallback_reads_ratios_before_key_metrics():
    assert _metrics(ratios=[{"returnOnEquity": 0.2}])["roe"] == 20.0


# ── the largest stake anchors the Key Management insight ─────────────────────

def _anchor(km):
    prompt = np_._key_management_insight_prompt(
        get_persona_config("warren_buffett"), "E", {"key_management": km},
    )
    return next(line for line in prompt.splitlines() if "DOMINANT HOLDER" in line)


def test_an_old_13g_does_not_outrank_a_larger_current_stake():
    line = _anchor({
        "top_holders": [{"name": "Old Filer", "percent_ownership": 5.0, "ownership_value": "$3.8B"}],
        "officers": [{"name": "Warren E. Buffett", "percent_owned": 13.193522,
                      "ownership_value": "$163.6B"}],
    })
    assert "Warren E. Buffett holds 13.2%" in line


def test_a_stake_of_5_percent_or_more_anchors_with_no_top_holder():
    line = _anchor({"officers": [{"name": "F", "percent_owned": 6.0, "ownership_value": "$1B"}]})
    assert "F holds 6.0%" in line


def test_a_small_officer_stake_is_not_dominant():
    line = _anchor({"officers": [{"name": "F", "percent_owned": 0.4, "ownership_value": "$1M"}]})
    assert "none holds 5%+" in line


def test_a_top_holder_with_no_percent_prints_no_percent():
    line = _anchor({"top_holders": [{"name": "T", "ownership_value": "$1B"}]})
    assert "T holds ($1B)" in line and "None" not in line and "0.0%" not in line


# ── Ask Cay AI's ownership tool ──────────────────────────────────────────────

def _chat_block(rows, *, symbol="BRK-B", close_on=None):
    svc = _holders()
    acts = svc._build_insider_activities(rows, [], symbol=symbol, close_on=close_on)
    return chat._insider_activity_block(SimpleNamespace(
        insider_data=svc._build_insider_smart_money(rows, {}, symbol=symbol, close_on=close_on),
        recent_activities=SimpleNamespace(insider_activities=SimpleNamespace(
            activities=acts, summary=svc._build_insider_activity_summary(acts), unavailable=None,
        )),
        ownership_detail=None,
    ), symbol=symbol)


def test_chat_never_says_a_valued_trade_is_outside_the_dollars():
    block = _chat_block(_berkshire_window(), close_on=_close_on())
    text = block["last_12_months"]
    assert "not in the dollar figures" not in text
    assert "20 trade(s) reported no price and are valued at that day's close" in text
    assert "net selling" in text, "every row has a value, so the direction may be stated"


def test_chat_keeps_its_short_windows_on_the_same_basis():
    block = _chat_block(_berkshire_window(), close_on=_close_on())
    assert "$15.50 million" in block["last_6_months"]
    assert "not in the dollar figures" not in block["last_6_months"]
    assert "20 trade(s) reported no price and are valued" in block["last_6_months"]


def test_chat_still_withholds_the_direction_when_a_trade_has_no_value():
    rows = [_row(shares=1000, price=100.0), _row(kind="S-Sale", shares=500_000, price=0)]
    block = _chat_block(rows, symbol="AAPL")
    assert "1 trade(s) reported no price and are not in the dollar figures" in block["last_6_months"]
    assert "net buying" not in block["last_6_months"]


def test_chat_says_which_class_the_share_totals_are_in():
    assert "Class B terms" in _chat_block(_berkshire_window())["share_classes"]
    assert "share_classes" not in _chat_block([_row(price=10.0)], symbol="AAPL")


def test_a_persons_trade_line_never_merges_two_classes():
    built = ih._trades([ih._Row(
        order=0, reporter="9", name="A", title="CEO", share_class=cls, held="direct",
        security="x", date="2026-10-01", filed="2026-10-02", after=1.0, change=1.0,
        shares=shares, price=price, code="P-Purchase", acquired=True,
    ) for cls, shares, price in (("class-a", 2.0, 750_000.0), ("class-b", 200.0, 500.0))])
    assert sorted(t["shares"] for t in built) == [2, 200]
    assert sorted(t["average_price"] for t in built) == [500.0, 750_000.0]


def test_the_roster_picks_the_larger_stake_by_worth_across_classes():
    entries = [
        {"held": "direct", "security": "Class A Common Stock", "shares": 5000,
         "as_of": "2026-01-01"},
        {"held": "direct", "security": "Class B Common Stock", "shares": 2_000_000,
         "as_of": "2026-09-01"},
    ]
    assert ih._traded_direct(entries, "BRK-B")["security"] == "Class A Common Stock"
    # With one value per share it stays the newest line, as before, even a smaller one.
    entries[0]["shares"], entries[1]["shares"] = 2_000_000, 5000
    assert ih._traded_direct(entries, "GOOGL")["security"] == "Class B Common Stock"


# ── the Overview card, the PDF and the versions ──────────────────────────────

def test_the_overview_card_marks_an_estimate():
    assert snapshot._fmt_usd_flow(4.5, False, True) == "Net Sell ~$4.50M"
    assert snapshot._fmt_usd_flow(4.5, False) == "Net Sell $4.50M"


def test_the_overview_card_reads_the_holders_flag(monkeypatch):
    service = object.__new__(snapshot.OwnershipSnapshotService)
    item = service._compute_from_holders(_holders_build(monkeypatch))
    row = next(m for m in item.metrics if m.name == "Insider Activity (12M)")
    assert row.value == "Net Buy ~$2.50M"


def test_the_ownership_snapshot_version_moved_with_holders():
    assert snapshot._SNAPSHOT_PAYLOAD_VERSION == 5


# ── end to end ───────────────────────────────────────────────────────────────

def _abel_rows():
    rows = [_row(shares=1, price=0, security="Class A Common Stock") for _ in range(4)]
    rows.append(_row(kind="S-Sale", shares=1000, price=500.0))
    for row in rows:
        row.update(symbol="BRK-B", companyCik="0001067983", reportingCik="9",
                   filingDate=_day(29), directOrIndirect="D",
                   acquisitionOrDisposition="D" if row["transactionType"] == "S-Sale" else "A")
    return rows


class _HoldersFmp:
    def __init__(self, rows, profile):
        self.rows, self.profile = rows, profile

    async def get_shares_float(self, t): return {"freeFloat": 60.0, "outstandingShares": 2.1e9}
    async def get_institutional_holder(self, t, limit=20): return []
    async def get_institutional_ownership_summary(self, t): return {"cik": "0001067983"}
    async def get_institutional_ownership_for_quarter(self, t, y, q, strict=False): return None
    async def get_insider_trades_since(self, since, *, symbol=None, page_size=1000,
                                       max_pages=5, transaction_type=None):
        return self.rows
    async def get_company_profile(self, t): return self.profile
    async def get_insider_roster(self, t): return []
    async def get_historical_prices(self, t, from_date=None, to_date=None):
        return _closes(500.0)
    async def get_senate_latest(self, limit=1000): return []
    async def get_house_latest(self, limit=1000): return []
    async def get_senate_disclosure(self, t): return []
    async def get_house_disclosure(self, t): return []
    async def get_stock_price_quote(self, t): return {"price": 500.0}


class _Actions(_Splits):
    async def has_unclassified_adjustment(self, *a, **k):
        return False


def _holders_build(monkeypatch, profile=None, splits=()):
    from app.services import holders_service as hs
    from tests._price_fakes import PriceFromFMPFake
    from tests.test_chat_ownership_tool import _Supabase

    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_inflight", {})
    monkeypatch.setattr(hs, "_background_tasks", set())
    fmp = _HoldersFmp(_abel_rows(), profile or {"symbol": "BRK-B"})
    svc = object.__new__(HoldersService)
    actions = _Actions(None if splits is None else list(splits))   # None = could not check
    svc.fmp, svc.price, svc.corporate_actions = fmp, PriceFromFMPFake(fmp), actions
    svc.supabase = _Supabase()
    return asyncio.run(svc.get_holders_with_status("BRK-B"))[0]


def test_a_real_holders_build_values_and_converts_the_rows(monkeypatch):
    resp = _holders_build(monkeypatch)
    summary = resp.insider_data.summary
    assert summary.total_buy_usd_millions == pytest.approx(3.0)       # 4 x 1,500 x $500
    assert summary.total_sell_usd_millions == pytest.approx(0.5)
    assert summary.usd_estimated is True
    acts = resp.recent_activities.insider_activities.activities
    assert sum(a.price_estimated is True for a in acts) == 4
    assert {a.price_at_transaction for a in acts} == {500.0}


def test_a_real_holders_build_on_an_adr_values_nothing_at_a_close(monkeypatch):
    summary = _holders_build(monkeypatch, profile={"isAdr": True}).insider_data.summary
    assert summary.total_buy_usd_millions is None and summary.usd_estimated is None


def test_a_real_holders_build_refuses_a_close_before_a_later_split(monkeypatch):
    summary = _holders_build(monkeypatch, splits=[{"date": _day(5)}]).insider_data.summary
    assert summary.total_buy_usd_millions is None


def test_a_real_holders_build_refuses_a_close_when_splits_could_not_be_read(monkeypatch):
    summary = _holders_build(monkeypatch, splits=None).insider_data.summary
    assert summary.total_buy_usd_millions is None


class _NoLookup:
    def get_benchmark_series(self, industry, sector, metrics, period_type):
        return {}

    def get_benchmarks(self, industry, sector, metrics, period_type):
        return {}

    def get_current_benchmarks(self, industry, sector, metrics):
        return {}


def _built(monkeypatch, *, split_days=(), historical=None, profile=None):
    monkeypatch.setattr("app.services.sector_benchmark_lookup.get_sector_benchmark_lookup",
                        lambda: _NoLookup())
    out = C.CollectedTickerData(
        ticker="BRK-B", persona_key="warren_buffett",
        profile=dict(profile or {}, price=500.0, cik="0001067983"),
    )
    out.insider_trades = _abel_rows()
    out.insider_split_days = None if split_days is None else list(split_days)
    out.historical = {"historical": _closes(500.0)} if historical is None else historical
    out.income = _income([Y - 2, Y - 1])
    coll = C.TickerReportDataCollector.__new__(C.TickerReportDataCollector)
    coll._compute_metrics(out)
    coll._build_sections(out)
    return out


def test_the_real_report_path_values_and_converts_the_rows(monkeypatch):
    out = _built(monkeypatch)
    buys, sells = out.insider_data_partial["transactions"]
    assert buys["value"] == "~$3.0M" and buys["shares"] == "6K"
    assert sells["value"] == "$500K"
    assert out.insider_vital_partial["estimated_value"] is True
    assert "insider_data:closes_unavailable" not in out.degraded_sections


def test_stage_a_evidence_says_what_an_estimate_is(monkeypatch):
    out = _built(monkeypatch)
    assert ic.INSIDER_VALUE_NOTE in C.build_financial_context(out)
    out.insider_data_partial["transactions"] = [
        {"type": "Buys", "count": 1, "shares": "1K", "value": "$1.0M"},
    ]
    assert ic.INSIDER_VALUE_NOTE not in C.build_financial_context(out)


def test_the_real_report_path_keeps_an_adr_unpriced_and_shareable(monkeypatch):
    out = _built(monkeypatch, profile={"isAdr": True})
    assert out.insider_vital_partial["unpriced_rows"] == 4
    assert "insider_data:closes_unavailable" not in out.degraded_sections, "permanent, not a blip"


def test_a_row_before_a_split_stays_unpriced_on_the_real_report_path(monkeypatch):
    out = _built(monkeypatch, split_days=[_day(5)])
    assert out.insider_vital_partial["unpriced_rows"] == 4


@pytest.mark.parametrize("failure", [{"historical": {}}, {"split_days": None}])
def test_a_failed_read_keeps_the_verdictless_collection_out_of_the_shared_caches(
    monkeypatch, failure,
):
    """A blip must not freeze "no verdict" for every persona until the close."""
    out = _built(monkeypatch, **failure)
    assert out.insider_vital_partial["unpriced_rows"] == 4
    assert "insider_data:closes_unavailable" in out.degraded_sections


def test_pass_two_reads_splits_only_for_a_row_with_no_price(monkeypatch):
    coll, out = _pass_two(monkeypatch, {})
    coll.corporate_actions = _Actions([{"date": "2026-06-12"}])
    out.insider_trades = [_row(price=0)]
    asyncio.run(coll._fetch_dependent(out))
    assert out.insider_split_days == ["2026-06-12"]

    coll.corporate_actions = _Actions(RuntimeError("429"))
    asyncio.run(coll._fetch_dependent(out))
    assert out.insider_split_days is None

    coll.corporate_actions = _Actions(AssertionError("must not be asked"))
    out.insider_trades = [_row(price=10.0)]
    asyncio.run(coll._fetch_dependent(out))
    assert out.insider_split_days == []


def test_a_split_helper_that_raises_reads_as_could_not_check(monkeypatch):
    coll, out = _pass_two(monkeypatch, {})

    async def boom(*_a):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(C, "insider_split_days", boom)
    out.insider_trades = [_row(price=0)]
    asyncio.run(coll._fetch_dependent(out))
    assert out.insider_split_days is None


# ── wiring ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path, name, required", [
    ("app/services/agents/ticker_report_data_collector.py", "_build_insider_sections",
     {"symbol", "close_on"}),
    ("app/services/agents/ticker_report_data_collector.py", "_build_key_management", {"symbol"}),
    ("app/services/agents/ticker_report_data_collector.py", "_select_visible_forecast_window",
     {"income"}),
    ("app/services/agents/ticker_report_data_collector.py", "roster_from_holdings", {"symbol"}),
    ("app/services/holders_service.py", "_build_insider_smart_money", {"symbol", "close_on"}),
    ("app/services/holders_service.py", "_build_top_insiders", {"symbol"}),
    ("app/services/holders_service.py", "_build_insider_activities", {"symbol", "close_on"}),
    ("app/services/holders_service.py", "_build_recent_activities", {"symbol", "close_on"}),
    ("app/services/holders_service.py", "roster_from_holdings", {"symbol"}),
    ("app/services/chat_ownership_tool.py", "_insider_activity_block", {"symbol"}),
])
def test_every_production_call_passes_what_the_fix_reads(path, name, required):
    """Each of these defaults to "no class, no closes", which would quietly undo the fix for a
    caller that forgets it: the builder tests above would stay green."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and name == getattr(
            node.func, "attr", getattr(node.func, "id", None),
        )
    ]
    assert calls, f"no call of {name}"
    for call in calls:
        assert required <= {kw.arg for kw in call.keywords}, f"{name} at line {call.lineno}"


# ── the PDF ──────────────────────────────────────────────────────────────────

def _pdf_context(rows, forecast=None):
    from app.services.pdf_report_service import build_context

    return build_context({
        "insider_data": {"recent_transactions": {"activities": rows}},
        "revenue_forecast": forecast or {},
    })


def test_the_pdf_marks_a_value_built_on_a_close():
    rows = [
        {"name": "A", "change_in_millions": 0.003, "price_at_transaction": 500.0,
         "price_estimated": True},
        {"name": "B", "change_in_millions": 0.003, "price_at_transaction": 500.0},
    ]
    recent = _pdf_context(rows)["insider"]["recent"]
    assert recent[0]["value_label"].startswith("~$")
    assert recent[1]["value_label"].startswith("$")


def test_the_pdf_shows_a_dash_for_the_no_rate_placeholder():
    forecast = _pdf_context([], {"cagr": 0.0, "eps_growth": 3.0})["forecast"]
    assert forecast["cagr"] is None and forecast["eps_growth"] == 3.0
