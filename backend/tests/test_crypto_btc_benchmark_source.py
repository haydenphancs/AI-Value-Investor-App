"""The altcoin "vs BTC" benchmark must not be fetched from FMP.

FMP 402s every `…USD` crypto pair since entitlement enforcement went live 2026-09-03. The
coin's OWN history moved to CoinGecko in this phase; its BENCHMARK leg was left behind on
`self.fmp.get_historical_prices("BTCUSD", …)`, so `btc_hist` was always `[]` and the vs-BTC
YTD / 3Y / 5Y / All-Time rows were silently absent on every altcoin screen.

Silent because an omitted row is ALSO what a genuinely short history produces — there is no
observable difference between "BTC has no history" and "we asked a source that refuses to
answer". That is why this needs a guard rather than a glance at the screen.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from app.services import crypto_service as cs


def _detail_source() -> str:
    """`get_crypto_detail`'s body with its docstring removed.

    Docstring-stripped because the explanatory comment beside the fix names every token a
    naive scan greps for, so an un-stripped scan would pass on the prose after a revert.
    Brace-bounded to the ONE function for the same reason.
    """
    src = inspect.cleandoc(inspect.getsource(cs.CryptoService.get_crypto_detail))
    fn = ast.parse(src).body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    return ast.unparse(fn)


def test_the_btc_benchmark_reads_the_coingecko_history():
    body = _detail_source()
    assert "self._cg_history('BTC'" in body or 'self._cg_history("BTC"' in body


def test_the_fmp_btc_history_call_is_gated_not_unconditional():
    """"Hide, don't remove" is this phase's rule — the FMP leg stays reachable via
    `CRYPTO_PRICE_SOURCE=fmp`. What must not survive is an UNGATED call."""
    body = _detail_source()
    tree = ast.parse(body)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_historical_prices"):
            continue
        arg = ast.unparse(node.args[0]) if node.args else ""
        if "btc_fmp_symbol" not in arg and "BTCUSD" not in arg:
            continue          # the SPY leg — equities are still licensed
        # Walk up: this call must sit inside an `if self._fmp_crypto_enabled()`.
        assert _is_inside_fmp_gate(tree, node), (
            "the BTCUSD history call is not behind the CRYPTO_PRICE_SOURCE gate"
        )


def _is_inside_fmp_gate(tree, target) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if "_fmp_crypto_enabled" not in ast.unparse(node.test):
            continue
        if any(n is target for n in ast.walk(node)):
            # It must be in the TRUE branch, not the else.
            return any(n is target for b in node.body for n in ast.walk(b))
    return False


def test_the_benchmark_cache_key_carries_the_source():
    """A rolling deploy with `CRYPTO_PRICE_SOURCE` mixed across pods would otherwise let
    FMP-shaped rows (OHLC) be read by CoinGecko-shaped code and vice versa — a mismatch
    that renders as missing data rather than as an error."""
    body = _detail_source()
    assert "_hist_source" in body
    assert "btc_hist:{_hist_source}" in body


def test_all_time_is_suppressed_on_the_benchmark_too():
    """`_compute_all_time_return` is "first row → last row". Over CoinGecko Basic's 730-day
    cap that is "the last two years", published under the card's most authoritative label.
    The coin's own row was fixed; the benchmark's has to be fixed with it, or the card shows
    a real All-Time for the coin beside a 2-year one for BTC.

    Structural (AST), not a substring: every `bench_all = …(btc_hist)` assignment must be
    conditioned on `_history_reaches_all_time`.
    """
    tree = ast.parse(_detail_source())
    guarded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "bench_all" for t in node.targets):
            continue
        value = ast.unparse(node.value)
        if "btc_hist" not in value:
            continue                       # the SPY leg — its history really is all-time
        guarded.append("_history_reaches_all_time" in value)
    assert guarded, "no bench_all assignment from btc_hist found — the guard is stale"
    assert all(guarded), "an unguarded All-Time benchmark over a 730-day window"


@pytest.mark.asyncio
async def test_cg_history_is_clamped_to_the_plan_cap(monkeypatch):
    """The benchmark asks for `history_days`, which is already the cap — but `_cg_history`
    clamps regardless, and CoinGecko answers a 401 (error 10012, NOT an auth failure) for
    anything past two years."""
    monkeypatch.setattr(cs, "get_supabase", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_fmp_client", lambda: None, raising=True)

    asked = {}

    class _CG:
        async def get_market_chart(self, symbol, days, interval=None):
            asked["days"] = days
            return {"prices": [], "total_volumes": []}

    monkeypatch.setattr(cs, "get_coingecko_client", lambda: _CG(), raising=True)
    svc = cs.CryptoService()
    cs._cache.clear()
    await svc._cg_history("BTC", 365 * 15)
    assert asked["days"] == svc._history_days_cap()
    cs._cache.clear()
