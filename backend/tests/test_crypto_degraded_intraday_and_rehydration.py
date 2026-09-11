"""Two more split-failure shapes on the crypto detail path.

1. THE UNGUARDED INTRADAY LEG. `get_crypto_detail` wraps the 52-week band, the altcoin
   BTC-benchmark and the SPY leg in try/except so one CoinGecko hiccup degrades one
   section. The 1D/1W chart leg was the exception: `await self._cg_history(..., intraday=True)`
   sat bare, and `_cg_history` propagates. A 429 after its retries therefore failed the
   WHOLE detail — header, statistics, performance, everything that had already succeeded —
   but only on 1D/1W; 3M+ degraded politely. Same screen, two behaviours, decided by which
   pill the user last tapped.

2. A CACHED FAILURE THAT LOOKS LIKE A REAL ANSWER. On a Tier-2 (Supabase) hit,
   `_get_coin_fundamentals` re-hydrates the price half from one live `/coins/markets` row.
   When that row was unavailable the price-less payload was memoised under the normal
   5-minute key, so `get_crypto_core` raised for five minutes after a one-second hiccup —
   and `_live_markets_row` itself does NOT memoise a failure, so the memo was the only
   thing preventing recovery. Availability is judged from the INPUT (is `current_price` a
   `{usd: …}` dict?) and a degraded payload is memoised for the live row's 60 s only.

Hermetic: CoinGecko, Supabase and FMP are all stubbed.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import time

import pytest

from app.services import crypto_service as cs
from app.services.crypto_service import strip_volatile_market_data


# ── fixtures (mirroring test_crypto_live_price_not_persisted.py) ─────────────

def _coin_payload(price=79_000.0):
    return {
        "id": "bitcoin", "name": "Bitcoin",
        "market_data": {
            "current_price": {"usd": price},
            "market_cap": {"usd": 1.5e12},
            "total_volume": {"usd": 4.2e10},
            "high_24h": {"usd": price * 1.02},
            "low_24h": {"usd": price * 0.98},
            "price_change_24h": 1_200.0,
            "price_change_percentage_24h": 1.54,
            "price_change_percentage_30d": 8.1,
            "price_change_percentage_1y": 44.0,
            "circulating_supply": 19_800_000.0,
            "total_supply": 21_000_000.0,
            "max_supply": 21_000_000.0,
            "last_updated": "2026-09-08T00:00:00Z",
        },
        "description": {"en": "durable prose"},
        "genesis_date": "2009-01-03",
    }


def _markets_row(price=81_500.0):
    return {
        "id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
        "current_price": price, "market_cap": 1.55e12, "total_volume": 4.4e10,
        "high_24h": price * 1.01, "low_24h": price * 0.99,
        "price_change_24h": 900.0, "price_change_percentage_24h": 1.12,
        "last_updated": "2026-09-09T12:00:00Z",
    }


class _FakeCG:
    def __init__(self, row=None, fail=False):
        self._row, self.fail = row, fail
        self.markets_calls: list[list[str]] = []

    async def resolve_coin_id(self, base):
        if self.fail:
            raise RuntimeError("coingecko down")
        return "bitcoin" if base.upper() == "BTC" else None

    async def get_markets(self, bases):
        self.markets_calls.append(list(bases))
        if self.fail:
            raise RuntimeError("coingecko down")
        return [self._row] if self._row else []


@pytest.fixture
def svc(monkeypatch):
    cs._cache.clear()
    monkeypatch.setattr(cs, "get_supabase", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_fmp_client", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_coingecko_client", lambda: _FakeCG(), raising=True)
    s = cs.CryptoService()
    yield s
    cs._cache.clear()


# ── 2. the degraded re-hydration is not memoised for five minutes ───────────

@pytest.mark.asyncio
async def test_a_failed_rehydration_recovers_after_the_live_row_ttl(svc, monkeypatch):
    """Outage on call 1 (price absent). 61 s later CoinGecko is back — call 2 must carry
    the live price, not the memoised failure."""
    durable = strip_volatile_market_data(_coin_payload())
    monkeypatch.setattr(svc, "_check_crypto_cache_db", lambda _sym: durable)
    svc.coingecko = _FakeCG(fail=True)

    out1 = await svc._get_coin_fundamentals("BTC")
    assert "current_price" not in out1["market_data"]

    svc.coingecko = _FakeCG(_markets_row(price=81_500.0))
    t0 = time.time()
    monkeypatch.setattr(cs.time, "time", lambda: t0 + cs._CG_LIVE_MD_TTL + 1)
    out2 = await svc._get_coin_fundamentals("BTC")
    assert out2["market_data"]["current_price"] == {"usd": 81_500.0}, (
        "the price-less payload was memoised past the live row's TTL"
    )


@pytest.mark.asyncio
async def test_within_the_live_row_ttl_a_degraded_payload_is_a_herd_guard(svc, monkeypatch):
    """The failure IS memoised — for 60 s, not 300 — so a burst during an outage costs one
    call, and the durable half still renders."""
    durable = strip_volatile_market_data(_coin_payload())
    monkeypatch.setattr(svc, "_check_crypto_cache_db", lambda _sym: durable)
    svc.coingecko = _FakeCG(fail=True)
    await svc._get_coin_fundamentals("BTC")

    fake = _FakeCG(_markets_row())
    svc.coingecko = fake
    out = await svc._get_coin_fundamentals("BTC")          # no time shift
    assert "current_price" not in out["market_data"]
    assert out["market_data"]["circulating_supply"] == 19_800_000.0
    assert fake.markets_calls == [], "a burst inside the window must not re-fetch"


@pytest.mark.asyncio
async def test_a_successful_rehydration_is_memoised_for_the_full_window(svc, monkeypatch):
    """Anti-regression: the healthy path keeps its 5-minute memo (and its one credit)."""
    durable = strip_volatile_market_data(_coin_payload())
    monkeypatch.setattr(svc, "_check_crypto_cache_db", lambda _sym: durable)
    fake = _FakeCG(_markets_row())
    svc.coingecko = fake
    await svc._get_coin_fundamentals("BTC")
    t0 = time.time()
    monkeypatch.setattr(cs.time, "time", lambda: t0 + cs._CG_LIVE_MD_TTL + 1)
    out = await svc._get_coin_fundamentals("BTC")
    assert out["market_data"]["current_price"] == {"usd": 81_500.0}
    assert len(fake.markets_calls) == 1, fake.markets_calls


@pytest.mark.asyncio
async def test_a_live_row_with_a_null_price_is_treated_as_degraded(svc, monkeypatch):
    """Availability is judged from the INPUT: a row that arrives but carries no price is
    still no price. `_volatile_stripped` alone cannot tell the two apart."""
    durable = strip_volatile_market_data(_coin_payload())
    monkeypatch.setattr(svc, "_check_crypto_cache_db", lambda _sym: durable)
    row = _markets_row()
    row["current_price"] = None
    svc.coingecko = _FakeCG(row)
    await svc._get_coin_fundamentals("BTC")
    assert cs._cache_get("cg_fundamentals:BTC", cs._CACHE_TTL_SECONDS) is None, (
        "a price-less payload landed under the 5-minute key"
    )


# ── 1. the intraday leg is guarded like its siblings ─────────────────────────

def _detail_tree() -> ast.AST:
    src = textwrap.dedent(inspect.getsource(cs.CryptoService.get_crypto_detail))
    return ast.parse(src)


def _intraday_history_calls(tree: ast.AST):
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "_cg_history":
            continue
        if any(k.arg == "intraday" and isinstance(k.value, ast.Constant) and k.value.value is True
               for k in node.keywords):
            out.append(node)
    return out


def test_every_intraday_history_await_sits_inside_a_try():
    tree = _detail_tree()
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    calls = _intraday_history_calls(tree)
    assert calls, "anti-vacuity: get_crypto_detail no longer calls _cg_history(intraday=True)"
    for call in calls:
        node, guarded = call, False
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.Try) and any(
                call in ast.walk(stmt) for stmt in node.body
            ):
                guarded = True
                break
        assert guarded, (
            f"line {call.lineno}: the 1D/1W chart leg is awaited bare — a CoinGecko 429 "
            "fails the whole detail instead of emptying the chart"
        )


@pytest.mark.asyncio
async def test_an_intraday_outage_empties_the_chart_and_keeps_the_screen(svc, monkeypatch):
    """Behavioural twin of the AST guard: every other leg succeeds, the 1D leg raises."""
    daily = [
        {"date": f"2026-0{m}-{d:02d}", "close": 70_000.0 + i * 10, "volume": 1.0e9}
        for i, (m, d) in enumerate([(7, 1), (7, 2), (7, 3), (8, 1), (8, 2), (9, 1)])
    ]

    async def fake_history(symbol, days, intraday=False):
        if intraday:
            raise cs.CoinGeckoRateLimitException("429") if hasattr(cs, "CoinGeckoRateLimitException") \
                else RuntimeError("429")
        return list(daily)

    async def fake_fundamentals(symbol):
        return _coin_payload(price=79_000.0)

    async def fake_related(symbols):
        return []

    async def fake_band(symbol):
        return (126_080.0, 57_779.0)

    async def fake_snapshots(*a, **k):
        return []

    monkeypatch.setattr(svc, "_cg_history", fake_history)
    monkeypatch.setattr(svc, "_get_coin_fundamentals", fake_fundamentals)
    monkeypatch.setattr(svc, "_cg_related_quotes", fake_related)
    monkeypatch.setattr(svc, "_cg_52_week_band", fake_band)
    monkeypatch.setattr(svc, "_build_snapshots", fake_snapshots)

    class _NoFMP:
        async def get_historical_prices(self, *a, **k):
            raise RuntimeError("fmp not used in this test")

    svc.fmp = _NoFMP()

    out = await svc.get_crypto_detail("BTC", chart_range="1D")
    assert out.chart_data == []
    assert out.current_price == 79_000.0
    assert out.symbol == "BTC"


# ── 3. rolling returns are never served from the persisted row ──────────────

@pytest.mark.asyncio
async def test_a_db_hit_carries_no_rolling_returns_so_the_live_fallback_runs(svc, monkeypatch):
    """`price_change_percentage_30d/1y` are exactly as stale as the price they derive
    from. On a DB hit they must be ABSENT so `get_crypto_detail` computes them from the
    coin's live history (`_compute_return(historical, 30/365)`), and the altcoin
    vs-BTC 1M/1Y rows likewise fall back to BTC's live series."""
    payload = _coin_payload()
    payload["market_data"]["price_change_percentage_30d"] = 8.1
    payload["market_data"]["price_change_percentage_1y"] = 44.0
    durable = strip_volatile_market_data(payload)
    monkeypatch.setattr(svc, "_check_crypto_cache_db", lambda _sym: durable)
    svc.coingecko = _FakeCG(_markets_row())
    out = await svc._get_coin_fundamentals("BTC")
    md = out["market_data"]
    assert "price_change_percentage_30d" not in md
    assert "price_change_percentage_1y" not in md
    assert md["current_price"] == {"usd": 81_500.0}


def test_the_builder_falls_back_to_computed_returns_when_the_fields_are_absent():
    src = textwrap.dedent(inspect.getsource(cs.CryptoService.get_crypto_detail))
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    i = code.index('md.get("price_change_percentage_30d")')
    assert "_compute_return(historical, 30)" in code[i:i + 400]
    j = code.index('btc_md.get("price_change_percentage_30d")')
    assert "_compute_return(btc_hist, 30)" in code[j:j + 6000]
