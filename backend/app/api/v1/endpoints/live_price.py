"""
Live Price WebSocket Endpoint
Streams real-time stock prices from FMP to iOS clients.
"""

import asyncio
import logging
import re
from collections import defaultdict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.core.security import decode_token, verify_supabase_token
from app.services.live_price_manager import get_live_price_manager
from app.utils.market_hours import is_market_active

logger = logging.getLogger(__name__)

router = APIRouter()

# WebSocket connection limits
_MAX_CONNECTIONS_PER_KEY = 10
_active_connections: dict[str, int] = defaultdict(int)
_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")


def _validate_ws_token(token: str) -> str | None:
    """
    Validate a JWT token for WebSocket auth.
    Returns user_id on success, None on failure.

    Reuses the same token validation logic as the REST endpoints
    (decode_token for custom JWT, verify_supabase_token for Supabase Auth).
    """
    # Try custom JWT first
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id:
            return user_id
    except Exception:
        pass

    # Try Supabase Auth token
    try:
        payload = verify_supabase_token(token)
        if payload:
            user_id = payload.get("sub")
            if user_id:
                return user_id
    except Exception:
        pass

    return None


@router.websocket("/ws/price/{ticker}")
async def live_price_ws(
    websocket: WebSocket,
    ticker: str,
    token: str = Query(None),
):
    """
    WebSocket endpoint for real-time price streaming.

    Connection flow:
    1. Client connects with optional JWT token as query param
    2. Server validates token (optional — allows guest access for crypto)
    3. Server checks market hours for stocks — crypto is 24/7
    4. Server subscribes client to the ticker's LivePriceManager room
    5. Price updates stream to client as JSON messages
    6. On disconnect, server unsubscribes and cleans up

    Message format sent to client:
        {"type": "price_update", "symbol": "AAPL", "price": 150.25,
         "change": 2.34, "change_percent": 1.58, "volume": 42500000,
         "timestamp": 1234567890}

    Auth:
        JWT passed as ?token=eyJ... query parameter (WebSocket doesn't
        support Authorization headers from iOS URLSessionWebSocketTask).
        Token is optional for crypto symbols (24/7 public data).
    """
    # Validate ticker format
    ticker_upper = ticker.strip().upper()
    if not _TICKER_RE.match(ticker_upper):
        await websocket.close(code=1008, reason="Invalid ticker")
        return

    # Validate JWT (optional — allow guest access)
    if token:
        user_id = _validate_ws_token(token)
    else:
        user_id = None

    # Enforce per-user/IP connection limit
    conn_key = user_id or (websocket.client.host if websocket.client else "unknown")
    if _active_connections[conn_key] >= _MAX_CONNECTIONS_PER_KEY:
        await websocket.close(code=1008, reason="Too many connections")
        return

    # Accept the connection
    await websocket.accept()
    _active_connections[conn_key] += 1
    is_crypto = ticker_upper.endswith("USD") and len(ticker_upper) >= 5

    # Check market hours for stocks — crypto trades 24/7
    if not is_crypto and not is_market_active():
        await websocket.send_json({
            "type": "market_closed",
            "message": "US markets are currently closed"
        })
        await websocket.close(code=1000, reason="Market closed")
        # This early return is BEFORE the try/finally that decrements the counter,
        # so we must release the slot here — otherwise every market-closed connect
        # (e.g. all weekend) permanently leaks a slot and locks the user/IP out
        # after _MAX_CONNECTIONS_PER_KEY attempts.
        _active_connections[conn_key] = max(0, _active_connections[conn_key] - 1)
        return

    # Subscribe to the ticker room
    manager = get_live_price_manager()
    try:
        await manager.subscribe(ticker_upper, websocket)

        # Keep connection alive — listen for client messages
        # Timeout allows periodic market-hours check so connections
        # opened near market close don't persist as zombies.
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=300
                )
                # Client can send ping or other control messages
                # No action needed — the loop keeps the connection open
            except asyncio.TimeoutError:
                # Check if market closed during this session
                if not is_crypto and not is_market_active():
                    await websocket.send_json({
                        "type": "market_closed",
                        "message": "US markets are now closed"
                    })
                    break
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for {ticker_upper}: {e}")
    finally:
        _active_connections[conn_key] = max(0, _active_connections[conn_key] - 1)
        await manager.unsubscribe(ticker_upper, websocket)
        logger.info(
            f"WebSocket closed for {ticker_upper} (user: {user_id})"
        )
