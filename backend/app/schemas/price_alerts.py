"""Request/response shapes for user-set price alerts.

⚠️ Every field here is decoded by iOS. A rename or a nullability change that iOS does
not mirror is a decode CRASH in production — see `tests/test_notification_schema_parity.py`.
"""

from typing import List, Literal, Mapping, Optional

from pydantic import BaseModel, Field

from app.schemas.listing_status import LISTING_FIELDS, LISTING_STATUS_DELISTED


class PriceAlertResponse(BaseModel):
    id: str
    ticker: str
    asset_type: str = "stock"
    kind: str = Field(description="price_above | price_below | percent_move")
    threshold: float
    repeat_mode: str = "once"
    is_active: bool = True
    # Surfaced so the sheet can say "this fires on the next crossing" rather than
    # leaving a user to wonder why an alert set below the current price is silent.
    armed: bool = True
    last_price: Optional[float] = None
    last_triggered_at: Optional[str] = None
    trigger_count: int = 0
    note: Optional[str] = None
    created_at: Optional[str] = None
    # The "Delisted" label (`app/schemas/listing_status.py`): all four set together for a
    # CONFIRMED delisting of the rule's ticker (`listing_status_service`), else all None —
    # trading or unknown. Appended LAST; builds that do not know them ignore them.
    listing_status: Optional[Literal["delisted"]] = None
    delisted_date: Optional[str] = None       # "YYYY-MM-DD" (US Eastern calendar)
    delisted_exchange: Optional[str] = None   # "NASDAQ"; None when FMP's row names none
    last_trade_date: Optional[str] = None     # "YYYY-MM-DD", newest session with volume > 0
    # Why the server is not evaluating this rule right now (Phase 2, owner 2026-11-13). Only
    # "delisted" today: the evaluation loop skips the rule (no quote, no state write), nothing
    # is persisted or deleted, and it resumes by itself if the ticker trades again. iOS shows
    # "Paused: delisted" and disables the toggle. None = evaluated as usual.
    paused_reason: Optional[Literal["delisted"]] = None


class PriceAlertListResponse(BaseModel):
    items: List[PriceAlertResponse] = []
    # So the sheet can disable "Add" with a real number instead of guessing.
    max_per_user: int = 20
    max_per_ticker: int = 3


class CreatePriceAlertRequest(BaseModel):
    ticker: str
    kind: str
    threshold: float
    asset_type: str = "stock"
    repeat_mode: str = "once"
    note: Optional[str] = None


class UpdatePriceAlertRequest(BaseModel):
    threshold: Optional[float] = None
    is_active: Optional[bool] = None
    repeat_mode: Optional[str] = None
    note: Optional[str] = None


class DeletePriceAlertResponse(BaseModel):
    deleted: bool = False


def price_alert_from_row(
    row: dict, listing: Optional[Mapping[str, Optional[str]]] = None,
) -> PriceAlertResponse:
    """Row → DTO, defensively.

    Numerics arrive from Supabase as strings when the column is `NUMERIC`, so every one
    is coerced rather than trusted — a raw string reaching a `float` field is a
    validation error that would 500 the whole list for one bad row.

    `listing` is `DelistedListing.wire()` for a CONFIRMED delisting of the row's ticker (the
    caller looks it up; this module stays free of service imports). Anything else — None, a
    dict whose status is not "delisted" — leaves all five listing fields None.
    """
    def _f(value) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return PriceAlertResponse(
        id=str(row.get("id") or ""),
        ticker=str(row.get("ticker") or "").upper(),
        asset_type=str(row.get("asset_type") or "stock"),
        kind=str(row.get("kind") or ""),
        threshold=_f(row.get("threshold")) or 0.0,
        repeat_mode=str(row.get("repeat_mode") or "once"),
        is_active=bool(row.get("is_active", True)),
        armed=bool(row.get("armed", True)),
        last_price=_f(row.get("last_price")),
        last_triggered_at=(str(row["last_triggered_at"]) if row.get("last_triggered_at") else None),
        trigger_count=int(row.get("trigger_count") or 0),
        note=row.get("note") or None,
        created_at=(str(row["created_at"]) if row.get("created_at") else None),
        **_listing_fields(listing),
    )


def _listing_fields(listing: Optional[Mapping[str, Optional[str]]]) -> dict:
    """The four wire fields + `paused_reason` for a confirmed delisting, else {} (all None)."""
    if not isinstance(listing, Mapping) or listing.get("listing_status") != LISTING_STATUS_DELISTED:
        return {}
    out = {}
    for key in LISTING_FIELDS:
        value = listing.get(key)
        out[key] = value if isinstance(value, str) and value else None
    out["paused_reason"] = "delisted"
    return out
