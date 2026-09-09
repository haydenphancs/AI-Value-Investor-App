"""The runtime half of the FMP licence guard.

`test_fmp_entitlement_parity.py` is a SOURCE scan — it proves nothing new gets *written*.
This file proves the guard actually *behaves* at runtime: that a blocked call is refused
before it costs anything, that the refusal is legible, and that the symbol axis is
enforced on endpoints we do own.

Hermetic: `_raise_if_not_entitled` is a staticmethod and makes no I/O, so these run
without a client, a network, or the conftest socket guard ever being touched.
"""

from __future__ import annotations

import pytest

from app.integrations.fmp import (
    FMPClient,
    FMPException,
    FMPNotEntitledException,
    FMPUnavailableException,
)
from app.integrations.fmp_entitlements import (
    BLOCKED_PATHS,
    ENTITLED_PATHS,
    PACKAGE_OF,
    PURCHASED_PACKAGES,
    SUBSTITUTION,
    is_blocked_symbol,
    normalize_path,
)

guard = FMPClient._raise_if_not_entitled


# ---------------------------------------------------------------------------- endpoints

@pytest.mark.parametrize("path", ["profile", "income-statement", "etf/holdings",
                                  "news/stock", "analyst-estimates", "company-screener",
                                  "search-symbol", "senate-trades"])
def test_entitled_endpoints_pass(path: str) -> None:
    guard(path, {"symbol": "AAPL"})


@pytest.mark.parametrize("path", ["quote", "batch-quote", "grades", "dividends",
                                  "splits", "biggest-gainers", "sp500-constituent",
                                  "earning-call-transcript"])
def test_blocked_endpoints_are_refused(path: str) -> None:
    with pytest.raises(FMPNotEntitledException):
        guard(path, {"symbol": "AAPL"})


def test_refusal_names_the_package_and_the_substitute() -> None:
    """The message must be actionable at the failure site, not a bare 'not allowed'."""
    with pytest.raises(FMPNotEntitledException) as exc:
        guard("quote", {"symbol": "AAPL"})
    msg = str(exc.value)
    assert "Real-time Market Data" in msg, "must name the package that would unlock it"
    assert "profile" in msg, "must name the entitled substitute"
    assert "PURCHASED_PACKAGES" in msg, "must say how to re-enable after buying it"


def test_not_entitled_is_not_treated_as_transient() -> None:
    """Subclassing FMPUnavailableException would make callers RETRY a permanent state.

    `FMPUnavailableException` means "upstream flaked, back off and try later". A licence
    refusal can never succeed on retry, and the retry/backoff paths key off that type —
    so the hierarchy is load-bearing, not cosmetic.
    """
    assert issubclass(FMPNotEntitledException, FMPException)
    assert not issubclass(FMPNotEntitledException, FMPUnavailableException)


def test_guard_does_not_count_as_an_upstream_failure() -> None:
    """`request_failures` feeds health/degradation signals — a licence refusal is not one."""
    client = FMPClient.__new__(FMPClient)
    client.request_failures = 0
    with pytest.raises(FMPNotEntitledException):
        client._raise_if_not_entitled("quote", {"symbol": "AAPL"})
    assert client.request_failures == 0


# ------------------------------------------------------------------------------ symbols

@pytest.mark.parametrize("symbol", ["^GSPC", "^IXIC", "^DJI", "^VIX", "^TNX",
                                    "GCUSD", "CLUSD", "SIUSD", "NGUSD",
                                    "BTCUSD", "ETHUSD", "SOLUSD",
                                    "EURUSD", "USDJPY"])
def test_blocked_symbols_are_refused_on_entitled_endpoints(symbol: str) -> None:
    """The axis that is easy to miss: the endpoint is licensed, the symbol is not."""
    assert "historical-price-eod/full" in ENTITLED_PATHS
    with pytest.raises(FMPNotEntitledException):
        guard("historical-price-eod/full", {"symbol": symbol})
    with pytest.raises(FMPNotEntitledException):
        guard("historical-chart/5min", {"symbol": symbol})


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "SPY", "QQQ", "BRK-B",
                                    "SHOP.TO", "AAPL.DE"])
