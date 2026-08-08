"""Request/response shapes for user-set price alerts.

⚠️ Every field here is decoded by iOS. A rename or a nullability change that iOS does
not mirror is a decode CRASH in production — see `tests/test_notification_schema_parity.py`.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


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


def price_alert_from_row(row: dict) -> PriceAlertResponse:
    """Row → DTO, defensively.

    Numerics arrive from Supabase as strings when the column is `NUMERIC`, so every one
    is coerced rather than trusted — a raw string reaching a `float` field is a
    validation error that would 500 the whole list for one bad row.
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
    )
