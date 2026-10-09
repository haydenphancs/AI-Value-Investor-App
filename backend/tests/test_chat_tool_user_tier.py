"""Congressional disclosures in Ask Cay AI's ownership tool are Pro and above — fail CLOSED.

`HoldersService.get_holders_with_status` is UNTIERED (the report and the ownership snapshot
read it too), so the gate must sit in the tool. `build_chat_tool_handlers(user_tier=None)` is
what every caller that does not pass a tier gets, and None, "free" and anything unrecognised
must see NO member's name and NO trade anywhere in the result — a locked note instead
(`entitlements.congress_holders_unlocked`, `holders_service.redact_congress`). Pro wording is
"disclosed a purchase/sale", with the range and the disclosure date.

Also pinned: a bare coin-collider ticker (LTC, BTC, ETH) reaches the ownership fetch
UNCHANGED — LTC is LTC Properties, never Litecoin's pair, on any screen.

Hermetic: fake holders service; side reads stubbed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.schemas.holders import (
    CongressActivitiesDataSchema,
    CongressActivitySchema,
    CongressActivitySummarySchema,
    HoldersResponse,
    InsiderHoldingSchema,
    InsiderHoldingsSchema,
    InsiderOwnerSchema,
    OwnershipDetailSchema,
    RecentActivitiesSchema,
    SmartMoneyDataSchema,
    SmartMoneyFlowDataPointSchema,
)
from app.services import chat_ownership_tool as cot
from app.services import holders_service as hs
from app.services.agents import chat_tools

_MEMBERS = ("Pelosi", "Nancy", "Tuberville", "Tommy")
_TRADE_MARKS = ("$1,001 - $15,000", "$50,001 - $100,000", "Representative (CA-11)", "Senator (AL)",
                "2026-07-20", "2026-08-01", "2026-06-02")


def _congress_resp() -> HoldersResponse:
    acts = [
        CongressActivitySchema(name="Pelosi, Nancy", role="Representative (CA-11)", date="2026-07-20",
                               change_in_millions=0.008, amount_range="$1,001 - $15,000",
                               amount_range_max_millions=0.015, owner="Spouse",
                               transaction_type="Purchase", disclosure_date="2026-08-01"),
        CongressActivitySchema(name="Tuberville, Tommy", role="Senator (AL)", date="2026-06-02",
                               change_in_millions=-0.075, amount_range="$50,001 - $100,000",
                               amount_range_max_millions=0.1, owner="Self",
                               transaction_type="Sale"),
    ]
    return HoldersResponse(
        symbol="CRWV",
        congress_data=SmartMoneyDataSchema(tab="Congress", flow_data=[
            SmartMoneyFlowDataPointSchema(month="07/2026", buy_volume=0.008)]),
        recent_activities=RecentActivitiesSchema(congress_activities=CongressActivitiesDataSchema(
            summary=CongressActivitySummarySchema(total_buys_in_millions=0.008, num_buyers=1,
                                                  total_sells_in_millions=0.075, num_sellers=1),
            activities=acts)),
        ownership_detail=OwnershipDetailSchema(
            insider_holdings=InsiderHoldingsSchema(insiders=[InsiderOwnerSchema(
                name="Brian M. Venturo", role="director", latest_transaction_date="2026-09-30",
                holdings=[InsiderHoldingSchema(security="Class A Common Stock", held="direct",
                                               shares=302526, as_of="2026-09-30")])]),
            institutions_quarter="Q2 2026",
            built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


class _Holders:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    async def get_holders_with_status(self, ticker, force_refresh=False):
        self.calls.append(ticker)
        return self.resp, []


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _no_short(sym):
        return {}

    async def _no_profile(sym):
        return None

    monkeypatch.setattr(cot, "_load_short_interest", _no_short)
    monkeypatch.setattr(cot, "_issuer_profile_flags", _no_profile)
    monkeypatch.setattr(cot, "_side_tasks", set())   # never another test's (or loop's) tasks
    monkeypatch.setattr(cot, "_fresh_reads", {})
    monkeypatch.setattr(cot, "_fresh_views", {})
    monkeypatch.setattr(cot, "_last_probe", {})
    fake = _Holders(_congress_resp())
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    return fake


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [None, "free", "FREE", " free ", "nonsense", "", "max", "guest",
                                  7, True, "pro_trial", "premium-ish"])
async def test_no_member_name_or_trade_reaches_a_locked_tier_anywhere(tier):
    out = await cot.fetch_ownership("CRWV", user_tier=tier)
    text = json.dumps(out)
    for mark in _MEMBERS + _TRADE_MARKS:
        assert mark not in text, (tier, mark)
    assert out["congress"]["locked"] is True
    assert "Caydex Pro" in out["congress"]["note"]
    assert "disclosures" not in out["congress"]
    # the rest of the answer is intact
    assert out["insiders"]["people"][0]["name"] == "Brian M. Venturo"


@pytest.mark.asyncio
async def test_the_default_call_is_locked():
    out = await cot.fetch_ownership("CRWV")
    assert out["congress"]["locked"] is True
    assert not any(m in json.dumps(out) for m in _MEMBERS)


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["pro", "PRO", " Pro ", "premium"])
async def test_a_pro_tier_reads_disclosures_with_ranges_and_dates(tier):
    out = await cot.fetch_ownership("CRWV", user_tier=tier)
    block = out["congress"]
    assert "locked" not in block
    rows = block["disclosures"]
    assert rows[0] == ("Pelosi, Nancy (Representative (CA-11), owner Spouse): disclosed a "
                       "purchase of $1,001 - $15,000, traded 2026-07-20, disclosed 2026-08-01")
    assert rows[1].startswith("Tuberville, Tommy (Senator (AL)): disclosed a sale of "
                              "$50,001 - $100,000, traded 2026-06-02, disclosure date not recorded")
    for row in rows:
        low = row.lower()
        assert "disclosed" in low and "bought" not in low and " sold" not in low
    assert block["last_12_months"] == "1 member(s) disclosed purchases and 1 disclosed sales"
    assert "ranges" in block["basis"]


@pytest.mark.asyncio
async def test_a_locked_tier_never_mutates_the_shared_holders_object(_hermetic):
    """`redact_congress` returns a COPY: the cached build a Pro caller reads next is intact."""
    await cot.fetch_ownership("CRWV", user_tier="free")
    assert _hermetic.resp.recent_activities.congress_activities.activities
    pro = await cot.fetch_ownership("CRWV", user_tier="pro")
    assert pro["congress"]["disclosures"]


# ── the handler: the tier travels only to a fetch that can take it ─────────────────────

class _Svc:
    def __init__(self):
        self._fetch_ownership_data = AsyncMock(return_value={"ok": True})

    @staticmethod
    def _chat_symbol(raw):
        return {"LTC": "LTCUSD", "BTC": "BTCUSD", "ETH": "ETHUSD"}.get(raw, raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", [None, "free", "nonsense"])
async def test_a_locked_tier_calls_the_fetch_without_a_tier(tier):
    svc = _Svc()
    handlers = chat_tools.build_chat_tool_handlers(svc, user_tier=tier)
    await handlers[chat_tools.OWNERSHIP_TOOL]({"ticker": "CRWV"})
    svc._fetch_ownership_data.assert_awaited_once_with("CRWV")


@pytest.mark.asyncio
async def test_the_default_handler_set_is_locked():
    svc = _Svc()
    await chat_tools.build_chat_tool_handlers(svc)[chat_tools.OWNERSHIP_TOOL]({"ticker": "CRWV"})
    svc._fetch_ownership_data.assert_awaited_once_with("CRWV")


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["pro", "premium"])
async def test_a_pro_tier_travels_with_the_call(tier):
    svc = _Svc()
    handlers = chat_tools.build_chat_tool_handlers(svc, user_tier=tier)
    await handlers[chat_tools.OWNERSHIP_TOOL]({"ticker": "CRWV"})
    svc._fetch_ownership_data.assert_awaited_once_with("CRWV", user_tier=tier)


@pytest.mark.asyncio
async def test_a_fetch_that_cannot_take_the_tier_serves_the_locked_default(caplog):
    calls = []

    class _OldSvc:
        async def _fetch_ownership_data(self, ticker):
            calls.append(ticker)
            return {"ok": True}

    handlers = chat_tools.build_chat_tool_handlers(_OldSvc(), user_tier="pro")
    with caplog.at_level(logging.WARNING):
        assert await handlers[chat_tools.OWNERSHIP_TOOL]({"ticker": "CRWV"}) == {"ok": True}
    assert calls == ["CRWV"]
    assert "serving the locked default" in caplog.text


@pytest.mark.asyncio
async def test_the_tier_reaches_the_real_tool_end_to_end():
    class _RealSvc:
        async def _fetch_ownership_data(self, ticker, user_tier=None):
            return await cot.fetch_ownership(ticker, user_tier=user_tier)

    free = await chat_tools.build_chat_tool_handlers(_RealSvc(), user_tier="free")[
        chat_tools.OWNERSHIP_TOOL]({"ticker": "CRWV"})
    assert free["congress"]["locked"] is True and "Pelosi" not in json.dumps(free)
    pro = await chat_tools.build_chat_tool_handlers(_RealSvc(), user_tier="pro")[
        chat_tools.OWNERSHIP_TOOL]({"ticker": "CRWV"})
    assert pro["congress"]["disclosures"][0].startswith("Pelosi, Nancy")


# ── collider tickers reach the fetch unchanged ─────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("screen", [None, "AAPL", "LTCUSD"])
@pytest.mark.parametrize("symbol", ["LTC", "BTC", "ETH", "ltc"])
async def test_a_coin_collider_ticker_reaches_the_ownership_fetch_unchanged(screen, symbol):
    svc = _Svc()
    asset = "CRYPTO" if screen == "LTCUSD" else ("STOCK" if screen else None)
    handlers = chat_tools.build_chat_tool_handlers(svc, screen_symbol=screen, screen_asset_type=asset)
    await handlers[chat_tools.OWNERSHIP_TOOL]({"ticker": symbol})
    svc._fetch_ownership_data.assert_awaited_once_with(symbol.upper())


@pytest.mark.asyncio
async def test_the_tool_says_a_collider_is_the_listed_security(_hermetic):
    out = await cot.fetch_ownership("LTC")
    assert _hermetic.calls == ["LTC"]
    assert out["resolved_as"] == "LTC: the US-listed security with this ticker — not the cryptocurrency of the same symbol"
    plain = await cot.fetch_ownership("CRWV")
    assert plain["resolved_as"] == "CRWV: the US-listed security with this ticker"
