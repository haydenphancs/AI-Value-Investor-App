"""
Live Price WebSocket Endpoint
Streams real-time stock prices from FMP to iOS clients.
"""

import asyncio
import logging
import re
from collections import defaultdict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.core.security import trusted_client_ip
# `_user_id_from_token` applies the access-token type guard that `decode_token` alone does not.
# The endpoint layer importing from `dependencies` (not `integrations`) is consistent with how
# every other endpoint reaches its auth helpers.
from app.dependencies import _user_id_from_token
from app.services.live_price_manager import get_live_price_manager
from app.utils.market_hours import is_market_active

logger = logging.getLogger(__name__)

router = APIRouter()

# WebSocket connection limits
_MAX_CONNECTIONS_PER_KEY = 10
_active_connections: dict[str, int] = defaultdict(int)
_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")

def _release_connection(conn_key: str) -> None:
    """Decrement, and DELETE the key at zero.

    Storing a 0 instead of removing the entry made `_active_connections` grow by one entry
    for every distinct user id or client IP that ever touched the live-price socket, and
    never shrink — an unbounded dict keyed on caller-supplied-ish values, for the life of the
    process. Nothing ever removed a key, so the only bound was a restart.
    """
    remaining = max(0, _active_connections.get(conn_key, 0) - 1)
    if remaining:
        _active_connections[conn_key] = remaining
    else:
        _active_connections.pop(conn_key, None)



def _validate_ws_token(token: str) -> str | None:
    """
    Validate a JWT token for WebSocket auth.
    Returns user_id on success, None on failure.

    Reuses the same token validation logic as the REST endpoints — `_user_id_from_token` from
    `app.dependencies`, which tries the app-minted JWT then the Supabase-issued one.

    That shared helper is the point. This function used to call `decode_token` DIRECTLY, which
    accepts any correctly-signed token regardless of its `type` claim — so a **refresh token
    worked as a WebSocket credential**, bypassing `_decode_access_token`'s guard entirely. That
    guard exists because refresh tokens live 7 days against an access token's 24 hours and skip
    the `password_changed_at` eviction check, so honouring one here handed a stolen or
    post-password-change refresh token a live price stream for the rest of the week.

    Returning None on failure is deliberate and unchanged: the caller treats an unusable token
    as "guest", because the stream is genuinely public for crypto. There is no 401 to raise on a
    socket that has not been accepted yet.
    """
    return _user_id_from_token(token)


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
    # Anonymous cap keys off the address OUR edge observed, not the leftmost X-Forwarded-For
    # entry uvicorn puts in `websocket.client` under --forwarded-allow-ips='*' (caller-supplied,
    # so rotating it gave every connection a fresh bucket and voided this cap entirely).
    conn_key = user_id or trusted_client_ip(websocket)
    # `.get`, NOT `[...]`: this is a defaultdict, so subscripting INSERTS the key — a
    # connection REJECTED by the cap below would still leave a permanent entry behind.
    if _active_connections.get(conn_key, 0) >= _MAX_CONNECTIONS_PER_KEY:
        await websocket.close(code=1008, reason="Too many connections")
        return

    # Accept the connection
    await websocket.accept()
    _active_connections[conn_key] += 1
    is_crypto = ticker_upper.endswith("USD") and len(ticker_upper) >= 5

    # Check market hours for stocks — crypto trades 24/7
    if not is_crypto and not is_market_active():
        # This early return is BEFORE the try/finally that decrements the counter, so
        # the slot must be released here on EVERY exit path. send_json/close can raise
        # (ConnectionClosed/RuntimeError) if the client dropped in the accept→send
        # race; a bare decrement AFTER the send would then be skipped, permanently
        # leaking the slot and locking the user/IP out after _MAX_CONNECTIONS_PER_KEY
        # market-closed connects (e.g. all weekend). The finally guarantees release.
        try:
            await websocket.send_json({
                "type": "market_closed",
                "message": "US markets are currently closed"
            })
            await websocket.close(code=1000, reason="Market closed")
        except Exception as e:
            logger.warning(f"Market-closed notify failed for {ticker_upper}: {type(e).__name__}: {e}")
        finally:
            _release_connection(conn_key)
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
        _release_connection(conn_key)
        await manager.unsubscribe(ticker_upper, websocket)
        logger.info(
            f"WebSocket closed for {ticker_upper} (user: {user_id})"
        )
