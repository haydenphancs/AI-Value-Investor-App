"""Ask Cay AI's ownership tool says degradation, and describes ONE read (review, 2026-10-08).

  * Congress (Pro and above): holders substitutes an EMPTY list for a failed chamber feed and
    names it in `degraded`. The block used to print "0 member(s) …" and "No congressional
    disclosure of this stock was found" when every feed had failed, and the House rows as if
    complete when only the Senate had. Now: ``complete: False``, "at least" counts, and never
    "none found".
  * Freshness: after a forced rebuild (a new Form 4), `insider_activity` comes from the SAME
    build as `insiders` — the rebuild's chart and list are kept beside its read
    (`_InsiderView`). When they are not held, the windows say they predate the list.
  * The 12-month line counts the open-market trades that reported no price, as the 3- and
    6-month lines do (the card's dollars sum priced trades only).

Hermetic: fake holders service; side reads stubbed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.schemas.holders import (
    CongressActivitiesDataSchema,
    CongressActivitySchema,
    CongressActivitySummarySchema,
    HoldersResponse,
    InsiderActivitiesDataSchema,
    InsiderActivitySchema,
    InsiderActivitySummarySchema,
    InsiderHoldingSchema,
    InsiderHoldingsSchema,
    InsiderOwnerSchema,
    OwnershipDetailSchema,
    RecentActivitiesSchema,
    SmartMoneyDataSchema,
    SmartMoneyFlowDataPointSchema,
    SmartMoneyFlowSummarySchema,
)
from app.services import chat_ownership_tool as cot
from app.services import holders_service as hs

_MEMBER_NAMES = ("Pelosi", "Tuberville")
_ALL_FEEDS = ["Senate latest", "House latest", "Senate disclosure", "House disclosure"]


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _month(dt: datetime) -> str:
    return f"{dt.month:02d}/{dt.year}"


def _owner(shares: float, as_of: str) -> InsiderOwnerSchema:
    return InsiderOwnerSchema(
        name="Brian M. Venturo", role="director", latest_transaction_date=as_of,
        holdings=[InsiderHoldingSchema(security="Class A Common Stock", held="direct",
                                       shares=shares, as_of=as_of)])


def _act(date: str, shares: float, price, kind: str = "Informative Sell") -> InsiderActivitySchema:
    sign = -1 if "Sell" in kind else 1
    return InsiderActivitySchema.model_construct(
        name="Brian M. Venturo", title="director", date=date,
        change_in_millions=sign * shares / 1e6, transaction_type=kind,
        price_at_transaction=price)


def _resp(*, built_at: str, shares: float = 241371, acts=None, flow=None, summary=None,
          card=None, congress=None, smart_unavailable=None) -> HoldersResponse:
    return HoldersResponse(
        symbol="CRWV",
        insider_data=SmartMoneyDataSchema(tab="Insider", flow_data=flow or [],
                                          summary=card or SmartMoneyFlowSummarySchema(),
                                          unavailable=smart_unavailable),
        recent_activities=RecentActivitiesSchema(
            insider_activities=InsiderActivitiesDataSchema(
                summary=summary or InsiderActivitySummarySchema(), activities=acts or []),
            congress_activities=congress or CongressActivitiesDataSchema(),
        ),
        ownership_detail=OwnershipDetailSchema(
            insider_holdings=InsiderHoldingsSchema(insiders=[_owner(shares, "2026-09-18")]),
            built_at=built_at),
    )


def _congress(rows: int = 2) -> CongressActivitiesDataSchema:
    acts = [
        CongressActivitySchema(name="Pelosi, Nancy", role="Representative (CA-11)",
                               date="2026-07-20", amount_range="$1,001 - $15,000",
                               transaction_type="Purchase", disclosure_date="2026-08-01"),
        CongressActivitySchema(name="Tuberville, Tommy", role="Senator (AL)", date="2026-06-02",
                               amount_range="$50,001 - $100,000", transaction_type="Sale"),
    ][:rows]
    return CongressActivitiesDataSchema(
        summary=CongressActivitySummarySchema(num_buyers=1 if rows else 0,
                                              num_sellers=1 if rows > 1 else 0),
        activities=acts)


class _Holders:
    """The cached build, a probe answer and the forced rebuild."""

    def __init__(self, cached, *, degraded=(), fresh=None, fresh_degraded=(), newer=False):
        self.cached, self.degraded = cached, list(degraded)
        self.fresh, self.fresh_degraded, self.newer = fresh, list(fresh_degraded), newer
        self.calls = []

    async def get_holders_with_status(self, ticker, *, force_refresh=False):
        self.calls.append(force_refresh)
        if force_refresh:
            return self.fresh, list(self.fresh_degraded)
        return self.cached, list(self.degraded)

    async def newer_insider_filing(self, ticker, detail):
        return self.newer


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _no_short(sym):
        return {}

    async def _no_profile(sym):
        return None

    monkeypatch.setattr(cot, "_load_short_interest", _no_short)
    monkeypatch.setattr(cot, "_issuer_profile_flags", _no_profile)
    monkeypatch.setattr(cot, "_side_tasks", set())
    monkeypatch.setattr(cot, "_fresh_reads", {})
    monkeypatch.setattr(cot, "_fresh_views", {})
    monkeypatch.setattr(cot, "_last_probe", {})
    monkeypatch.setattr(cot, "_probe_inflight", {})


def _serve(monkeypatch, fake):
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    return fake


# ── Congress: a failed feed is said, never zeroed ─────────────────────────────────────

@pytest.mark.asyncio
async def test_every_congress_feed_failed_is_incomplete_never_none_found(monkeypatch):
    _serve(monkeypatch, _Holders(_resp(built_at=_iso(0), congress=CongressActivitiesDataSchema()),
                                 degraded=_ALL_FEEDS))
    block = (await cot.fetch_ownership("CRWV", user_tier="pro"))["congress"]
    assert block["complete"] is False
    assert "No congressional disclosure" not in json.dumps(block)
    assert "last_12_months" not in block, "never '0 member(s) disclosed purchases'"
    assert "House and Senate disclosure feed(s) could not be loaded" in block["note"]
    assert "never say there are none" in block["note"]


@pytest.mark.asyncio
async def test_only_the_senate_failed_lists_the_house_rows_as_incomplete(monkeypatch):
    _serve(monkeypatch, _Holders(_resp(built_at=_iso(0), congress=_congress(1)),
                                 degraded=["Senate latest", "Senate disclosure", "Quote"]))
    block = (await cot.fetch_ownership("CRWV", user_tier="premium"))["congress"]
    assert block["complete"] is False
    assert block["disclosures"][0].startswith("Pelosi, Nancy (Representative (CA-11))")
    assert block["last_12_months"] == ("at least 1 member(s) disclosed purchases and at least 0 "
                                       "disclosed sales (incomplete)")
    assert block["note"].startswith("The Senate disclosure feed(s) could not be loaded")


@pytest.mark.asyncio
async def test_a_complete_build_keeps_its_counts_and_its_none_found(monkeypatch):
    _serve(monkeypatch, _Holders(_resp(built_at=_iso(0), congress=CongressActivitiesDataSchema()),
                                 degraded=["Quote", "Historical prices"]))
    block = (await cot.fetch_ownership("CRWV", user_tier="pro"))["congress"]
    assert "complete" not in block
    assert block["last_12_months"] == "0 member(s) disclosed purchases and 0 disclosed sales"
    assert block["note"] == "No congressional disclosure of this stock was found in the period."


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [None, "free", "garbage"])
async def test_a_locked_tier_with_failed_feeds_stays_locked_and_names_nobody(monkeypatch, tier):
    _serve(monkeypatch, _Holders(_resp(built_at=_iso(0), congress=_congress(2)),
                                 degraded=["House latest"]))
    out = await cot.fetch_ownership("CRWV", user_tier=tier)
    assert out["congress"]["locked"] is True and "complete" not in out["congress"]
    text = json.dumps(out)
    assert not any(name in text for name in _MEMBER_NAMES)


@pytest.mark.parametrize("degraded,missing", [
    (None, []), ("Senate latest", []), (5, []), ([None, 3, "House latest"], ["House"]),
    (("Senate disclosure",), ["Senate"]), ({"House disclosure"}, ["House"]),
    (_ALL_FEEDS, ["House", "Senate"]), (["senate latest", "Quote"], []),
])
def test_the_failed_chambers_are_read_from_any_shape(degraded, missing):
    assert cot._congress_missing(degraded) == missing
    block = cot._congress_block(_resp(built_at=_iso(0), congress=CongressActivitiesDataSchema()),
                                True, degraded)
    if missing:
        assert block["complete"] is False and "No congressional disclosure" not in block["note"]
    else:
        assert "complete" not in block
        assert block["note"].startswith("No congressional disclosure")


def test_no_congress_section_is_unavailable_never_none_found():
    class _Bare:
        recent_activities = None
    block = cot._congress_block(_Bare(), True, [])
    assert block["available"] is False and "never say there are none" in block["note"]


def test_garbage_congress_counts_are_dropped_never_printed():
    resp = _resp(built_at=_iso(0), congress=CongressActivitiesDataSchema.model_construct(
        summary=CongressActivitySummarySchema.model_construct(num_buyers=float("nan"),
                                                              num_sellers=-2),
        activities=[]))
    block = cot._congress_block(resp, True, [])
    assert "last_12_months" not in block and "nan" not in json.dumps(block).lower()


# ── Freshness: the activity windows describe the insiders list's read ─────────────────

def _stale_and_fresh():
    """The cached build predates a 65,616-share sale; the rebuild has it (list, bars, card)."""
    # New York's day and month, as the 3/6-month windows end on: on the UTC month a run between
    # 20:00 ET and midnight on a month's last day put the sale in a month the windows don't hold.
    now = datetime.now(ZoneInfo("America/New_York"))
    sale_day = now.date().isoformat()
    stale = _resp(built_at=_iso(10), shares=368142)
    fresh = _resp(
        built_at=_iso(0), shares=302526,
        acts=[_act(sale_day, 65616, 87.69)],
        flow=[SmartMoneyFlowDataPointSchema(month=_month(now), sell_volume=0.065616)],
        summary=InsiderActivitySummarySchema(informative_sells_in_millions=0.065616, num_sellers=1),
        card=SmartMoneyFlowSummarySchema(total_buy_usd_millions=0.0, total_sell_usd_millions=5.7539),
        congress=_congress(2))
    return stale, fresh


@pytest.mark.asyncio
async def test_after_a_rebuild_the_windows_show_the_sale_the_insiders_list_shows(monkeypatch):
    stale, fresh = _stale_and_fresh()
    fake = _serve(monkeypatch, _Holders(stale, fresh=fresh, newer=True,
                                        fresh_degraded=["Senate latest"]))
    out = await cot.fetch_ownership("CRWV")
    assert fake.calls == [False, True]
    assert "302,526" in json.dumps(out["insiders"])
    act = out["insider_activity"]
    assert "sold 65,616 shares" in act["last_3_months"]
    assert "sold 65,616 shares" in act["last_12_months"]
    assert "note" not in act and "read_at" not in act
    # A follow-up served from the kept read keeps the same windows (no second rebuild).
    again = await cot.fetch_ownership("CRWV")
    assert fake.calls == [False, True, False]
    assert "sold 65,616 shares" in again["insider_activity"]["last_3_months"]


@pytest.mark.asyncio
async def test_the_kept_view_never_carries_congressional_rows(monkeypatch):
    stale, fresh = _stale_and_fresh()
    _serve(monkeypatch, _Holders(stale, fresh=fresh, newer=True))
    out = await cot.fetch_ownership("CRWV", user_tier=None)
    view = cot._fresh_views["CRWV"]
    assert view.ownership_detail is cot._fresh_reads["CRWV"]
    assert not hasattr(view.recent_activities, "congress_activities")
    assert view.insider_data.daily_prices == [] and view.insider_data.price_data == []
    assert not any(name in json.dumps(out) for name in _MEMBER_NAMES)


@pytest.mark.asyncio
async def test_without_the_rebuilds_activity_the_windows_say_they_predate_the_list(monkeypatch):
    stale, fresh = _stale_and_fresh()
    fresh = fresh.model_copy(update={"insider_data": fresh.insider_data.model_copy(
        update={"unavailable": True})})
    _serve(monkeypatch, _Holders(stale, fresh=fresh, newer=True))
    act = (await cot.fetch_ownership("CRWV"))["insider_activity"]
    assert act["available"] is True
    assert "earlier read of the filings" in act["note"] and "never that it did not happen" in act["note"]
    # New York time, labelled ET (owner rule, 2026-10-29) — it was the UTC clock.
    assert act["read_at"] == datetime.fromisoformat(stale.ownership_detail.built_at).astimezone(
        ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    assert act["last_3_months"].endswith("no open-market purchase or sale was reported")


@pytest.mark.asyncio
async def test_a_kept_read_whose_view_was_lost_is_flagged(monkeypatch):
    stale, fresh = _stale_and_fresh()
    fake = _serve(monkeypatch, _Holders(stale, fresh=fresh, newer=True))
    await cot.fetch_ownership("CRWV")
    cot._fresh_views.clear()                          # the view gone, the read kept
    act = (await cot.fetch_ownership("CRWV"))["insider_activity"]
    assert fake.calls == [False, True, False]
    assert "earlier read of the filings" in act["note"]


@pytest.mark.asyncio
async def test_the_same_build_never_gets_a_note(monkeypatch):
    stale, _ = _stale_and_fresh()
    _serve(monkeypatch, _Holders(stale.model_copy(update={"ownership_detail": stale.ownership_detail
                                                          .model_copy(update={"built_at": _iso(0)})})))
    act = (await cot.fetch_ownership("CRWV"))["insider_activity"]
    assert "note" not in act and "read_at" not in act


def test_forgetting_a_read_forgets_its_view_and_both_stay_bounded():
    detail = OwnershipDetailSchema(built_at=_iso(0))
    cot._remember_read("AAA", detail, cot._InsiderView(_resp(built_at=_iso(0))))
    assert "AAA" in cot._fresh_views
    cot._forget_read("AAA")
    assert "AAA" not in cot._fresh_reads and "AAA" not in cot._fresh_views
    for i in range(cot._FRESH_MEMORY + 5):
        cot._remember_read(f"T{i}", OwnershipDetailSchema(built_at=_iso(0)),
                           cot._InsiderView(_resp(built_at=_iso(0))))
    assert len(cot._fresh_reads) == len(cot._fresh_views) == cot._FRESH_MEMORY
    assert set(cot._fresh_reads) == set(cot._fresh_views)


def test_an_insider_view_of_a_malformed_build_reads_as_unavailable():
    view = cot._InsiderView(object())
    assert view.ownership_detail is None and view.insider_data is None
    assert cot._activity_available(view) is False
    assert cot._insider_activity_block(view)["available"] is False


# ── the 12-month line names the trades its dollars leave out ──────────────────────────

@pytest.mark.asyncio
async def test_the_twelve_months_count_the_unpriced_trades_in_their_window(monkeypatch):
    now = datetime.now(timezone.utc)
    day = lambda back: (now - timedelta(days=back)).date().isoformat()  # noqa: E731
    acts = [
        _act(day(10), 1000, 50.0),                                  # priced
        _act(day(200), 2000, 0.0),                                  # unpriced, in 12 months
        _act(day(300), 500, float("nan"), "Informative Buy"),       # NaN price = unpriced
        _act(day(400), 700, 0.0),                                   # older than 365 days
        _act(day(20), 900, 0.0, "Uninformative Sell"),              # not open-market
        _act(day(30), float("nan"), 0.0),                           # no share count
    ]
    summary = InsiderActivitySummarySchema(informative_buys_in_millions=0.0005,
                                           informative_sells_in_millions=0.003,
                                           num_buyers=1, num_sellers=1)
    card = SmartMoneyFlowSummarySchema(total_buy_usd_millions=0.0, total_sell_usd_millions=0.05)
    _serve(monkeypatch, _Holders(_resp(built_at=_iso(0), acts=acts, summary=summary, card=card)))
    twelve = (await cot.fetch_ownership("CRWV"))["insider_activity"]["last_12_months"]
    assert "2 trade(s) reported no price and are not in the dollar figures" in twelve, twelve


@pytest.mark.asyncio
async def test_all_priced_twelve_months_say_nothing_about_unpriced(monkeypatch):
    now = datetime.now(timezone.utc).date().isoformat()
    summary = InsiderActivitySummarySchema(informative_sells_in_millions=0.001, num_sellers=1)
    card = SmartMoneyFlowSummarySchema(total_buy_usd_millions=0.0, total_sell_usd_millions=0.05)
    _serve(monkeypatch, _Holders(_resp(built_at=_iso(0), acts=[_act(now, 1000, 50.0)],
                                       summary=summary, card=card)))
    twelve = (await cot.fetch_ownership("CRWV"))["insider_activity"]["last_12_months"]
    assert "reported no price" not in twelve and "sold 1,000 shares" in twelve


@pytest.mark.parametrize("acts,cutoff,expected", [
    ([], "2026-01-01", 0),
    ([InsiderActivitySchema.model_construct(transaction_type="Informative Sell", date=None,
                                            change_in_millions=-0.1, price_at_transaction=0.0)],
     "2026-01-01", 0),
    ([InsiderActivitySchema.model_construct(transaction_type="Informative Buy", date="2026-01-01",
                                            change_in_millions=0.1, price_at_transaction=None)],
     "2026-01-01", 1),
    ([InsiderActivitySchema.model_construct(transaction_type="Informative Buy", date="2026-01-01",
                                            change_in_millions=0.1, price_at_transaction=-3.0)],
     "2026-01-02", 0),
])
def test_unpriced_since_outliers(acts, cutoff, expected):
    assert cot._unpriced_since(acts, cutoff) == expected


@pytest.mark.asyncio
async def test_an_old_build_without_dollars_never_mentions_unpriced_trades(monkeypatch):
    now = datetime.now(timezone.utc).date().isoformat()
    summary = InsiderActivitySummarySchema(informative_sells_in_millions=0.001, num_sellers=1)
    _serve(monkeypatch, _Holders(_resp(built_at=_iso(0), acts=[_act(now, 1000, 0.0)],
                                       summary=summary)))      # card dollars None
    twelve = (await cot.fetch_ownership("CRWV"))["insider_activity"]["last_12_months"]
    assert "sold 1,000 shares" in twelve and "reported no price" not in twelve and "$" not in twelve


# ── The read's stamps are New York's clock and calendar (owner rule, 2026-10-29) ──────
#
# `read_at` is the read's New York clock labelled ET, and the 12-month line's "to <date>"
# is the SAME instant's New York date: on the UTC calendar a read at 8:30 PM ET was already
# the next day, so the block said "… 20:30 ET" beside "to <tomorrow>".

@pytest.mark.parametrize("built_at,read_at,day", [
    # EDT: 00:30 UTC on Oct 2 is 8:30 PM on Oct 1 in New York.
    ("2026-10-02T00:30:00+00:00", "2026-10-01 20:30 ET", "2026-10-01"),
    # EST: the UTC day turns at 7 PM ET.
    ("2026-01-15T00:30:00+00:00", "2026-01-14 19:30 ET", "2026-01-14"),
    ("2026-01-15T05:00:00+00:00", "2026-01-15 00:00 ET", "2026-01-15"),
    # Mid-day: both calendars agree.
    ("2026-10-02T15:00:00+00:00", "2026-10-02 11:00 ET", "2026-10-02"),
])
def test_read_at_and_the_twelve_month_day_are_the_same_new_york_instant(built_at, read_at, day):
    summary = InsiderActivitySummarySchema(informative_sells_in_millions=0.001, num_sellers=1)
    block = cot._insider_activity_block(_resp(built_at=built_at, summary=summary),
                                        note=cot._ACTIVITY_OLDER_READ)
    assert block["read_at"] == read_at
    assert block["last_12_months"].startswith(f"last 12 months (trailing 365 days to {day})")
    assert "UTC" not in json.dumps(block)


@pytest.mark.parametrize("built_at", [None, "", "garbage", "2026-13-45T10:00:00+00:00",
                                      # Corrupt but parseable: the 365-day step back from any
                                      # year-1 stamp left the datetime range and raised out of
                                      # the tool (New York's pre-1883 −4:56 offset kept most of
                                      # year 1 "in range" for its clock, so a clock check alone
                                      # missed them), and the model was shown a year-1 clock.
                                      "0001-01-01T00:00:00+00:00", "0001-01-01T05:00:00+00:00",
                                      "0001-06-01T00:00:00+00:00", "0001-12-31T23:00:00+00:00",
                                      "0001-06-01T00:00:00", "1999-12-31T23:59:59+00:00"])
def test_an_unreadable_read_time_names_no_clock_and_no_day(built_at):
    """Unstamped, unparseable or implausible → "unknown" and a span with no end date, never a
    guess and never a raise."""
    summary = InsiderActivitySummarySchema(informative_sells_in_millions=0.001, num_sellers=1)
    resp = _resp(built_at="2026-10-02T15:00:00+00:00", summary=summary)
    resp = resp.model_copy(update={"ownership_detail": resp.ownership_detail.model_copy(
        update={"built_at": built_at})})
    block = cot._insider_activity_block(resp, note=cot._ACTIVITY_OLDER_READ)
    assert block["read_at"] == "unknown"
    assert block["last_12_months"].startswith("last 12 months (trailing 365 days)")


def test_a_read_time_outside_new_yorks_range_is_none_not_a_crash():
    """The edge of the datetime range overflows once moved into New York: the helpers answer
    None / "unknown" rather than raising out of the tool."""
    edge = datetime(1, 1, 1, tzinfo=timezone.utc)
    assert cot._in_new_york(edge) is None and cot._et_stamp(edge) == "unknown"
    assert cot._in_new_york(None) is None and cot._et_stamp(None) == "unknown"
    # A naive read is UTC (as `_built_at` reads one).
    assert cot._et_stamp(datetime(2026, 10, 2, 0, 30)) == "2026-10-01 20:30 ET"


def test_the_twelve_month_window_starts_from_the_reads_own_utc_day():
    """The unpriced trades are counted over the CARD's window: 365 days back from the read's
    UTC instant, as `holders_service` cut it at build. Not from now (a days-old read would drop
    trades the card counted), and not from New York's day (the read below is still Oct 2 in New
    York, which would pull in a trade from the day the card left out)."""
    built_at = "2026-10-03T02:00:00+00:00"           # 10 PM ET on Oct 2; the card's cutoff 2025-10-03
    acts = [_act("2025-10-02", 2000, 0.0),            # the day before the card's window: out
            _act("2025-10-03", 1500, 0.0)]            # its first day: in
    summary = InsiderActivitySummarySchema(informative_sells_in_millions=0.0035, num_sellers=1)
    card = SmartMoneyFlowSummarySchema(total_buy_usd_millions=0.0, total_sell_usd_millions=0.05)
    assert hs.insider_window_cutoff(datetime.fromisoformat(built_at)) == "2025-10-03"
    block = cot._insider_activity_block(_resp(built_at=built_at, acts=acts, summary=summary, card=card))
    assert "1 trade(s) reported no price" in block["last_12_months"], block["last_12_months"]


# The 3- and 6-month windows end on the read's NEW YORK month, like the stamps beside them. On
# the UTC month a read from 20:00 ET (19:00 in EST) on a month's last day already counted the
# next, empty month and dropped the oldest one — and with it a real sale.

@pytest.mark.parametrize("built_at,three,six", [
    # EDT: 00:30 UTC on Nov 1 is 8:30 PM on Oct 31 in New York.
    ("2026-11-01T00:30:00+00:00", "08/2026-10/2026", "05/2026-10/2026"),
    # EST: 00:30 UTC on Jan 1 is 7:30 PM on Dec 31.
    ("2027-01-01T00:30:00+00:00", "10/2026-12/2026", "07/2026-12/2026"),
    # Mid-month: both calendars agree.
    ("2026-10-15T15:00:00+00:00", "08/2026-10/2026", "05/2026-10/2026"),
])
def test_the_windows_end_on_new_yorks_month(built_at, three, six):
    oldest = three.split("-")[0]          # the 3-month window's oldest New York month
    flow = [SmartMoneyFlowDataPointSchema(month=oldest, sell_volume=0.5)]
    block = cot._insider_activity_block(_resp(built_at=built_at, flow=flow))
    assert block["last_3_months"].startswith(f"last 3 months ({three})"), block["last_3_months"]
    assert block["last_6_months"].startswith(f"last 6 months ({six})"), block["last_6_months"]
    assert "sold 500,000" in block["last_3_months"], block["last_3_months"]


def test_an_unstamped_read_with_no_bars_ends_on_new_yorks_month(monkeypatch):
    """No read stamp and no chart bars: the windows end on New York's month NOW, not UTC's."""
    late = datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc)      # 20:30 ET on Oct 31

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return late.astimezone(tz) if tz is not None else late.replace(tzinfo=None)

    monkeypatch.setattr(cot, "datetime", _Clock)
    resp = _resp(built_at="2026-10-02T15:00:00+00:00")
    resp = resp.model_copy(update={"ownership_detail": resp.ownership_detail.model_copy(
        update={"built_at": None})})
    block = cot._insider_activity_block(resp)
    assert block["last_3_months"].startswith("last 3 months (08/2026-10/2026)"), block["last_3_months"]
