"""
Live Price Manager — WebSocket fan-out proxy for FMP real-time prices.

Maintains one upstream FMP WebSocket per ticker and broadcasts price
updates to all connected iOS clients watching that ticker.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Any

import websockets
from fastapi import WebSocket

from app.config import settings
from app.integrations.fmp import get_fmp_client

logger = logging.getLogger(__name__)

FMP_WS_URL = "wss://websockets.financialmodelingprep.com"


@dataclass
class TickerRoom:
    """Represents a single ticker's live price room."""
    ticker: str
    fmp_ws: Any = None  # websockets.WebSocketClientProtocol
    clients: set = field(default_factory=set)
    reader_task: Optional[asyncio.Task] = None
    last_message: Optional[dict] = None
    previous_close: float = 0.0


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

    async def _create_room(self, ticker: str) -> TickerRoom:
        """Open an upstream FMP WebSocket and start the reader task."""
        room = TickerRoom(ticker=ticker)

        # Fetch previous close for computing change/changePercent
        try:
            fmp = get_fmp_client()
            quote = await fmp.get_stock_price_quote(ticker)
            room.previous_close = quote.get("previousClose", 0.0) or 0.0
            logger.info(
                f"Room {ticker}: previous close = {room.previous_close}"
            )
        except Exception as e:
            logger.warning(f"Room {ticker}: failed to fetch previous close: {e}")

        # Connect to FMP WebSocket
        try:
            ws_url = f"{FMP_WS_URL}?apikey={settings.FMP_API_KEY}"
            room.fmp_ws = await websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )

            # Subscribe to the ticker
            subscribe_msg = json.dumps({
                "event": "subscribe",
                "data": {"ticker": ticker}
            })
            await room.fmp_ws.send(subscribe_msg)
            logger.info(f"Room {ticker}: FMP WebSocket connected and subscribed")

        except Exception as e:
            logger.error(f"Room {ticker}: failed to connect to FMP WebSocket: {e}")
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
                # Unsubscribe before closing
                unsub_msg = json.dumps({
                    "event": "unsubscribe",
                    "data": {"ticker": ticker}
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
                        ws_url = f"{FMP_WS_URL}?apikey={settings.FMP_API_KEY}"
                        room.fmp_ws = await websockets.connect(
                            ws_url,
                            ping_interval=20,
                            ping_timeout=10,
                            close_timeout=5,
                        )
                        subscribe_msg = json.dumps({
                            "event": "subscribe",
                            "data": {"ticker": room.ticker}
                        })
                        await room.fmp_ws.send(subscribe_msg)
                        logger.info(f"Room {room.ticker}: reconnected to FMP")
                        reconnect_attempts = 0
                    except Exception as e:
                        logger.warning(
                            f"Room {room.ticker}: reconnect failed: {e}"
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
