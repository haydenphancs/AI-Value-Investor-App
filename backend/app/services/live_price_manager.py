"""
Live Price Manager — WebSocket fan-out proxy for FMP real-time prices.

Maintains one upstream FMP WebSocket per ticker and broadcasts price
updates to all connected iOS clients watching that ticker.
"""

import asyncio
import json
import logging
import os
import ssl
import time
from dataclasses import dataclass, field
from typing import Optional, Any

import certifi
import websockets
from fastapi import WebSocket

from app.config import settings
from app.integrations.fmp import get_fmp_client

logger = logging.getLogger(__name__)

# FMP runs SEPARATE real-time sockets per asset class. Crypto pairs (e.g. BTCUSD)
# will NOT stream on the stock socket — they must use the crypto endpoint, which
# additionally requires an explicit `login` event and lowercase tickers.
FMP_WS_URL_STOCK = "wss://websockets.financialmodelingprep.com"
FMP_WS_URL_CRYPTO = "wss://crypto.financialmodelingprep.com"

# Explicit TLS trust store built from certifi. Without this, websockets falls back
# to ssl.create_default_context() which trusts the host's OpenSSL default CA path
# — that path is unreliable across environments (e.g. a stale/absent anaconda
# cert.pem locally, or a slim container image on Railway). httpx (our FMP REST
# client) never hit this because it bundles certifi; this makes the WebSocket
# handshake use the same trusted bundle. Built once at import.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# ...but certifi alone is NOT enough here. FMP's WebSocket hosts are served by
# multiple edge nodes and SOME intermittently OMIT the Sectigo intermediate CA
# certs from the TLS handshake, sending only the leaf. certifi ships trust ROOTS,
# not intermediates, so a client that hits one of those nodes cannot build a path
# and fails with `[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer
# certificate` — non-deterministically, per connection. Preloading the known
# intermediates lets us complete the chain (leaf -> Sectigo R36 -> Sectigo R46 ->
# USERTrust RSA, the latter already in certifi) no matter which node answers.
# Best-effort: a missing/corrupt bundle degrades to certifi-only (the prior
# behavior) rather than crashing import. Re-capture the bundle if FMP rotates CAs.
_WS_INTERMEDIATE_BUNDLE = os.path.join(
    os.path.dirname(__file__), "certs", "fmp_ws_intermediates.pem"
)
try:
    _SSL_CONTEXT.load_verify_locations(cafile=_WS_INTERMEDIATE_BUNDLE)
except Exception as e:
    logger.warning(
        f"Live price: could not load FMP WS intermediate CAs from "
        f"{_WS_INTERMEDIATE_BUNDLE} ({type(e).__name__}: {e}); TLS handshakes may "
        f"fail intermittently when FMP omits intermediates."
    )


def _fmp_ws_target(ticker: str) -> tuple[str, bool, str]:
    """Resolve how to connect to FMP's live socket for ``ticker``.

    Returns ``(connect_url, send_login_event, subscribe_ticker)``:

    - Crypto pairs (``…USD`` with length >= 5, mirroring the endpoint's own
      ``is_crypto`` heuristic) use the dedicated crypto socket, which authenticates
      via a ``login`` event and expects a lowercase ticker.
    - Everything else uses the stock socket, which authenticates via the ``apikey``
      URL query parameter and uses the uppercase ticker.
    """
    t = ticker.upper()
    if t.endswith("USD") and len(t) >= 5:
        return FMP_WS_URL_CRYPTO, True, ticker.lower()
    return f"{FMP_WS_URL_STOCK}?apikey={settings.FMP_API_KEY}", False, t

# Min seconds between previous-close re-fetch attempts, so a genuinely-empty quote
# (or repeated failures) can't hammer the FMP REST API on every trade tick.
_PREV_CLOSE_RETRY_SECONDS = 60.0


