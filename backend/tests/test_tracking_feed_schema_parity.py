"""
Schema-parity test for the Tracking Assets feed (GET /api/v1/tracking/assets).

Pins `TrackingFeedResponse` and every nested response (TrackedAssetResponse,
AlertResponse, WhaleTradeItemResponse, AnalystRatingItemResponse,
InsiderTransactionItemResponse) against the iOS Swift Codable decoders in
frontend/ios/ios/Models/TrackingModels.swift. A renamed/dropped/re-typed key
crashes the Assets tab (Holdings list or an alert card) on decode in prod, so
this fails before the app does.

The key sets below mirror the iOS `CodingKeys`. Update BOTH sides together.
No network / Supabase — models are built inline at their worst case.

Run: cd backend && ./venv/bin/pytest tests/test_tracking_feed_schema_parity.py -x
"""

from __future__ import annotations

from app.schemas.tracking import (
    AlertResponse,
    AnalystRatingItemResponse,
    InsiderTransactionItemResponse,
    TrackedAssetResponse,
    TrackingFeedResponse,
    WhaleTradeItemResponse,
)

# ── Mirror the iOS CodingKeys (snake_case wire keys) ────────────────────

ASSET_KEYS = {
    "ticker", "company_name", "price", "change_percent", "previous_close",
    "sparkline_data", "logo_url", "sector", "country", "market_cap",
    "shares", "market_value", "asset_type",
}
ALERT_KEYS = {
    "type", "title", "description", "ticker", "company_name", "day", "month",
    "report_time", "eps_estimate", "revenue_estimate", "action",
    "total_amount", "time_window_label", "whale_trade_items",
    "analyst_rating_items", "insider_transaction_items",
}
WHALE_ITEM_KEYS = {
    "ticker", "company_name", "whale_count", "amount", "raw_amount",
    "raw_amount_low", "raw_amount_high", "is_congress", "lead_whale_id",
    "lead_whale_name", "lead_whale_avatar_name", "lead_whale_firm",
}
ANALYST_ITEM_KEYS = {
    "ticker", "firm_name", "rating_action", "new_rating", "previous_rating",
    "price_target", "previous_price_target", "day", "month",
}
INSIDER_ITEM_KEYS = {
    "ticker", "insider_name", "insider_title", "amount", "raw_amount",
    "day", "month",
}

# iOS non-optional `let` fields — these MUST be present AND non-null or the
# Swift decoder throws keyNotFound / valueNotFound and blanks the whole list.
ASSET_REQUIRED = {"ticker", "company_name", "price", "change_percent", "sparkline_data"}
ALERT_REQUIRED = {"type", "title", "description"}
WHALE_ITEM_REQUIRED = {"ticker", "company_name", "whale_count", "amount"}
ANALYST_ITEM_REQUIRED = {"ticker", "firm_name", "rating_action", "new_rating"}
INSIDER_ITEM_REQUIRED = {"ticker", "insider_name", "insider_title", "amount"}


def _worst_case_feed() -> TrackingFeedResponse:
    """Degraded row + one alert of every type, at their most-empty legal shape."""
    asset = TrackedAssetResponse(ticker="", company_name="", price=0.0, change_percent=0.0)
    alerts = [
        AlertResponse(
            type="earnings", title="Earnings Alert", description="",
            ticker="AAPL", company_name="Apple", day=None, month=None,
            report_time=None, eps_estimate=None, revenue_estimate=None,
        ),
        AlertResponse(type="market", title="Market", description=""),
        AlertResponse(
            type="whale_trade", title="Whales Bought", description="",
            action="bought", total_amount="$1K", time_window_label="this week",
            whale_trade_items=[
                WhaleTradeItemResponse(
                    ticker="AAPL", company_name="Apple", whale_count=1,
                    amount="$1K", raw_amount=1000.0,
                )
            ],
        ),
        AlertResponse(
            type="analyst_rating", title="Analyst Ratings", description="",
            time_window_label="this week",
            analyst_rating_items=[
                AnalystRatingItemResponse(
                    ticker="CRM", firm_name="Goldman", rating_action="upgrade",
                    new_rating="Buy",
                )
            ],
        ),
        AlertResponse(
            type="insider_transaction", title="Insider Bought", description="",
            action="bought", total_amount="$1M", time_window_label="this week",
            insider_transaction_items=[
                InsiderTransactionItemResponse(
                    ticker="NVDA", insider_name="CFO", insider_title="CFO",
                    amount="$1M", raw_amount=1_000_000.0,
                )
            ],
        ),
    ]
    return TrackingFeedResponse(assets=[asset], alerts=alerts)


def _assert_keys(obj: dict, expected: set, where: str):
    assert set(obj.keys()) == expected, (
        f"{where}: key drift vs iOS CodingKeys.\n"
        f"  missing (iOS expects, backend dropped): {expected - set(obj.keys())}\n"
        f"  extra   (backend added, iOS ignores):   {set(obj.keys()) - expected}"
    )


def _assert_required(obj: dict, required: set, where: str):
    for k in required:
        assert obj.get(k) is not None, f"{where}: required iOS field '{k}' is null/missing"


def test_tracking_feed_schema_parity():
    payload = _worst_case_feed().model_dump()

    assert set(payload.keys()) == {"assets", "alerts"}

    # Assets
    assert payload["assets"], "expected at least one asset"
    for asset in payload["assets"]:
        _assert_keys(asset, ASSET_KEYS, "TrackedAssetResponse")
        _assert_required(asset, ASSET_REQUIRED, "TrackedAssetResponse")
        assert isinstance(asset["price"], float)
        assert isinstance(asset["change_percent"], float)
        assert isinstance(asset["sparkline_data"], list)

    # Alerts — every alert carries the full AlertResponse key set (the `type`
    # discriminator selects which optionals iOS reads).
    seen_types = set()
    for alert in payload["alerts"]:
        _assert_keys(alert, ALERT_KEYS, "AlertResponse")
        _assert_required(alert, ALERT_REQUIRED, "AlertResponse")
        seen_types.add(alert["type"])

        for item in alert.get("whale_trade_items") or []:
            _assert_keys(item, WHALE_ITEM_KEYS, "WhaleTradeItemResponse")
            _assert_required(item, WHALE_ITEM_REQUIRED, "WhaleTradeItemResponse")
            assert isinstance(item["whale_count"], int)
        for item in alert.get("analyst_rating_items") or []:
            _assert_keys(item, ANALYST_ITEM_KEYS, "AnalystRatingItemResponse")
            _assert_required(item, ANALYST_ITEM_REQUIRED, "AnalystRatingItemResponse")
        for item in alert.get("insider_transaction_items") or []:
            _assert_keys(item, INSIDER_ITEM_KEYS, "InsiderTransactionItemResponse")
            _assert_required(item, INSIDER_ITEM_REQUIRED, "InsiderTransactionItemResponse")

    assert seen_types == {
        "earnings", "market", "whale_trade", "analyst_rating", "insider_transaction",
    }

    # Round-trip: the dumped dict must re-validate (pins required vs optional).
    TrackingFeedResponse.model_validate(payload)


def test_optional_item_lists_serialize_as_null_not_missing():
    """A non-rollup alert leaves the *_items lists as null — iOS decodes them
    as optional arrays, so the keys must still be present."""
    alert = AlertResponse(type="market", title="Market", description="x").model_dump()
    for k in ("whale_trade_items", "analyst_rating_items", "insider_transaction_items"):
        assert k in alert
        assert alert[k] is None
