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
    now = datetime.now(timezone.utc)
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
    assert act["read_at"] == datetime.fromisoformat(stale.ownership_detail.built_at).astimezone(
        timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
