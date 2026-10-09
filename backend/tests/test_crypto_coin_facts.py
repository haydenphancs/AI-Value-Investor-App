"""`CryptoService.get_coin_facts` — supply facts plus the market-wide Fear & Greed reading.

Each leg degrades alone; max supply keeps the screen's three states; NaN / inf / bool /
non-positive values are omitted, never 0; the Fear & Greed read uses the gauge's own limit
(30) so it cannot shrink the shared cache; Gemini and the detail build are never reached.
Hermetic: `_get_coin_fundamentals` and the Fear & Greed client are stubbed.
"""

from __future__ import annotations

import json

import pytest

from app.services.crypto_service import CryptoService

_FG = [{"value": "62", "value_classification": "Greed", "timestamp": "1791331200"}]


def _svc(monkeypatch, coin=None, *, coin_raises=None, fg=None, fg_raises=None):
    svc = object.__new__(CryptoService)
    seen = {"coin": [], "fg_limits": []}

    async def _coin(symbol):
        seen["coin"].append(symbol)
        if coin_raises is not None:
            raise coin_raises
        return coin if coin is not None else {}

    async def _fg(limit=30):
        seen["fg_limits"].append(limit)
        if fg_raises is not None:
            raise fg_raises
        return fg if fg is not None else list(_FG)

    svc._get_coin_fundamentals = _coin
    monkeypatch.setattr("app.integrations.alternative_me.get_fear_greed_index", _fg)
    return svc, seen


def _btc(**md_over):
    md = {
        "circulating_supply": 19_850_000.0, "total_supply": 19_850_000.0,
        "max_supply": 21_000_000.0, "fully_diluted_valuation": {"usd": 1.6e12},
        "market_cap": {"usd": 1.5e12}, "last_updated": "2026-10-08T14:03:11.000Z",
    }
    md.update(md_over)
    return {"name": "Bitcoin", "market_cap_rank": 1, "market_data": md}


@pytest.mark.asyncio
async def test_a_full_read_states_supply_valuation_rank_and_the_credited_index(monkeypatch):
    svc, seen = _svc(monkeypatch, _btc())
    out = await svc.get_coin_facts("BTCUSD")
    assert seen["coin"] == ["BTC"], "the pair is reduced to the base the screen uses"
    assert seen["fg_limits"] == [30], "the gauge's own limit — never a smaller read"
    assert out["available"] is True and out["coin_status"] == "ok"
    assert out["name"] == "Bitcoin" and out["symbol"] == "BTC"
    assert out["circulating_supply"] == "19.85M BTC"
    assert out["max_supply"] == "21.00M BTC"
    assert out["fully_diluted_valuation"] == "USD 1.60T"
    assert out["fully_diluted_valuation_as_of"] == "2026-10-08 14:03 UTC"
    assert out["market_cap_rank"] == 1
    fg = out["crypto_fear_greed"]
    assert fg["value"] == 62 and fg["classification"] == "Greed"
    assert fg["source"] == "Crypto Fear & Greed Index by Alternative.me"
    assert "not this coin" in fg["scope"] and fg["as_of"] == "2026-10-07"
    assert "unavailable" not in out
    assert "current_price" not in json.dumps(out) and "price" not in out


@pytest.mark.asyncio
async def test_max_supply_keeps_three_states(monkeypatch):
    # Measured "no cap": the key is present and null.
    svc, _ = _svc(monkeypatch, {"name": "Dogecoin", "market_data": {
        "circulating_supply": 1.5e11, "max_supply": None}})
    out = await svc.get_coin_facts("DOGEX")
    assert out["max_supply"] == "no maximum supply (no hard cap)"
    # Not measured: absent key, not curated → unavailable, never "no cap".
    svc, _ = _svc(monkeypatch, {"name": "Foo", "market_data": {"circulating_supply": 10.0}})
    out = await svc.get_coin_facts("FOOX")
    assert "max_supply" not in out and "max supply" in out["unavailable"]
    # Curated profile (ETH is uncapped by its profile) → no cap even when the field is absent.
    svc, _ = _svc(monkeypatch, {"name": "Ethereum", "market_data": {"circulating_supply": 1.2e8}})
    out = await svc.get_coin_facts("ETH")
    assert out["max_supply"] == "no maximum supply (no hard cap)"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, 0, -5, "lots", None])
