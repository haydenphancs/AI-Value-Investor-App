"""
Portfolios Endpoints — named groupings of tickers within a user's watchlist.

A portfolio is a user-named subset of the master watchlist_items table. Tickers
in portfolio_items.ticker must already exist as a watchlist row for the same
user (enforced at the service layer; a portfolio can never contain a ticker
the user isn't tracking).

Each portfolio_items row also carries optional per-portfolio holding values —
``shares`` and ``market_value`` — that drive the Portfolio Insights
diversification score for the active portfolio. These are independent across
portfolios: GOOGL with 10 shares set in "Holdings" doesn't leak into a
separate "Tech" portfolio.

The first call to GET /portfolios for a user with no rows lazily seeds a
default "Holdings" portfolio populated from their existing watchlist (carrying
over each row's shares / market_value), so the iOS client never has to
special-case the empty state.

Routes:
  GET    /portfolios                         → PortfolioListResponse
  POST   /portfolios                         → PortfolioResponse
  PUT    /portfolios/reorder                 → message
  PUT    /portfolios/{portfolio_id}          → PortfolioResponse
  DELETE /portfolios/{portfolio_id}          → message
  PUT    /portfolios/{portfolio_id}/tickers  → PortfolioResponse  (membership; preserves holdings)
  PUT    /portfolios/{portfolio_id}/holdings → PortfolioResponse  (per-portfolio shares / market_value)
"""

from datetime import datetime
from typing import List, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from supabase import Client

from app.database import get_supabase
from app.dependencies import get_current_user_or_guest
from app.schemas.tracking import PortfolioInsightsResponse
from app.services.portfolio_insights_service import PortfolioInsightsService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Schemas ─────────────────────────────────────────────────────────


class PortfolioItemResponse(BaseModel):
    """A ticker inside a portfolio with optional per-portfolio holding values."""

    ticker: str
    shares: Optional[float] = None
    market_value: Optional[float] = None


class PortfolioResponse(BaseModel):
    id: str
    name: str
    sort_order: int
    items: List[PortfolioItemResponse]
    created_at: datetime
    updated_at: datetime


class PortfolioListResponse(BaseModel):
    portfolios: List[PortfolioResponse]


class CreatePortfolioRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class RenamePortfolioRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class SetTickersRequest(BaseModel):
    tickers: List[str]


class ReorderPortfoliosRequest(BaseModel):
    portfolio_ids: List[str]


class HoldingItem(BaseModel):
    """One row of the per-portfolio holdings bulk-update payload.

    Setting both ``shares`` and ``market_value`` to ``null`` clears the
    holding values for that ticker — the row stays in the portfolio but
    stops counting toward the diversification score.
    """

    ticker: str
    shares: Optional[float] = None
    market_value: Optional[float] = None


class SetPortfolioHoldingsRequest(BaseModel):
    items: List[HoldingItem]


# ── Helpers ─────────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    return name.strip()


