"""
HTTP glue shared by the five `GET /{asset}/{symbol}/chart/history` routes.

Each route stays a thin wrapper on its own guarded router (account-only, market rate
limit, plus `MarketFanoutRateLimit`: a cold page can cost several sequential FMP calls)
and delegates here, so the error contract is written once:

  * a malformed request → 422 `INVALID_INPUT` (the same status FastAPI's own query
    validation answers, which the client treats as "stop asking");
  * a known upstream failure → its typed code (`upstream_error_response`);
  * anything else → `FMP_UNAVAILABLE` (`COINGECKO_UNAVAILABLE` for crypto), logged with
    the stack. Never `REPORT_GENERATION_FAILED` — `classify_exception`'s fallback for an
    unknown class, which would tell a user panning a chart that a REPORT failed. An empty
    source lands here too: it is a failure, never `has_more: false`.

Lives beside `error_response.py`, not under `endpoints/`, because it declares no routes.
"""

import logging
from typing import Any, Dict, Optional, Union

from fastapi.responses import JSONResponse

from app.api.error_response import ErrorCode, make_error_response, upstream_error_response
from app.log_redaction import redact_secrets
from app.services.price_history_service import (
    ASSET_CRYPTO,
    ChartHistoryInputError,
    get_price_history_service,
)

logger = logging.getLogger(__name__)

#: `interval` patterns for the routes (anything else is a 422 from FastAPI).
FMP_HISTORY_INTERVAL_PATTERN = "^(daily|weekly|monthly|5min|15min|30min|1hour)$"
CRYPTO_HISTORY_INTERVAL_PATTERN = "^(daily|weekly|monthly|1hour)$"
#: `before` is validated in the service (format depends on the interval); this only
#: keeps a pathological value out of the logs.
BEFORE_MAX_LENGTH = 19


async def chart_history_response(
    asset: str,
    symbol: str,
    *,
    interval: str,
    before: str,
    limit: Optional[int],
    extended_hours: bool = False,
    fmp: Any = None,
    display_symbol: Optional[str] = None,
) -> Union[Dict[str, Any], JSONResponse]:
    """One history page, or the typed error body (invariant #3)."""
    try:
        return await get_price_history_service().history_page(
            asset,
            symbol,
            interval,
            before=before,
            limit=limit,
            extended_hours=extended_hours,
            fmp=fmp,
            display_symbol=display_symbol,
        )
    except ChartHistoryInputError as e:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            status_code=422,
            message=f"chart history request rejected: {e}",
            user_message="That chart request isn't valid.",
            details={"ticker": symbol[:32], "interval": interval[:16], "step": "chart_history"},
        )
    except Exception as e:
        logger.error(
            "Chart history failed for %s %s %s before=%s: %s: %s",
            asset, symbol, interval, (before or "")[:32], type(e).__name__, e,
            exc_info=True,
        )
        resp = upstream_error_response(
            e, ticker=symbol, step="chart_history", extra_details={"interval": interval}
        )
        if resp is not None:
            return resp
        code = ErrorCode.COINGECKO_UNAVAILABLE if asset == ASSET_CRYPTO else ErrorCode.FMP_UNAVAILABLE
        return make_error_response(
            code,
            message=f"{type(e).__name__}: {redact_secrets(e)[:300]}",
            details={"ticker": symbol[:32], "interval": interval[:16], "step": "chart_history"},
        )
