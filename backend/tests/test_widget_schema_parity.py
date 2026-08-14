"""Backend ⇄ iOS contract for the home-screen widget payload.

MANDATORY per `.claude/rules/testing.md`: iOS decodes this shape, so a drift here
is a decode failure in production. It is worse than usual for a widget — a
`DecodingError` in an extension does not show an error state, it shows the
placeholder or nothing at all, on the Home Screen, with no way for the user to
retry and no crash report they would ever think to send.

Two wire rules this pins, both from the `schemas/updates.py` header:

* **No fractional seconds.** Swift's `.iso8601` decoding strategy rejects them
  outright — one stray microsecond fails the whole response.
* **snake_case survives.** `APIClient` deliberately does not set
  `.convertFromSnakeCase`; every DTO declares explicit `CodingKeys`. A field
  renamed to camelCase here silently stops decoding on the client.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from app.schemas.widget import (
    WidgetBasketResponse,
    WidgetCauseResponse,
    WidgetMoveContextResponse,
    WidgetMoverPayload,
    WidgetMoverResponse,
)
from app.services.daily_move_attribution import CauseKind
from app.services.widget_movers_service import detect_basket, rank_movers
from app.utils.market_hours import ET

_ISO_NO_FRACTION = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Every key the Swift decoder reads, at the level it reads it.
_PAYLOAD_KEYS = {
    "mode", "as_of", "market_session", "headline_mover", "basket", "runners_up",
}
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


def _full_payload() -> WidgetMoverPayload:
    return WidgetMoverPayload(
        mode="portfolio",
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_session="regular",
        is_stale=False,
        headline_mover=WidgetMoverResponse(
            ticker="ACHR",
            company_name="Archer Aviation Inc.",
            change_percent=-5.02152,
            price=6.62,
            tier="Notable",
            z=1.1,
            reason=WidgetReasonResponse(
                kind=WidgetReasonKind.CATALYST,
                text="Archer fell after an analyst downgrade.",
                catalyst_tag="Analyst Downgrade",
                sources=[],
            ),
        ),
        basket=WidgetBasketResponse(
            direction="down", moved_count=3, total_count=5,
            factor_kind="sector", factor_label="Technology",
            average_change_percent=-4.17,
            tickers=["AMD", "AVGO", "NVDA"], text="3 of your 5 holdings fell together.",
        ),
        market_story="Mixed Signals Cloud US Market Outlook",
        universe_label="Tracked by Caydex",
    )


def test_every_documented_key_is_present_at_the_documented_level():
    d = _full_payload().model_dump(mode="json")
    assert set(d) == _PAYLOAD_KEYS
    assert set(d["headline_mover"]) == _MOVER_KEYS
    assert set(d["headline_mover"]["reason"]) == _REASON_KEYS
    assert set(d["basket"]) == _BASKET_KEYS


def test_field_names_stay_snake_case():
    blob = json.dumps(_full_payload().model_dump(mode="json"))
    for camel in ("asOf", "marketSession", "isStale", "headlineMover",
                  "changePercent", "catalystTag", "companyName",
                  "movedCount", "totalCount", "factorKind", "factorLabel",
                  "averageChangePercent", "marketStory", "universeLabel"):
        assert camel not in blob, f"{camel} would not decode — CodingKeys expect snake_case"


def test_as_of_has_no_fractional_seconds():
    assert _ISO_NO_FRACTION.match(_full_payload().model_dump(mode="json")["as_of"])


def test_reason_kind_serialises_as_a_plain_string():
    """A Swift `String`-backed enum decodes "catalyst", not {"value": ...}."""
    d = _full_payload().model_dump(mode="json")
    assert d["headline_mover"]["reason"]["kind"] == "catalyst"
    assert isinstance(d["headline_mover"]["reason"]["kind"], str)


@pytest.mark.parametrize("kind", list(WidgetReasonKind))
def test_all_reason_kinds_round_trip(kind):
    """iOS switches on this exhaustively — an unlisted value would fall through."""
    r = WidgetReasonResponse(kind=kind, text="x")
    assert WidgetReasonResponse.model_validate(r.model_dump(mode="json")).kind == kind


def test_the_minimal_payload_validates():
    """A flat day with nothing to say must still be a decodable response."""
    d = WidgetMoverPayload(
        mode="market", as_of="2026-08-14T21:50:28Z", market_session="closed"
    ).model_dump(mode="json")
    assert set(d) == _PAYLOAD_KEYS
    assert d["headline_mover"] is None
    assert d["basket"] is None
    assert d["is_stale"] is False
    WidgetMoverPayload.model_validate(d)


def test_optional_numerics_serialise_as_null_not_zero():
    """`change_percent: null` makes iOS hide the number. `0.0` would render a
    confident flat reading for a stock whose quote we could not read."""
    d = WidgetMoverResponse(
        ticker="X",
        reason=WidgetReasonResponse(kind=WidgetReasonKind.NONE, text="n/a"),
    ).model_dump(mode="json")
    assert d["change_percent"] is None
    assert d["price"] is None
    assert d["z"] is None
    assert d["tier"] is None


def test_the_worst_case_service_output_still_validates():
    """Drive the real builders with hostile inputs and validate the result.

    The parity-test pattern from `test_ticker_report_schema_parity`: worst-case
    dict in, `model_validate` out — so a change to the service that produces an
    undecodable shape fails here rather than on someone's Home Screen.
    """
    ranked = rank_movers(
        [
            {"ticker": "AAA", "change_percent": -5.0, "sigma_daily": 0.03},
            {"ticker": "BBB", "change_percent": -6.0, "sigma_daily": None},
            {"ticker": "CCC", "change_percent": float("nan"), "sigma_daily": 0.03},
            {"ticker": "DDD", "change_percent": -4.0, "sigma_daily": 0.03},
            {"ticker": None, "change_percent": 1.0, "sigma_daily": 0.03},
        ]
    )
    top = ranked[0]
    payload = WidgetMoverPayload(
        mode="portfolio",
        as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        market_session="closed",
        is_stale=True,
        headline_mover=WidgetMoverResponse(
            ticker=top.ticker,
            company_name=None,
            change_percent=top.change_percent,
            price=None,
            tier=top.tier,
            z=round(top.z, 2) if top.z is not None else None,
            reason=resolve_reason(
                {"headline": "", "price_move": "garbage", "generated_at": "nope"},
                top.change_percent,
                datetime.now(ET).date().isoformat(),
            ),
        ),
        basket=detect_basket(ranked, {"AAA": "Technology"}),
    )
    WidgetMoverPayload.model_validate(payload.model_dump(mode="json"))


def test_json_is_serialisable_with_no_nan_or_infinity():
    """`json.dumps` emits bare `NaN`/`Infinity` for floats, which is invalid JSON
    and fails `JSONSerialization` on the client. Nothing may reach the wire."""
    ranked = rank_movers(
        [{"ticker": "AAA", "change_percent": float("inf"), "sigma_daily": 0.03},
         {"ticker": "BBB", "change_percent": -6.0, "sigma_daily": 0.03}]
    )
    payload = WidgetMoverPayload(
        mode="market",
        as_of="2026-08-14T21:50:28Z",
        market_session="regular",
        headline_mover=WidgetMoverResponse(
            ticker=ranked[0].ticker,
            change_percent=ranked[0].change_percent,
            z=ranked[0].z,
            reason=WidgetReasonResponse(kind=WidgetReasonKind.NONE, text="x"),
        ),
    )
    blob = json.dumps(payload.model_dump(mode="json"), allow_nan=False)
    assert "NaN" not in blob and "Infinity" not in blob