def _row_to_portfolio(row: dict, items: List[PortfolioItemResponse]) -> PortfolioResponse:
    return PortfolioResponse(
        id=str(row["id"]),
        name=row["name"],
        sort_order=int(row.get("sort_order") or 0),
        items=items,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _fetch_portfolio_items(
    supabase: Client, portfolio_id: str
) -> List[PortfolioItemResponse]:
    rows = (
        supabase.table("portfolio_items")
        .select("ticker,shares,market_value")
        .eq("portfolio_id", portfolio_id)
        .order("position")
        .execute()
        .data
        or []
    )
    return [
        PortfolioItemResponse(
            ticker=r["ticker"],
            shares=r.get("shares"),
            market_value=r.get("market_value"),
        )
        for r in rows
    ]


def _fetch_user_portfolios(supabase: Client, user_id: str) -> List[PortfolioResponse]:
    """Return all of the user's portfolios with their items, ordered by sort_order."""
    rows = (
        supabase.table("portfolios")
        .select("*")
        .eq("user_id", user_id)
        .order("sort_order")
        .execute()
        .data
        or []
    )
    if not rows:
        return []

    portfolio_ids = [r["id"] for r in rows]
    item_rows = (
        supabase.table("portfolio_items")
        .select("portfolio_id,ticker,position,shares,market_value")
        .in_("portfolio_id", portfolio_ids)
        .order("position")
        .execute()
        .data
        or []
    )

    by_portfolio: dict[str, List[PortfolioItemResponse]] = {
        pid: [] for pid in portfolio_ids
    }
    for item in item_rows:
        by_portfolio[item["portfolio_id"]].append(
            PortfolioItemResponse(
                ticker=item["ticker"],
                shares=item.get("shares"),
                market_value=item.get("market_value"),
            )
        )

    return [_row_to_portfolio(r, by_portfolio.get(r["id"], [])) for r in rows]


def _seed_default_portfolio(supabase: Client, user_id: str) -> None:
    """Create a "Holdings" portfolio populated from the user's watchlist.

    Carries over each watchlist row's ``shares`` / ``market_value`` so a user
    who already filled in Insights values doesn't lose them on the first call
    after migration 038.
    """
    seed_rows = (
        supabase.table("watchlist_items")
        .select("ticker,added_at,shares,market_value")
        .eq("user_id", user_id)
        .order("added_at", desc=True)
        .execute()
        .data
        or []
    )

    portfolio_row = (
        supabase.table("portfolios")
        .insert({"user_id": user_id, "name": "Holdings", "sort_order": 0})
        .execute()
        .data[0]
    )

    if seed_rows:
        item_rows = [
            {
                "portfolio_id": portfolio_row["id"],
                "ticker": (r["ticker"] or "").upper(),
                "position": i,
                "shares": r.get("shares"),
                "market_value": r.get("market_value"),
            }
            for i, r in enumerate(seed_rows)
            if r.get("ticker")
        ]
        if item_rows:
            supabase.table("portfolio_items").insert(item_rows).execute()


def _get_portfolio_or_404(supabase: Client, user_id: str, portfolio_id: str) -> dict:
    """Fetch a portfolio row scoped to user_id; raise 404 if missing."""
    result = (
        supabase.table("portfolios")
        .select("*")
        .eq("user_id", user_id)
        .eq("id", portfolio_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return result.data[0]


def _name_taken(
    supabase: Client, user_id: str, name: str, exclude_id: Optional[str] = None
) -> bool:
    """Case-insensitive name conflict check within the user's portfolios."""
    rows = (
        supabase.table("portfolios")
        .select("id,name")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    target = name.casefold()
    for row in rows:
        if row.get("name", "").casefold() == target and row["id"] != exclude_id:
            return True
    return False


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=PortfolioListResponse)
async def list_portfolios(
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """List the user's portfolios (with items + per-portfolio holdings).

    Lazy-seeds a default "Holdings" portfolio on first call so the iOS client
    never has to special-case the empty state.
    """
    portfolios = _fetch_user_portfolios(supabase, user["id"])
    if not portfolios:
        _seed_default_portfolio(supabase, user["id"])
        portfolios = _fetch_user_portfolios(supabase, user["id"])
    return PortfolioListResponse(portfolios=portfolios)


@router.post("", response_model=PortfolioResponse)
async def create_portfolio(
    request: CreatePortfolioRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    name = _normalize_name(request.name)
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    if _name_taken(supabase, user["id"], name):
        raise HTTPException(
            status_code=409, detail=f'A portfolio named "{name}" already exists.'
        )

    # New portfolio appends at the end of the user's existing list.
    existing = (
        supabase.table("portfolios")
        .select("sort_order")
        .eq("user_id", user["id"])
        .order("sort_order", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    next_order = (existing[0]["sort_order"] + 1) if existing else 0

    row = (
        supabase.table("portfolios")
        .insert({"user_id": user["id"], "name": name, "sort_order": next_order})
        .execute()
        .data[0]
    )
    return _row_to_portfolio(row, [])


@router.put("/reorder")
async def reorder_portfolios(
    request: ReorderPortfoliosRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Bulk-update sort_order from the order of the supplied portfolio_ids."""
    rows = (
        supabase.table("portfolios")
        .select("id")
        .eq("user_id", user["id"])
        .execute()
        .data
        or []
    )
    owned_ids = {r["id"] for r in rows}
    for pid in request.portfolio_ids:
        if pid not in owned_ids:
            raise HTTPException(
                status_code=404, detail=f"Portfolio {pid} not found"
            )

    now = datetime.utcnow().isoformat()
    for index, pid in enumerate(request.portfolio_ids):
        supabase.table("portfolios").update(
            {"sort_order": index, "updated_at": now}
        ).eq("user_id", user["id"]).eq("id", pid).execute()

    return {"message": "Reordered", "count": len(request.portfolio_ids)}


@router.put("/{portfolio_id}", response_model=PortfolioResponse)
async def rename_portfolio(
    portfolio_id: str,
    request: RenamePortfolioRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    name = _normalize_name(request.name)
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    _get_portfolio_or_404(supabase, user["id"], portfolio_id)
    if _name_taken(supabase, user["id"], name, exclude_id=portfolio_id):
        raise HTTPException(
            status_code=409, detail=f'A portfolio named "{name}" already exists.'
        )

    row = (
        supabase.table("portfolios")
        .update({"name": name, "updated_at": datetime.utcnow().isoformat()})
        .eq("user_id", user["id"])
        .eq("id", portfolio_id)
        .execute()
        .data[0]
    )

    items = _fetch_portfolio_items(supabase, portfolio_id)
    return _row_to_portfolio(row, items)


@router.delete("/{portfolio_id}")
async def delete_portfolio(
    portfolio_id: str,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    _get_portfolio_or_404(supabase, user["id"], portfolio_id)

    # Don't let the user delete their last portfolio — leaves them with no
    # active context. The iOS UI hides the destructive button in that state,
    # but we backstop it here too.
    other_count = (
        supabase.table("portfolios")
        .select("id", count="exact")
        .eq("user_id", user["id"])
        .neq("id", portfolio_id)
        .execute()
        .count
        or 0
    )
    if other_count == 0:
        raise HTTPException(
            status_code=409, detail="Cannot delete your only portfolio."
        )

    supabase.table("portfolios").delete().eq("user_id", user["id"]).eq(
        "id", portfolio_id
    ).execute()
    return {"message": "Portfolio deleted"}


@router.put("/{portfolio_id}/tickers", response_model=PortfolioResponse)
async def set_portfolio_tickers(
    portfolio_id: str,
    request: SetTickersRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Replace the portfolio's ticker membership.

    Tickers must already exist on the user's watchlist; unknown ones are
    silently dropped (the iOS Add Asset flow always pushes the ticker to the
    master watchlist before calling this endpoint, so this is a defensive
    skip rather than a normal path).

    Per-portfolio holding values (``shares`` / ``market_value``) are
    PRESERVED for tickers that remain in the portfolio after the swap; new
    tickers come in with no holdings; removed tickers lose theirs.
    """
    _get_portfolio_or_404(supabase, user["id"], portfolio_id)

    # Dedupe + uppercase while preserving order.
    seen: set[str] = set()
    requested: List[str] = []
    for raw in request.tickers:
        symbol = (raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        requested.append(symbol)

    # Restrict to tickers already on the master watchlist.
    if requested:
        watchlist = (
            supabase.table("watchlist_items")
            .select("ticker")
            .eq("user_id", user["id"])
            .in_("ticker", requested)
            .execute()
            .data
            or []
        )
        valid = {row["ticker"].upper() for row in watchlist if row.get("ticker")}
        accepted = [t for t in requested if t in valid]
    else:
        accepted = []

    # Capture existing holdings so kept tickers don't lose shares /
    # market_value when we delete + reinsert below.
    existing_items = (
        supabase.table("portfolio_items")
        .select("ticker,shares,market_value")
        .eq("portfolio_id", portfolio_id)
        .execute()
        .data
        or []
    )
    existing_holdings = {
        (item["ticker"] or "").upper(): {
            "shares": item.get("shares"),
            "market_value": item.get("market_value"),
        }
        for item in existing_items
    }

    supabase.table("portfolio_items").delete().eq(
        "portfolio_id", portfolio_id
    ).execute()
    if accepted:
        rows = []
        for i, t in enumerate(accepted):
            prior = existing_holdings.get(t, {})
            rows.append(
                {
                    "portfolio_id": portfolio_id,
                    "ticker": t,
                    "position": i,
                    "shares": prior.get("shares"),
                    "market_value": prior.get("market_value"),
                }
            )
        supabase.table("portfolio_items").insert(rows).execute()

    supabase.table("portfolios").update(
        {"updated_at": datetime.utcnow().isoformat()}
    ).eq("id", portfolio_id).execute()

    refreshed = (
        supabase.table("portfolios")
        .select("*")
        .eq("id", portfolio_id)
        .execute()
        .data[0]
    )
    items = _fetch_portfolio_items(supabase, portfolio_id)
    return _row_to_portfolio(refreshed, items)


@router.put("/{portfolio_id}/holdings", response_model=PortfolioResponse)
async def set_portfolio_holdings(
    portfolio_id: str,
    request: SetPortfolioHoldingsRequest,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Bulk-update shares / market_value for tickers within a portfolio.

    Used by the iOS Portfolio Insights config sheet, which now scopes
    holdings per portfolio (rather than the older watchlist-global flow).
    Only updates tickers already in this portfolio (others are silently
    ignored — the sheet should never send those). Setting both fields to
    ``null`` clears that ticker's holding values: it stays in the portfolio
    but stops counting toward the diversification score.
    """
    _get_portfolio_or_404(supabase, user["id"], portfolio_id)

    errors: List[str] = []
    for item in request.items:
        ticker = item.ticker.upper()
        if item.shares is not None and item.shares < 0:
            errors.append(f"{ticker}: shares cannot be negative")
            continue
        if item.market_value is not None and item.market_value < 0:
            errors.append(f"{ticker}: market_value cannot be negative")
            continue

        supabase.table("portfolio_items").update(
            {"shares": item.shares, "market_value": item.market_value}
        ).eq("portfolio_id", portfolio_id).eq("ticker", ticker).execute()

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    supabase.table("portfolios").update(
        {"updated_at": datetime.utcnow().isoformat()}
    ).eq("id", portfolio_id).execute()

    refreshed = (
        supabase.table("portfolios")
        .select("*")
        .eq("id", portfolio_id)
        .execute()
        .data[0]
    )
    items = _fetch_portfolio_items(supabase, portfolio_id)
    return _row_to_portfolio(refreshed, items)


@router.get(
    "/{portfolio_id}/insights",
    response_model=Optional[PortfolioInsightsResponse],
)
async def get_portfolio_insights(
    portfolio_id: str,
    user: dict = Depends(get_current_user_or_guest),
    supabase: Client = Depends(get_supabase),
):
    """Server-computed Portfolio Insights for ONE portfolio — the 0..100
    diversification health score, sub-scores, breakdown allocations, and
    nudges. Scores this portfolio's ``portfolio_items`` holdings joined with
    the metadata on the user's watchlist rows. Returns ``null`` when the
    portfolio has fewer than the minimum holdings for a meaningful score.
    """
    _get_portfolio_or_404(supabase, user["id"], portfolio_id)
    service = PortfolioInsightsService()
    return await service.compute_insights_for_portfolio(user["id"], portfolio_id)