def test_equities_and_etfs_are_not_swept_up(symbol: str) -> None:
    """The packages carry '60+ Global Exchanges' — international equities are fine."""
    guard("historical-price-eod/full", {"symbol": symbol})
    guard("historical-chart/1min", {"symbol": symbol})


def test_symbol_gate_applies_only_to_market_data_endpoints() -> None:
    """`profile` accepts an index symbol and returns [] — it is not 402, so do not gate it.

    Over-gating here would break the honest empty-state path, turning "no data" into an
    exception on an endpoint FMP is happy to serve.
    """
    guard("profile", {"symbol": "^GSPC"})
    guard("news/crypto", {"symbols": "BTCUSD"})


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_symbol_is_not_blocked(value) -> None:
    """Outlier inputs must not turn a legitimate no-symbol call into a refusal."""
    assert is_blocked_symbol(value) is False
    guard("historical-price-eod/full", {"symbol": value})
    guard("historical-price-eod/full", {})
    guard("historical-price-eod/full", None)


@pytest.mark.parametrize("symbol", ["btcusd", " ^gspc ", "gcusd"])
def test_symbol_matching_is_case_and_whitespace_insensitive(symbol: str) -> None:
    """A lowercase or padded symbol must not slip past the gate."""
    assert is_blocked_symbol(symbol) is True


@pytest.mark.parametrize("symbol", ["USD", "XUSD", "AUSD"])
def test_short_symbols_are_not_mistaken_for_fx_pairs(symbol: str) -> None:
    """The suffix rule needs a length floor or it eats real tickers."""
    assert is_blocked_symbol(symbol) is False


@pytest.mark.parametrize("symbol", ["PIUSD", "OPUSD", "ZKUSD"])
def test_two_char_coin_bases_are_blocked_despite_the_length_floor(symbol: str) -> None:
    """A 5-char pair with a 2-char base is still crypto, and still 402s.

    The `len(s) >= 6` floor above cannot see these. Pi, Optimism and zkSync are real
    coins, so every quote for them went out to FMP, came back 402, and surfaced at
    ERROR — while `detect_asset_class` had always classified them as crypto. The two
    classifiers disagreed about the same symbol.

    The repair is membership in `SYMBOL_TO_COINGECKO_ID`, not a lower floor: the base
    either names a coin we can price or it does not. `test_short_symbols_...` above
    stays green precisely because X and A are not coins.
    """
    assert is_blocked_symbol(symbol) is True


def test_every_known_coin_pair_is_blocked_on_both_suffixes() -> None:
    """No coin in the map may reach FMP under either quote convention.

    Assert over the whole map rather than a sample, so adding a 2-char-base coin
    cannot silently reopen the gap.
    """
    from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID

    leaked = [
        f"{base}{suffix}"
        for base in SYMBOL_TO_COINGECKO_ID
        for suffix in ("USD", "USDT")
        if not is_blocked_symbol(f"{base}{suffix}")
    ]
    assert leaked == [], f"these coin pairs would reach FMP: {leaked}"


@pytest.mark.parametrize("symbol", ["QTUMUSD", "WAVESUSD"])
def test_long_tail_coin_pairs_still_rely_on_the_length_rule(symbol: str) -> None:
    """The coin-map arm must ADD coverage, never replace the length rule.

    The hardcoded map is the top ~110 coins only, so a long-tail pair is caught by
    `len(s) >= 6` alone. Without this test, deleting the length rule entirely left
    the whole file green — the map arm happens to cover BTCUSD/ETHUSD, which is
    what the other crypto tests assert. Verified by hand: these two bases are
    absent from SYMBOL_TO_COINGECKO_ID, so this is the rule's unique coverage.
    """
    from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID

    base = symbol[: -len("USD")]
    assert base not in SYMBOL_TO_COINGECKO_ID, (
        f"{base} joined the coin map — this test no longer pins the length rule; "
        "pick another long-tail base"
    )
    assert is_blocked_symbol(symbol) is True


