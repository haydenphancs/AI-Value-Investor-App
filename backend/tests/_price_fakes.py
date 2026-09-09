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
        """Mirrors `PriceService.get_quote`, including its `{}`-on-miss contract.

⚠️ DELIBERATELY DOES NOT apply `is_blocked_symbol`, unlike the real service.

        That makes this fake more permissive than the thing it stands in for, and the cost
        is real: every `_PULSE_SYMBOLS` entry (`^GSPC`, `BTCUSD`, `GCUSD`, ...) is blocked
        at the symbol level, so Home renders ZERO Market Pulse tiles in production while a
        test driven through here can assert six.

        It stays permissive on purpose. Index, commodity and crypto are blocked wholesale
        and their screens are Phase 4's job (ETF proxies); filtering here would turn ~34
        tests across those surfaces red for a gap this fake cannot fix. The honest fix is
        per-test: drive entitlement-sensitive assertions with LICENSED symbols and state
        the gap explicitly — see `test_quote_batching.py`'s
        `test_every_real_pulse_symbol_is_currently_unlicensed`.
        """
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


class FakeCoinGecko:
    """A CoinGecko stand-in for `price_service`'s crypto quote path.

    WHY THIS EXISTS
    ---------------
    Phase 5 routes crypto quotes to CoinGecko instead of dropping them, and
    `PriceService._crypto_quotes` degrades to `{}` on ANY exception so that a CoinGecko
    outage cannot break the equity half of a mixed batch. That is right in production and
    a trap in tests: the hermetic guard raises `NetworkCallInTests`, the degrade swallows
    it, and a test asserting `== {}` goes green **because the network was blocked** rather
    than because the code refused the symbol. That exact false pass was observed on
    `test_blocked_symbols_return_none_without_calling_fmp[BTCUSD]` before this fake existed.

    So: stub it, never let it reach the socket.

        monkeypatch.setattr(price_service_module, "get_coingecko_client",
                            lambda: FakeCoinGecko({"BTC": 78_813.0}))

    ⚠️ `price_service` imports `get_coingecko_client` INSIDE the method (a function-scoped
    import), so it resolves from the SOURCE module on every call — patch
    `app.integrations.coingecko.get_coingecko_client`, not a name on `price_service`.
    See `.claude/rules/testing.md` on picking the right binding.
    """

    def __init__(
        self,
        prices: Dict[str, float] | None = None,
        *,
        fail: bool = False,
        omit_change: bool = False,
        null_price: bool = False,
    ):
        # Keyed by BARE base symbol ("BTC"), which is what `resolve_coin_id` takes.
        self.prices = dict(prices or {})
        self.fail = fail
        # `omit_change` drops `price_change_24h`, so previousClose has no reference.
        # `null_price` returns a row whose `current_price` is None — CoinGecko does
        # this for a coin it lists but cannot currently price. Both exist so the
        # DEGRADED branches are reachable from a test; without them those guards
        # pass mutation-testing vacuously.
        self.omit_change = omit_change
        self.null_price = null_price
        self.markets_calls: List[List[str]] = []
        self.resolve_calls: List[str] = []

    async def resolve_coin_id(self, symbol: str) -> Any:
        self.resolve_calls.append(symbol)
        base = (symbol or "").strip().upper()
        # Mirror the real map's shape: an id per known coin, None for the rest.
        return f"fake-{base.lower()}" if base in self.prices else None

    async def get_markets(self, symbols: Sequence[str], *, sparkline: bool = False):
        """ONE call for the batch, exactly like the real client."""
        if self.fail:
            raise RuntimeError("coingecko down")
        self.markets_calls.append(list(symbols))
        rows = []
        for sym in symbols:
            base = (sym or "").strip().upper()
            price = self.prices.get(base)
            if price is None:
                continue
            # `id` is what the adapter keys on — never `symbol`, because MATIC and POL
            # share an id. Ordering is deliberately REVERSED against the request to keep
            # any positional zip honest.
            row = {
                "id": f"fake-{base.lower()}",
                "symbol": base.lower(),
                "name": f"{base} coin",
                "current_price": None if self.null_price else price,
                "price_change_24h": price * 0.01,
                "price_change_percentage_24h": 1.0,
                "total_volume": 1_000_000.0,
                "market_cap": price * 1_000.0,
            }
            if self.omit_change:
                row.pop("price_change_24h")
            rows.append(row)
        return list(reversed(rows))


class MoversFromFMPFake:
    """Adapts a legacy FMP-shaped test fake to the `market_movers_service` interface.

    Same reasoning as `PriceFromFMPFake`: the scanner tests each encode a specific
    scenario in their fake (a starved most-actives list, a universe past the old 50-symbol
    profile chunk, an ETF that must fail the quality gate). Re-deriving that data by hand
    against a new interface is how a test quietly stops testing what it says it does.

    So the DATA stays where it is and only the SOURCE is re-pointed: this reads the fake's
    `get_biggest_gainers` / `get_biggest_losers` / `get_most_actives` /
    `get_company_profiles_batch` and returns the `(profile_map, change_map)` pair the
    service now hands to the ranking helpers.

    Usage, alongside the existing `svc.fmp = fake`:

        monkeypatch.setattr(module_under_test, "get_market_movers_service",
                            lambda: MoversFromFMPFake(fake))
    """

    def __init__(self, fmp: Any):
        self._fmp = fmp

    async def _raw_lists(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for name in ("get_biggest_gainers", "get_biggest_losers", "get_most_actives"):
            getter = getattr(self._fmp, name, None)
            if getter is None:
                continue
            rows = await getter()
            out.extend(r for r in (rows or []) if isinstance(r, dict))
        return out

    async def get_scanner_inputs(self):
        raw = await self._raw_lists()
        symbols, seen = [], set()
        for r in raw:
            s = (r.get("symbol") or "").upper()
            if s and s not in seen:
                seen.add(s)
                symbols.append(s)

        profile_map: Dict[str, Dict[str, Any]] = {}
        batch = getattr(self._fmp, "get_company_profiles_batch", None)
        if batch is not None and symbols:
            # Chunked at 50 like the real client was, so a fake that asserts on chunking
            # still sees the call pattern it expects.
            for i in range(0, len(symbols), 50):
                for p in (await batch(symbols[i:i + 50])) or []:
                    if isinstance(p, dict) and p.get("symbol"):
                        profile_map[p["symbol"].upper()] = p

        change_map: Dict[str, float] = {}
        for r in raw:
            s = (r.get("symbol") or "").upper()
            if not s or s in change_map:
                continue
            for key in ("changesPercentage", "changePercentage"):
                v = r.get(key)
                if v is None:
                    continue
                try:
                    change_map[s] = float(str(v).replace("%", ""))
                except (TypeError, ValueError):
                    pass
                break
        return profile_map, change_map

    async def get_sector_performance(self):
        getter = getattr(self._fmp, "get_sector_performance", None)
        return (await getter()) if getter else []

    async def get_industry_performance(self):
        getter = getattr(self._fmp, "get_industry_performance", None)
        return (await getter()) if getter else []
