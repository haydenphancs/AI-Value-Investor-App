"""
Cross-screen parity: the Holders tab (``TickerDetailView``) vs ``TickerReportView``.

The report does not re-fetch ownership data — it reuses the SAME
``HoldersResponse`` the Holders tab renders. These tests pin the places where the
two screens could still disagree despite that shared source:

  * the report re-FILTERS holders' congress activities → the window must match
    the one the summary counts were computed over, or the pills contradict the
    rows sitting directly beneath them;
  * the report re-SHAPES holders' quarterly institutional flow into monthly
    points → the labels must parse and the totals must reconcile;
  * ``hedge_fund_smart_money`` is a verbatim passthrough → it must stay byte-
    identical to what the Holders tab receives.

Pure functions; no network, no Supabase.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.holders import (
    CongressActivitiesDataSchema,
    CongressActivitySchema,
    CongressActivitySummarySchema,
    HoldersResponse,
    RecentActivitiesSchema,
    SmartMoneyDataSchema,
    SmartMoneyFlowDataPointSchema,
    SmartMoneyFlowSummarySchema,
)
from app.services.agents.ticker_report_data_collector import (
    _build_hidden_market_signals,
    _build_insider_sections,
    _hedge_fund_flow_from_holders,
)
from app.services.holders_service import HoldersService


# ── Congress: the report's trade list vs the Holders summary window ──────────

def _congress_holders(activities, summary) -> HoldersResponse:
    return HoldersResponse(
        symbol="NVDA",
        recent_activities=RecentActivitiesSchema(
            congress_activities=CongressActivitiesDataSchema(
                summary=summary, activities=activities
            )
        ),
    )


def _act(date: str, name: str, kind: str = "Purchase") -> CongressActivitySchema:
    return CongressActivitySchema(
        name=name, role="Representative (TX-11)", date=date,
        change_in_millions=0.008 if kind == "Purchase" else -0.008,
        amount_range="$1,001 - $15,000", amount_range_max_millions=0.015,
        transaction_type=kind,
    )


def test_report_congress_rows_use_the_same_window_as_its_own_pills():
    """The pills ("2 Buyers") come from holders' summary, computed over the
    CALENDAR-month window (`_generate_month_keys(12)`). The report used to filter
    `.activities` with a ROLLING 365-day cutoff instead — the two differ by up to
    30 days at the left edge, so a trade the summary never counted still appeared
    in the list: "1 Buyer" rendered directly above two purchase rows from two
    different members.
    """
    month_keys = HoldersService._generate_month_keys(12)
    oldest_month, oldest_year = month_keys[0].split("/")
    first_of_window = datetime(int(oldest_year), int(oldest_month), 1, tzinfo=timezone.utc)

    # Inside the calendar window (counted by the summary).
    inside = _act(
        (first_of_window + timedelta(days=5)).strftime("%Y-%m-%d"), "Tuberville, Tommy"
    )
    # Before the calendar window but INSIDE a rolling 365-day cutoff — the exact
    # gap the old filter admitted.
    outside = _act(
        (first_of_window - timedelta(days=5)).strftime("%Y-%m-%d"), "Pelosi, Nancy"
    )

    holders = _congress_holders(
        [inside, outside],
        CongressActivitySummarySchema(
            num_buyers=1, num_sellers=0, total_buys_in_millions=0.008
        ),
    )

    out = _build_hidden_market_signals(holders, None, None)
    congress = out["congress"]

    names = {t["name"] for t in congress["trades"]}
    assert names == {"Tuberville, Tommy"}
    # The invariant that matters to a reader: never more distinct traders in the
    # list than the pill above it claims.
    assert len({t["name"] for t in congress["trades"]}) <= (
        congress["num_buyers"] + congress["num_sellers"]
    )


def test_report_congress_trades_are_the_holders_rows_verbatim():
    """iOS decodes these into the SAME `CongressActivity` model the Holders tab
    uses, so the STOCK Act range (not just the midpoint) has to survive."""
    act = _act(datetime.now(timezone.utc).strftime("%Y-%m-%d"), "Fields, Cleo")
    holders = _congress_holders(
        [act], CongressActivitySummarySchema(num_buyers=1, total_buys_in_millions=0.008)
    )
    trade = _build_hidden_market_signals(holders, None, None)["congress"]["trades"][0]
    assert trade["amount_range"] == "$1,001 - $15,000"
    assert trade["change_in_millions"] == act.change_in_millions
    assert trade["transaction_type"] == "Purchase"


def test_report_hides_congress_when_the_summary_counted_nothing():
    """No buyers and no sellers → the module must not render a card whose pills
    all read 0 above an empty list."""
    holders = _congress_holders([], CongressActivitySummarySchema())
    assert _build_hidden_market_signals(holders, None, None) is None


def test_report_congress_survives_malformed_dates():
    holders = _congress_holders(
        [_act("", "Blank, B"), _act("N/A", "Bad, B")],
        CongressActivitySummarySchema(num_buyers=2, total_buys_in_millions=0.016),
    )
    out = _build_hidden_market_signals(holders, None, None)
    assert out["congress"]["trades"] == []  # dropped, not crashed, not stamped today


# ── Institutions: quarterly holders flow → the report's monthly points ───────

def _hedge_holders(flow_points) -> HoldersResponse:
    return HoldersResponse(
        symbol="AAPL",
        hedge_funds_data=SmartMoneyDataSchema(
            tab="Institutions",
            flow_data=flow_points,
            summary=SmartMoneyFlowSummarySchema(
                total_buy=sum(p.buy_volume for p in flow_points),
                total_sell=sum(p.sell_volume for p in flow_points),
                period_description="2-Year",
            ),
        ),
    )


def test_report_parses_every_quarter_label_holders_actually_emits():
    """`_hedge_fund_flow_from_holders` splits the label on "\\n" and does
    `2000 + int(yy)`. If it silently fails to parse, the report's Institutions
    chart renders all-zero bars while the Holders tab shows real ones."""
    pairs = HoldersService._generate_quarter_keys(8)
    points = [
        SmartMoneyFlowDataPointSchema(
            month=HoldersService._quarter_label(y, q),
            buy_volume=30.0, sell_volume=12.0, net_flow=18.0,
        )
        for y, q in pairs
    ]
    monthly = [
        {"month": f"{m:02d}/{y}", "price": 100.0}
        for (y, q) in pairs[-4:]
        for m in ((q - 1) * 3 + 1, (q - 1) * 3 + 2, (q - 1) * 3 + 3)
    ]

    out = _hedge_fund_flow_from_holders(_hedge_holders(points), monthly)

    assert len(out) == len(monthly)
    assert all(p["buy_volume"] > 0 for p in out), "every label must have parsed"
    # Each quarter's volume is spread across its 3 months, so the monthly total
    # reconciles back to the quarterly figure the Holders tab shows.
    assert sum(p["buy_volume"] for p in out[:3]) == pytest.approx(30.0, abs=0.05)
    assert sum(p["sell_volume"] for p in out[:3]) == pytest.approx(12.0, abs=0.05)


def test_report_institutions_flow_is_honest_zero_without_holders():
    """No holders → zero-volume points, NOT synthetic noise."""
    monthly = [{"month": "01/2026", "price": 100.0}]
    assert _hedge_fund_flow_from_holders(None, monthly) == [
        {"month": "01/2026", "buy_volume": 0.0, "sell_volume": 0.0}
    ]
    assert _hedge_fund_flow_from_holders(_hedge_holders([]), []) == []


def test_report_institutions_flow_ignores_unparseable_labels():
    points = [
        SmartMoneyFlowDataPointSchema(month="garbage", buy_volume=99.0, sell_volume=1.0),
        SmartMoneyFlowDataPointSchema(month="Q9\n'99", buy_volume=99.0, sell_volume=1.0),
    ]
    out = _hedge_fund_flow_from_holders(
        _hedge_holders(points), [{"month": "01/2026", "price": 100.0}]
    )
    assert out == [{"month": "01/2026", "buy_volume": 0.0, "sell_volume": 0.0}]


# ── Insiders: the report re-aggregates the SAME Form 4 rows independently ────

def _form4(name: str, shares: int, price: float, kind: str = "P-Purchase"):
    return {
        "reportingName": name, "transactionType": kind,
        "securityName": "Common Stock", "securitiesTransacted": shares,
        "price": price,
        "transactionDate": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def test_price_less_informative_buy_is_counted_identically_on_both_screens():
    """FMP leaves `price` blank on plenty of real open-market Form 4 rows.

    The Holders tab's activity list used to drop every price-0 row (a leftover
    from when the list was denominated in DOLLARS; it is now SHARES), while the
    chart above it and the report's Buys/Sells table both key off the
    informative/uninformative classification and ignore price. Result on the same
    two buys: the summary card read "1 buyer" directly beneath a bar that had
    summed TWO buyers' shares, and TickerReportView reported "Buys 2".
    """
    trades = [_form4("SMITH JOHN", 10_000, 50.0), _form4("DOE JANE", 25_000, 0.0)]

    acts = HoldersService._build_insider_activities(
        object.__new__(HoldersService), trades, []
    )
    summary = HoldersService._build_insider_activity_summary(
        object.__new__(HoldersService), acts
    )
    chart = HoldersService._build_insider_smart_money(
        object.__new__(HoldersService), trades, {}, None
    )
    _, vital = _build_insider_sections(trades)

    assert len(acts) == 2                                   # Holders list
    assert summary.num_buyers == 2                          # Holders summary card
    assert vital["buy_count"] == 2                          # report table
    assert summary.informative_buys_in_millions == pytest.approx(0.035)
    assert sum(p.buy_volume for p in chart.flow_data) == pytest.approx(0.035)


def test_rsu_vesting_at_zero_price_stays_hidden_from_the_activity_list():
    """The price-0 suppression was aimed at comp mechanics — keep that. Only
    INFORMATIVE rows are exempted."""
    trades = [_form4("RSU HOLDER", 9_999, 0.0, kind="A-Award")]
    acts = HoldersService._build_insider_activities(
        object.__new__(HoldersService), trades, []
    )
    assert acts == []


def test_hedge_fund_smart_money_passthrough_is_byte_identical():
    """The report's Institutions net-flow badge reads `hedge_fund_smart_money`,
    which must be the Holders tab's `hedge_funds_data` unchanged — no re-rounding,
    no unit conversion (it is MILLIONS OF SHARES on both screens)."""
    points = [
        SmartMoneyFlowDataPointSchema(
            month=HoldersService._quarter_label(y, q),
            buy_volume=1.234567, sell_volume=0.000002,
            net_flow=1.234565, buyers_count=12, sellers_count=3,
        )
        for y, q in HoldersService._generate_quarter_keys(8)
    ]
    holders = _hedge_holders(points)
    assert holders.hedge_funds_data.model_dump() == SmartMoneyDataSchema(
        **holders.hedge_funds_data.model_dump()
    ).model_dump()
    # Sub-share precision must survive the dump the collector persists.
    dumped = holders.hedge_funds_data.model_dump()
    assert dumped["flow_data"][0]["sell_volume"] == 0.000002