def test_the_coin_map_rule_agrees_with_detect_asset_class() -> None:
    """The two classifiers must not disagree about any coin in the map.

    This divergence is what the bug was: one module calling a symbol crypto while
    the other let it through to a provider that cannot serve it.
    """
    from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
    from app.services.asset_class import detect_asset_class

    disagreements = [
        pair
        for base in SYMBOL_TO_COINGECKO_ID
        for pair in (f"{base}USD",)
        if detect_asset_class(pair) == "crypto" and not is_blocked_symbol(pair)
    ]
    assert disagreements == [], f"classified crypto but not blocked: {disagreements}"


# ------------------------------------------------------------------------------- paths

def test_normalize_path_folds_the_chart_template() -> None:
    """`historical-chart/{interval}` is formatted at the call site; both forms must map."""
    assert normalize_path("historical-chart/{interval}") in ENTITLED_PATHS
    assert normalize_path("historical-chart/5min") in ENTITLED_PATHS
    assert normalize_path("/profile?symbol=AAPL") == "profile"


def test_entitled_and_blocked_are_disjoint_and_derived() -> None:
    """Both sets come from PACKAGE_OF, so they cannot drift apart or double-count."""
    assert not (ENTITLED_PATHS & set(BLOCKED_PATHS))
    assert ENTITLED_PATHS | set(BLOCKED_PATHS) == set(PACKAGE_OF)
    for path, package in BLOCKED_PATHS.items():
        assert package not in PURCHASED_PACKAGES, f"{path} maps to a purchased package"


def test_every_substitution_names_a_blocked_path() -> None:
    """A substitution hint for a path that is not blocked is stale documentation."""
    stale = set(SUBSTITUTION) - set(BLOCKED_PATHS)
    assert not stale, f"SUBSTITUTION describes non-blocked path(s): {sorted(stale)}"


# ── FX pairs quoted in a minor ───────────────────────────────────────────────────────


@pytest.mark.parametrize("symbol", [
    "USDCAD", "USDCHF", "USDMXN", "USDSEK", "USDNOK", "USDTRY", "USDZAR",
    "EURNOK", "EURCHF", "EURPLN", "GBPAUD", "AUDNZD", "CADJPY",
])
def test_pairs_quoted_in_a_minor_are_blocked(symbol):
    """The suffix rule is QUOTE-currency-only and missed every one of these.

    `BLOCKED_SYMBOL_SUFFIXES` lists USD/JPY/EUR/GBP/CNY, so `USDCAD` — which ends in
    "CAD" — passed the guard and went out as a live request, answered 402, and surfaced
    as an untyped upstream failure instead of a clean "not licensed" skip. Widening the
    suffix set is the wrong repair: it would start matching real six-letter tickers by
    their last three characters. Both halves being ISO-4217 is the exact test.
    """
    assert is_blocked_symbol(symbol) is True


@pytest.mark.parametrize("symbol", [
    "AAPL", "GOOGL", "BRK-B", "SHOP.TO", "NVDA", "MSFT", "ASML", "TSM",
])
def test_the_fx_rule_does_not_touch_real_equities(symbol):
    assert is_blocked_symbol(symbol) is False


def test_crypto_pairs_still_rely_on_the_suffix_rule():
    """BTC/ETH/SOL are not ISO-4217, so the both-halves test cannot see them.

    Pinned because deleting the suffix rule in favour of the "tighter" FX one would
    silently unblock every crypto pair.
    """
    from app.integrations.fmp_entitlements import _is_fx_pair

    assert _is_fx_pair("BTCUSD") is False
    assert is_blocked_symbol("BTCUSD") is True
    assert is_blocked_symbol("ETHUSD") is True
