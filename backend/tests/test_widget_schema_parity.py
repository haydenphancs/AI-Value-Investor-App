"""Backend ⇄ iOS contract for the home-screen widget payload.

MANDATORY per `.claude/rules/testing.md`: iOS decodes this shape, so a drift here is a
decode failure in production. It is worse than usual for a widget — a `DecodingError`
in an extension does not show an error state, it shows the placeholder or nothing at
all, on the Home Screen, with no way for the user to retry and no crash report they
would ever think to send.

Two wire rules this pins, both from the `schemas/updates.py` header:

* **No fractional seconds.** Swift's `.iso8601` strategy rejects them outright — one
  stray microsecond fails the whole response.
* **snake_case survives.** `APIClient` deliberately does not set
  `.convertFromSnakeCase`; every DTO declares explicit `CodingKeys`. A field renamed
  to camelCase here silently stops decoding on the client.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone

import pytest

from app.schemas.widget import (
    WidgetBasketResponse,
    WidgetCauseResponse,
    WidgetIndexResponse,
    WidgetMarketContextResponse,
    WidgetMoveContextResponse,
    WidgetMoverPayload,
    WidgetMarketBriefResponse,
    WidgetMoverResponse,
)
from app.services.daily_move_attribution import CauseKind, attribute
from app.services.widget_movers_service import detect_basket, rank_movers

_ISO_NO_FRACTION = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Every key the Swift decoder reads, at the level it reads it.
_PAYLOAD_KEYS = {
    "mode", "as_of", "market_session", "headline_mover", "basket", "runners_up",
    # Session LABELLING. `as_of` is when the payload was built; `session_date` is which
    # trading session the numbers describe. Keeping them separate is what lets the tile
    # re-derive "Fri close" on a Sunday instead of presenting Friday's move as today's.
    "session_date", "session_label",
    # Which universe the movers came from — an empty active group falls back to market
    # data, and nothing used to tell the user their "My Holdings" tile showed the market.
    "scope_label",
    # How the market itself did — the band the tile leads with.
    "market_context",
    # The one-sentence read on the whole market, for the Market tile. Absent whenever
    # the __MARKET__ roll-up is not dated to this session — see _MARKET_BRIEF_KEYS.
    "market_brief",
}
_MARKET_BRIEF_KEYS = {"headline", "sentiment", "generated_at"}
_MARKET_CONTEXT_KEYS = {
    "indices", "breadth_up", "breadth_total",
    "leading_sector", "leading_sector_change_percent",
    "lagging_sector", "lagging_sector_change_percent",
    "text",
}
_INDEX_KEYS = {"symbol", "label", "change_percent", "price"}
_MOVER_KEYS = {
    "ticker", "company_name", "change_percent", "price", "tier", "z",
    "cause", "context",
}
_CAUSE_KEYS = {"kind", "tag", "detail"}
_CONTEXT_KEYS = {
    "change_percent", "z", "gap_percent", "intraday_percent", "gap_dominant",
    "industry_name", "industry_change_percent", "market_change_percent",
}
_BASKET_KEYS = {
    "direction", "moved_count", "total_count", "factor_kind", "factor_label",
    "average_change_percent", "tickers", "text",
}


def _mover(ticker: str = "ACHR") -> WidgetMoverResponse:
    return WidgetMoverResponse(
        ticker=ticker,
        company_name="Archer Aviation Inc.",
        change_percent=-5.02152,
        price=6.62,
        tier="Notable",
        z=1.1,
        cause=WidgetCauseResponse(
            kind="none",
            tag=None,
            detail="Aerospace & Defense fell 1.2%; ACHR moved far more.",
        ),
        context=WidgetMoveContextResponse(
            change_percent=-5.02152,
            z=1.1,
            gap_percent=-1.0,
            intraday_percent=-4.02,
            gap_dominant=False,
            industry_name="Aerospace & Defense",
            industry_change_percent=-1.23,
            market_change_percent=-0.15,
        ),
    )


def _full_payload() -> WidgetMoverPayload:
    return WidgetMoverPayload(
        mode="portfolio",
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_session="regular",
        headline_mover=_mover(),
        basket=WidgetBasketResponse(
            direction="down", moved_count=3, total_count=5,
            factor_kind="sector", factor_label="Technology",
            average_change_percent=-4.17,
            tickers=["AMD", "AVGO", "NVDA"],
            text="3 of your 5 holdings fell together.",
        ),
        runners_up=[_mover("JOBY")],
        market_brief=WidgetMarketBriefResponse(
            headline="AI, Fed Speech Drive Market Cautious Tone",
            sentiment="Neutral",
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        market_context=WidgetMarketContextResponse(
            indices=[
                WidgetIndexResponse(symbol="^GSPC", label="S&P 500",
                                    change_percent=-0.62, price=6412.1),
            ],
            breadth_up=3, breadth_total=11,
            leading_sector="Energy", leading_sector_change_percent=0.81,
            lagging_sector="Technology", lagging_sector_change_percent=-1.44,
            text="S&P 500 fell 0.6% · 3 of 11 sectors up · Energy leads 0.8%",
        ),
    )


def test_every_documented_key_is_present_at_the_documented_level():
    d = _full_payload().model_dump(mode="json")
    assert set(d) == _PAYLOAD_KEYS
    assert set(d["market_brief"]) == _MARKET_BRIEF_KEYS
    assert set(d["headline_mover"]) == _MOVER_KEYS
    assert set(d["headline_mover"]["cause"]) == _CAUSE_KEYS
    assert set(d["headline_mover"]["context"]) == _CONTEXT_KEYS
    assert set(d["basket"]) == _BASKET_KEYS
    assert set(d["runners_up"][0]) == _MOVER_KEYS
    assert set(d["market_context"]) == _MARKET_CONTEXT_KEYS
    assert set(d["market_context"]["indices"][0]) == _INDEX_KEYS


def test_field_names_stay_snake_case():
    blob = json.dumps(_full_payload().model_dump(mode="json"))
    for camel in ("asOf", "marketSession", "headlineMover", "runnersUp",
                  "changePercent", "companyName", "gapPercent", "intradayPercent",
                  "gapDominant", "industryName", "industryChangePercent",
                  "marketChangePercent", "movedCount", "totalCount",
                  "factorKind", "factorLabel", "averageChangePercent"):
        assert camel not in blob, f"{camel} would not decode — CodingKeys expect snake_case"


def test_the_removed_fields_are_really_gone():
    """`market_story`, `universe_label` and `is_stale` were dropped in the daily-move
    rebuild — a news headline is not a move, the label was rendered nowhere, and
    "the market is closed" is not the same as "this data is stale"."""
    blob = json.dumps(_full_payload().model_dump(mode="json"))
    for dead in ("market_story", "universe_label", "is_stale", "reason", "catalyst_tag"):
        assert dead not in blob, f"{dead} should have been removed from the payload"


def test_as_of_has_no_fractional_seconds():
    assert _ISO_NO_FRACTION.match(_full_payload().model_dump(mode="json")["as_of"])


def test_cause_kind_serialises_as_a_plain_string():
    """A Swift `String`-backed enum decodes "none", not {"value": …}."""
    d = _full_payload().model_dump(mode="json")
    assert d["headline_mover"]["cause"]["kind"] == "none"
    assert isinstance(d["headline_mover"]["cause"]["kind"], str)


@pytest.mark.parametrize("kind", [k.value for k in CauseKind])
def test_every_cause_kind_the_backend_can_emit_round_trips(kind):
    """iOS switches on this — a value it has never seen must still decode."""
    c = WidgetCauseResponse(kind=kind, detail="x")
    assert WidgetCauseResponse.model_validate(c.model_dump(mode="json")).kind == kind


def test_the_minimal_payload_validates():
    """A day with nothing readable must still be a decodable response."""
    d = WidgetMoverPayload(
        mode="market", as_of="2026-08-14T21:50:28Z", market_session="closed"
    ).model_dump(mode="json")
    assert set(d) == _PAYLOAD_KEYS
    assert d["headline_mover"] is None
    assert d["basket"] is None
    assert d["runners_up"] == []
    WidgetMoverPayload.model_validate(d)


def test_optional_numerics_serialise_as_null_not_zero():
    """`change_percent: null` makes iOS hide the number. `0.0` would render a
    confident flat reading for a stock whose quote we could not read."""
    d = WidgetMoverResponse(
        ticker="X",
        cause=WidgetCauseResponse(kind="none", detail="n/a"),
        context=WidgetMoveContextResponse(change_percent=0.0),
    ).model_dump(mode="json")
    assert d["change_percent"] is None
    assert d["price"] is None
    assert d["z"] is None
    assert d["tier"] is None
    assert d["context"]["gap_percent"] is None


def test_the_worst_case_service_output_still_validates():
    """Drive the real builders with hostile inputs and validate the result.

    The pattern from `test_ticker_report_schema_parity`: worst-case in,
    `model_validate` out — so a service change producing an undecodable shape fails
    here rather than on someone's Home Screen.
    """
    ranked = rank_movers(
        [
            {"ticker": "AAA", "change_percent": -5.0, "sigma_daily": 0.03,
             "open": None, "previous_close": 0.0},
            {"ticker": "BBB", "change_percent": -6.0, "sigma_daily": None},
            {"ticker": "CCC", "change_percent": float("nan"), "sigma_daily": 0.03},
            {"ticker": "DDD", "change_percent": -4.0, "sigma_daily": 0.03},
            {"ticker": None, "change_percent": 1.0, "sigma_daily": 0.03},
        ]
    )
    top = ranked[0]
    a = attribute(
        ticker=top.ticker, change_percent=top.change_percent, today=date(2026, 8, 14),
        z=top.z, open_price=top.open_price, previous_close=top.previous_close,
        industry_change_percent=float("nan"), market_change_percent=None,
        grade_rows=[{"date": None}], classified_news=[("", "")],
    )
    payload = WidgetMoverPayload(
        mode="portfolio",
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_session="closed",
        headline_mover=WidgetMoverResponse(
            ticker=top.ticker,
            change_percent=top.change_percent,
            tier=top.tier,
            z=round(top.z, 2) if top.z is not None else None,
            cause=WidgetCauseResponse(kind=a.kind.value, tag=a.tag, detail=a.detail),
            context=WidgetMoveContextResponse(change_percent=top.change_percent or 0.0),
        ),
        basket=detect_basket(ranked, {"AAA": "Technology"}),
    )
    WidgetMoverPayload.model_validate(payload.model_dump(mode="json"))


def test_json_is_serialisable_with_no_nan_or_infinity():
    """`json.dumps` emits bare `NaN`/`Infinity` for floats, which is invalid JSON and
    fails `JSONSerialization` on the client. Nothing may reach the wire."""
    ranked = rank_movers(
        [{"ticker": "AAA", "change_percent": float("inf"), "sigma_daily": 0.03},
         {"ticker": "BBB", "change_percent": -6.0, "sigma_daily": 0.03}]
    )
    top = ranked[0]
    payload = WidgetMoverPayload(
        mode="market",
        as_of="2026-08-14T21:50:28Z",
        market_session="regular",
        headline_mover=WidgetMoverResponse(
            ticker=top.ticker,
            change_percent=top.change_percent,
            z=top.z,
            cause=WidgetCauseResponse(kind="none", detail="x"),
            context=WidgetMoveContextResponse(change_percent=top.change_percent or 0.0),
        ),
    )
    blob = json.dumps(payload.model_dump(mode="json"), allow_nan=False)
    assert "NaN" not in blob and "Infinity" not in blob
