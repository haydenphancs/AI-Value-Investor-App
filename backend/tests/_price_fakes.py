"""Adapter: let an existing FMP-shaped test fake stand in for `price_service`.

WHY THIS EXISTS
---------------
FMP's package enforcement took away `quote` / `batch-quote`, so services now read prices
from `price_service` (profile + company-screener + the stored close snapshot) instead of
from `FMPClient`. Dozens of tests inject a fake upstream as `svc.fmp = _FakeFMP()`, and
every one of those fakes already encodes exactly the scenario its test is about — thin
volume, a NaN field, a missing symbol, a specific change %.

Rewriting each of those fakes would mean re-deriving all of that data by hand, with a real
chance of quietly weakening a test in the process. This adapter instead re-points the
SOURCE while leaving the DATA untouched: the fake keeps its old method names, and this
exposes them under the names `price_service` uses.

Usage — alongside the existing `svc.fmp = fake`:

    monkeypatch.setattr(module_under_test, "get_price_service",
                        lambda: PriceFromFMPFake(fake))
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Sequence


class PriceFromFMPFake:
    """Exposes a legacy FMP fake through the `price_service` interface."""

    def __init__(self, fmp: Any):
        self._fmp = fmp

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Mirrors `PriceService.get_quote`, including its `{}`-on-miss contract."""
        getter = getattr(self._fmp, "get_stock_price_quote", None)
        if getter is None:
            return {}
        return (await getter(symbol)) or {}

    async def get_quotes_list(self, symbols: Sequence[str]) -> List[Dict[str, Any]]:
        getter = getattr(self._fmp, "get_batch_quotes_bulk", None)
        if getter is not None:
            return (await getter(list(symbols))) or []
        # Some fakes only implement the single-symbol method; fan out so a test that
        # supplied one still exercises the batch path rather than seeing an empty result.
        #
        # The `symbol` stamp matters: a single-quote fake usually omits it, because the
        # caller already knows which symbol it asked for. `batch-quote` always carried one
        # per row, and every consumer keys its map off it — so without this the rows are
        # unmappable, the service decides the batch returned nothing, and it fans out AGAIN
        # per tile. That showed up as exactly double the expected call count.
        # CONCURRENTLY, because the thing being stood in for is a BATCH call — one round
        # trip for every symbol. Awaiting them in sequence makes a fake with a deliberate
        # delay take N x that delay, which silently breaks timing-sensitive tests (the
        # pulse-timeout pair asserts a shielded build finishes inside a fixed window).
        rows = await asyncio.gather(*(self.get_quote(s) for s in symbols))
        return [
            {**row, "symbol": row.get("symbol") or sym}
            for sym, row in zip(symbols, rows)
            if row
        ]

    async def get_quotes(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        rows = await self.get_quotes_list(symbols)
        return {
            (r.get("symbol") or "").upper(): r
            for r in rows
            if isinstance(r, dict) and r.get("symbol")
        }