@dataclass
class TickerRoom:
    """Represents a single ticker's live price room."""
    ticker: str
    fmp_ws: Any = None  # websockets.WebSocketClientProtocol
    clients: set = field(default_factory=set)
    reader_task: Optional[asyncio.Task] = None
    last_message: Optional[dict] = None
    previous_close: float = 0.0
    # UTC epoch-day the previous_close was fetched for (proxy for the trading day);
    # lets the reader detect a crossed day-boundary and refresh the reference close.
    previous_close_epoch_day: Optional[int] = None
    # Monotonic time of the last previous_close fetch ATTEMPT (success or failure),
    # used to throttle retries.
    last_prev_close_attempt: float = 0.0


class LivePriceManager:
    """
    Manages per-ticker FMP WebSocket connections and fans out
    price updates to all subscribed iOS clients.
    """

    def __init__(self):
        self._rooms: dict[str, TickerRoom] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, ticker: str, client_ws: WebSocket):
        """
        Add a client to a ticker room.
        Creates the room and opens an FMP connection if this is the first subscriber.
        """
        async with self._lock:
            room = self._rooms.get(ticker)

            if room is None:
                # First subscriber — create room and open FMP upstream
                room = await self._create_room(ticker)
                self._rooms[ticker] = room

            room.clients.add(client_ws)
            logger.info(
                f"Client subscribed to {ticker} "
                f"(total: {len(room.clients)})"
            )

        # Send the last cached price immediately so the client doesn't
        # have to wait for the next FMP tick
        if room.last_message:
            try:
                await client_ws.send_json(room.last_message)
            except Exception:
                pass

    async def unsubscribe(self, ticker: str, client_ws: WebSocket):
        """
        Remove a client from a ticker room.
        Tears down the FMP connection if this was the last subscriber.
        """
        async with self._lock:
            room = self._rooms.get(ticker)
            if room is None:
                return

            room.clients.discard(client_ws)
            logger.info(
                f"Client unsubscribed from {ticker} "
                f"(remaining: {len(room.clients)})"
            )

            if not room.clients:
                # Last subscriber left — tear down FMP connection
                await self._destroy_room(room)
                del self._rooms[ticker]

    async def _fetch_previous_close(self, room: TickerRoom) -> None:
        """Fetch (or refresh) the room's previous close via the FMP REST quote.

        Records the attempt time unconditionally (for throttling) and only
        overwrites ``previous_close`` on a usable value — a transient failure or a
        quote missing ``previousClose`` leaves the prior value untouched instead of
        pinning it to 0.0, which would freeze change/change_percent at +0.00% for
        the room's lifetime.
        """
        room.last_prev_close_attempt = time.monotonic()
        try:
            fmp = get_fmp_client()
            quote = await fmp.get_stock_price_quote(room.ticker)
            pc = quote.get("previousClose", 0.0) or 0.0
            if pc and pc > 0:
                room.previous_close = float(pc)
                room.previous_close_epoch_day = int(time.time()) // 86400
                logger.info(f"Room {room.ticker}: previous close = {room.previous_close}")
            else:
                logger.warning(
                    f"Room {room.ticker}: quote had no usable previousClose "
                    f"(keeping {room.previous_close})"
                )
        except Exception as e:
            logger.warning(
                f"Room {room.ticker}: failed to fetch previous close: "
                f"{type(e).__name__}: {e}"
            )

    async def _ensure_previous_close(self, room: TickerRoom, tick_epoch: Any) -> None:
        """Lazily (re)fetch previous_close from the reader loop when it's missing or
        stale, throttled so an empty/failing quote can't hammer FMP per tick.

        Missing: never successfully fetched (still 0.0). Stale: this trade tick is on
        a later UTC day than the day the close was fetched — ticks only arrive during
        market hours, so the first tick of a new trading day trips this and refreshes
        the reference close (fixing change% measured against the wrong prior close)."""
        needs = not room.previous_close or room.previous_close <= 0
        if not needs and room.previous_close_epoch_day is not None:
            try:
                ts = float(tick_epoch) if tick_epoch else 0.0
                # FMP timestamps are usually epoch SECONDS but some feeds send
                # milliseconds; normalize so the epoch-day math is correct either way
                # (else a ms timestamp always reads as a new day → wasteful refetch).
                if ts > 1e11:
                    ts /= 1000.0
                tick_day = int(ts) // 86400 if ts else None
            except (TypeError, ValueError):
                tick_day = None
            if tick_day is not None and tick_day > room.previous_close_epoch_day:
                needs = True
        if not needs:
            return
        if time.monotonic() - room.last_prev_close_attempt < _PREV_CLOSE_RETRY_SECONDS:
            return
        await self._fetch_previous_close(room)

    async def _open_fmp_ws(self, ticker: str):
        """Open + authenticate + subscribe an FMP upstream socket for ``ticker``.

        Routes crypto vs stock to the correct endpoint (``_fmp_ws_target``), pins
        the certifi TLS trust store, and sends the login (crypto only) + subscribe
        events. Returns the connected socket or raises — the caller decides whether
        the failure is fatal (room creation) or triggers a backoff-retry (reader).
        """
        url, send_login, sub_ticker = _fmp_ws_target(ticker)
        ws = await websockets.connect(
            url,
            ssl=_SSL_CONTEXT,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        try:
            # Crypto socket authenticates via an explicit login event; the stock
            # socket authenticates via the apikey URL query param (login no-op there).
            if send_login:
                await ws.send(json.dumps({
                    "event": "login",
                    "data": {"apiKey": settings.FMP_API_KEY},
                }))
            await ws.send(json.dumps({
                "event": "subscribe",
                "data": {"ticker": sub_ticker},
            }))
        except Exception:
            # Handshake connected but auth/subscribe failed — close the half-open
            # socket before propagating so it doesn't leak past the caller's
            # `fmp_ws = None` reset.
            try:
                await ws.close()
            except Exception:
                pass
            raise
        return ws

    async def _create_room(self, ticker: str) -> TickerRoom:
        """Open an upstream FMP WebSocket and start the reader task."""
        room = TickerRoom(ticker=ticker)

        # Fetch previous close for computing change/changePercent (best-effort; the
        # reader loop lazily retries if this fails so change% never stays pinned at 0).
        await self._fetch_previous_close(room)

        # Connect to FMP WebSocket (best-effort; the reader loop retries on failure
        # so a transient handshake error never leaves the room permanently dead).
        try:
            room.fmp_ws = await self._open_fmp_ws(ticker)
            logger.info(f"Room {ticker}: FMP WebSocket connected and subscribed")
        except Exception as e:
            logger.error(
                f"Room {ticker}: failed to connect to FMP WebSocket: "
                f"{type(e).__name__}: {e}"
            )
            room.fmp_ws = None

        # Start the reader task that fans out FMP messages to clients
        room.reader_task = asyncio.create_task(
            self._fmp_reader(room),
            name=f"fmp-reader-{ticker}",
        )

        return room

    async def _destroy_room(self, room: TickerRoom):
        """Close FMP connection and cancel the reader task."""
        ticker = room.ticker

        if room.reader_task and not room.reader_task.done():
            room.reader_task.cancel()
            try:
                await room.reader_task
            except asyncio.CancelledError:
                pass

        if room.fmp_ws:
            try:
                # Unsubscribe before closing, using the same ticker casing the
                # subscribe used (lowercase for crypto, uppercase for stocks).
                _, _, sub_ticker = _fmp_ws_target(ticker)
                unsub_msg = json.dumps({
                    "event": "unsubscribe",
                    "data": {"ticker": sub_ticker}
                })
                await room.fmp_ws.send(unsub_msg)
                await room.fmp_ws.close()
            except Exception:
                pass

        logger.info(f"Room {ticker}: destroyed")

    async def _fmp_reader(self, room: TickerRoom):
        """
        Read messages from FMP WebSocket and broadcast to all clients.
        Handles reconnection on unexpected disconnects.
        """
        reconnect_attempts = 0
        max_reconnects = 3
        backoff_base = 1.0

        while True:
            try:
                if room.fmp_ws is None:
                    # Attempt reconnect
                    if reconnect_attempts >= max_reconnects:
                        logger.error(
                            f"Room {room.ticker}: max reconnect attempts reached, "
                            f"notifying clients and cleaning up"
                        )
                        await self._broadcast(room, {
                            "type": "error",
                            "message": "Live price feed unavailable"
                        })
                        # Clean up the orphaned room so it doesn't persist as a zombie
                        async with self._lock:
                            if room.ticker in self._rooms:
                                for client in list(room.clients):
                                    try:
                                        await client.close()
                                    except Exception:
                                        pass
                                room.clients.clear()
                                if room.fmp_ws:
                                    try:
                                        await room.fmp_ws.close()
                                    except Exception:
                                        pass
                                del self._rooms[room.ticker]
                                logger.info(f"Room {room.ticker}: orphaned room cleaned up")
                        return

                    delay = backoff_base * (2 ** reconnect_attempts)
                    logger.info(
                        f"Room {room.ticker}: reconnecting in {delay}s "
                        f"(attempt {reconnect_attempts + 1}/{max_reconnects})"
                    )
                    await asyncio.sleep(delay)
                    reconnect_attempts += 1

                    try:
                        room.fmp_ws = await self._open_fmp_ws(room.ticker)
                        logger.info(f"Room {room.ticker}: reconnected to FMP")
                        reconnect_attempts = 0
                    except Exception as e:
                        logger.warning(
                            f"Room {room.ticker}: reconnect failed: "
                            f"{type(e).__name__}: {e}"
                        )
                        room.fmp_ws = None
                        continue

                # Read next message from FMP
                raw = await room.fmp_ws.recv()
                data = json.loads(raw)

                # FMP sends various message types; we only care about trade ticks
                # Format: {"s":"AAPL","t":1234567890,"type":"T","lp":150.25,
                #          "ls":100,"v":42500000,"ap":150.20,"bp":150.19,...}
                if not isinstance(data, dict):
                    continue

                # Skip non-trade messages (heartbeats, subscription confirmations)
                last_price = data.get("lp")
                if last_price is None:
                    continue

                # Refresh previous_close if it was never fetched (transient failure at
                # room creation) or is stale across a trading-day boundary — otherwise
                # change/change_percent would stay pinned at +0.00% forever.
                await self._ensure_previous_close(room, data.get("t"))

                # Compute change from previous close
                change = 0.0
                change_percent = 0.0
                if room.previous_close and room.previous_close > 0:
                    change = last_price - room.previous_close
                    change_percent = (change / room.previous_close) * 100

                # Transform to clean message for iOS
                message = {
                    "type": "price_update",
                    "symbol": room.ticker,
                    "price": round(last_price, 4),
                    "change": round(change, 4),
                    "change_percent": round(change_percent, 4),
                    "volume": data.get("v"),
                    "timestamp": data.get("t"),
                }

                room.last_message = message
                await self._broadcast(room, message)

            except websockets.exceptions.ConnectionClosed:
                logger.warning(
                    f"Room {room.ticker}: FMP WebSocket connection closed"
                )
                room.fmp_ws = None
                continue

            except asyncio.CancelledError:
                return

            except Exception as e:
                logger.error(
                    f"Room {room.ticker}: reader error: {e}",
                    exc_info=True,
                )
                await asyncio.sleep(1)

    async def _broadcast(self, room: TickerRoom, message: dict):
        """Send a message to all connected clients. Remove dead connections."""
        # Snapshot to avoid RuntimeError if clients set is mutated during iteration
        clients_snapshot = list(room.clients)
        dead_clients = []

        for client in clients_snapshot:
            try:
                await client.send_json(message)
            except Exception:
                dead_clients.append(client)

        for client in dead_clients:
            room.clients.discard(client)
            logger.debug(
                f"Room {room.ticker}: removed dead client "
                f"(remaining: {len(room.clients)})"
            )

    async def shutdown(self):
        """Gracefully close all rooms. Called from app lifespan shutdown."""
        async with self._lock:
            tickers = list(self._rooms.keys())
            for ticker in tickers:
                room = self._rooms[ticker]
                # Close all client connections
                for client in list(room.clients):
                    try:
                        await client.close()
                    except Exception:
                        pass
                await self._destroy_room(room)
            self._rooms.clear()
            logger.info("LivePriceManager: all rooms shut down")


# Singleton
_manager: Optional[LivePriceManager] = None


def get_live_price_manager() -> LivePriceManager:
    """Get the global LivePriceManager singleton."""
    global _manager
    if _manager is None:
        _manager = LivePriceManager()
    return _manager
