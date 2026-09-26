"""Search-screen chips: `GET /api/v1/search/trending` and `POST /api/v1/search/picks`.

Its own `/search` router, not a route on `stocks`: that router ends in a `/{ticker}`
catch-all, and a new path under it would be one careless edit away from being shadowed.

🔒 Account-only (auth.md §1a): the item names are FMP-derived, and a pick needs an account
by product decision — an anonymous caller could mint picks with a rotating header. The
router-level `get_current_user_id` makes a route added here gated by default.

Neither route can break a search screen:
  • GET is impersonal (the same lists for everyone) and never errors — the service falls
    back to the curated "Popular" lists.
  • POST always answers 204, whether the pick was counted, de-duplicated, invalid or the
    write failed. The app fires it and forgets it, and a uniform answer reveals nothing
    about what the server knows. No new ErrorCode, so no iOS AppError change.
"""

import logging

from fastapi import APIRouter, Depends, Response

from app.dependencies import SearchPickRateLimit, SearchTrendingRateLimit, get_current_user_id
from app.schemas.search_trending import SearchPickRequest, SearchTrendingResponse
from app.services.search_pick_service import get_search_pick_service
from app.services.search_trending_service import get_search_trending_service

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user_id)])


@router.get("/trending", response_model=SearchTrendingResponse)
async def get_search_trending(_rate: None = SearchTrendingRateLimit) -> SearchTrendingResponse:
    """The chips every search screen shows before the user types."""
    return await get_search_trending_service().get_trending()


@router.post("/picks", status_code=204)
async def record_search_pick(
    body: SearchPickRequest,
    user_id: str = Depends(get_current_user_id),
    _rate: None = SearchPickRateLimit,
) -> Response:
    """One tap on a search RESULT row. Counted anonymously, at most once per account per
    ticker per 7 days (`search_pick_service`). The account id comes from the token only."""
    try:
        outcome = await get_search_pick_service().record_pick(user_id, body.symbol, body.type)
        logger.debug("search pick outcome: %s", outcome)
    except Exception as e:  # noqa: BLE001 — record_pick never raises; this is the backstop
        # No account id and no ticker on this line (search_pick_service's rule).
        logger.error("search pick: unexpected %s — pick dropped", type(e).__name__, exc_info=True)
    return Response(status_code=204)