async def test_unusable_numbers_are_omitted_never_zero(monkeypatch, bad):
    svc, _ = _svc(monkeypatch, _btc(circulating_supply=bad, total_supply=bad,
                                    fully_diluted_valuation={"usd": bad}))
    out = await svc.get_coin_facts("BTC")
    for key in ("circulating_supply", "total_supply", "fully_diluted_valuation"):
        assert key not in out, key
    assert {"circulating supply", "total supply", "fully diluted valuation"} <= set(
        out["unavailable"])
    json.dumps(out, allow_nan=False)


@pytest.mark.asyncio
async def test_a_bad_rank_is_omitted(monkeypatch):
    coin = _btc()
    coin["market_cap_rank"] = float("nan")
    svc, _ = _svc(monkeypatch, coin)
    out = await svc.get_coin_facts("BTC")
    assert "market_cap_rank" not in out and "market-cap rank" in out["unavailable"]


@pytest.mark.asyncio
async def test_a_failed_fear_greed_read_drops_only_that_block(monkeypatch):
    from app.integrations.alternative_me import FearGreedUnavailableException

    svc, _ = _svc(monkeypatch, _btc(), fg_raises=FearGreedUnavailableException("down"))
    out = await svc.get_coin_facts("BTC")
    assert out["available"] is True and "crypto_fear_greed" not in out
    assert "Crypto Fear & Greed Index" in out["unavailable"]
    for bad in ([], [{"value": "abc"}], [{"value": "150"}], [None], [{"value": True}]):
        svc, _ = _svc(monkeypatch, _btc(), fg=bad)
        out = await svc.get_coin_facts("BTC")
        assert "crypto_fear_greed" not in out, bad


@pytest.mark.asyncio
async def test_a_failed_coin_read_keeps_the_market_reading_and_says_so(monkeypatch):
    from app.integrations.coingecko import CoinGeckoUnavailableException

    svc, _ = _svc(monkeypatch, coin_raises=CoinGeckoUnavailableException("429"))
    out = await svc.get_coin_facts("BTC")
    assert out["available"] is False and out["coin_status"] == "failed"
    assert out["crypto_fear_greed"]["value"] == 62
    assert "upstream" not in out, "a usable market reading is still an answer"
    assert out["name"] == "Bitcoin", "the curated name still resolves the coin"


@pytest.mark.asyncio
async def test_both_legs_failing_is_an_upstream_error(monkeypatch):
    svc, _ = _svc(monkeypatch, coin_raises=RuntimeError("down"), fg_raises=RuntimeError("down"))
    out = await svc.get_coin_facts("BTC")
    assert out["available"] is False and out["upstream"] is True


@pytest.mark.asyncio
async def test_an_unknown_coin_is_not_found_not_an_outage(monkeypatch):
    svc, _ = _svc(monkeypatch, {}, fg_raises=RuntimeError("down"))
    out = await svc.get_coin_facts("NOPECOIN")
    assert out["available"] is False and out["coin_status"] == "not_found"
    assert "upstream" not in out


@pytest.mark.asyncio
async def test_bad_symbols_never_raise(monkeypatch):
    svc, seen = _svc(monkeypatch, _btc())
    for bad in (None, "", "   ", 5, "BTC USD", "X" * 40):
        out = await svc.get_coin_facts(bad)  # type: ignore[arg-type]
        assert out["available"] is False
    assert seen["coin"] == []


@pytest.mark.asyncio
async def test_gemini_and_the_detail_build_are_never_reached(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("Gemini must not be reached from coin facts")

    monkeypatch.setattr("app.services.crypto_service.get_gemini_client", _boom)
    monkeypatch.setattr("app.integrations.gemini.get_gemini_client", _boom)
    svc, _ = _svc(monkeypatch, _btc())
    for name in ("get_crypto_detail", "_build_snapshots", "_generate_ai_snapshots"):
        async def _never(*_a, _n=name, **_k):
            raise AssertionError(f"{_n} must not be called")
        setattr(svc, name, _never)
    out = await svc.get_coin_facts("BTC")
    assert out["available"] is True


def test_the_method_reads_only_the_coin_fundamentals():
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(CryptoService.get_coin_facts)))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"}
    assert called == {"_get_coin_fundamentals"}, called
