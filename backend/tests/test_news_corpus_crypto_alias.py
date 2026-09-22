"""A coin scope answers to its BASE symbol in the subject filter.

TestFlight (ETH, 2026-09-02): "no AI insights" on a crypto chip. On today's code the
watchlist key is the canonical pair (`ETHUSD`), FMP tags the pair, enrichment appends
the bare coin (`ETH`), and headlines print "ETH" when they print anything at all —
`ETHUSD` never appears in prose. So without an alias a coin's card could only ever be
built from articles that spell out "Ethereum". `_scope_aliases` adds the base symbol
for crypto pairs ONLY; every non-crypto scope keeps its single-symbol behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.news_insight_service import (
    _MIN_ALIAS_TITLE_CHARS,
    _scope_aliases,
    article_is_about,
)


def _row(headline, tickers):
    return {
        "headline": headline,
        "related_tickers": tickers,
        "published_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    }


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("ETHUSD", ["ETHUSD", "ETH"]),
        ("ETHUSDT", ["ETHUSDT", "ETH"]),
        ("BTCUSD", ["BTCUSD", "BTC"]),
        ("DOGEUSD", ["DOGEUSD", "DOGE"]),
        # Not crypto: never split.
        ("USD", ["USD"]),          # the ETF ticker — stripping would leave nothing
        ("GCUSD", ["GCUSD"]),      # gold, a commodity pair
        ("CLUSD", ["CLUSD"]),      # crude
        ("AAPL", ["AAPL"]),
        ("ETH", ["ETH"]),          # the bare form is the listed security post-160
        ("", [""]),
        # Satisfy the suffix rule but are NOT coins the registry knows: a 4-letter
        # listed ticker (an alias of "X", U.S. Steel, would follow), an FX pair, an
        # unknown coin. The alias is gated on the registry, not the suffix.
        ("XUSD", ["XUSD"]),
        ("AUSD", ["AUSD"]),
        ("EURUSD", ["EURUSD"]),
        ("FOOUSD", ["FOOUSD"]),
    ],
)
def test_scope_aliases(symbol, expected):
    assert _scope_aliases(symbol) == expected


def test_aliases_always_lead_with_the_scope_itself():
    for s in ("ETHUSD", "AAPL", "GCUSD"):
        assert _scope_aliases(s)[0] == s


# ── lead tag ────────────────────────────────────────────────────────────────


def test_the_base_symbol_as_lead_tag_admits_the_pair_scope():
    # Enrichment (or a row first cached under the bare key) tags the coin bare.
    assert article_is_about(_row("Whales accumulate ahead of the upgrade", ["ETH", "BTC"]), "ETHUSD")


def test_the_pair_as_lead_tag_still_admits_the_pair_scope():
    assert article_is_about(_row("Whales accumulate ahead of the upgrade", ["ETHUSD"]), "ETHUSD")


def test_a_peer_coin_story_is_still_rejected():
    # BTC leads, ETH merely tagged, no ETH/Ethereum in the title → not about ETH.
    assert article_is_about(_row("Bitcoin hits an all-time high", ["BTCUSD", "ETHUSD"]), "ETHUSD") is False
    assert article_is_about(_row("Bitcoin hits an all-time high", ["BTC", "ETH"]), "ETHUSD") is False


# ── title token ─────────────────────────────────────────────────────────────


def test_the_base_token_in_the_title_admits_the_pair_scope():
    assert article_is_about(_row("ETH breaks $4,000 as withdrawals hit a 3-year high", ["BTCUSD"]), "ETHUSD")


def test_the_base_alias_is_whole_token_not_substring():
    assert article_is_about(_row("Ethical investing funds see record inflows", ["BTCUSD"]), "ETHUSD") is False
    assert article_is_about(_row("Methane emissions rules tighten", ["BTCUSD"]), "ETHUSD") is False


def test_ethusdt_shares_the_eth_alias():
    assert article_is_about(_row("ETH options volume doubles", ["BTCUSD"]), "ETHUSDT")


def test_a_two_letter_base_is_not_matched_against_prose():
    """OP (Optimism), AR (Arweave), PI, ZK are English-ish; a whole-token match on
    them inside the coin's own feed would admit every peer story using the word."""
    assert _MIN_ALIAS_TITLE_CHARS == 3
    for base in ("OP", "AR", "PI", "ZK"):
        scope = f"{base}USD"
        assert _scope_aliases(scope) == [scope, base]
        assert article_is_about(_row(f"{base} thing happened to bitcoin", ["BTCUSD"]), scope) is False
    # ...but the SAME short base as a LEAD TAG still admits (a tag is a symbol).
    assert article_is_about(_row("Optimism network upgrade ships", ["OP", "ETH"]), "OPUSD")


def test_a_three_letter_base_is_matched_against_prose():
    """The floor is 3 precisely so the tokens headlines actually print qualify."""
    for base in ("ETH", "BTC", "SOL", "XRP"):
        assert article_is_about(_row(f"{base} slides 4% as funding rates flip", ["BTCUSD"]), f"{base}USD")


def test_the_scope_symbol_itself_is_still_tested_at_any_length():
    """Anti-regression for the pre-existing rule: a 1-char equity symbol in the
    title still matches whole-token, exactly as before the alias existed."""
    assert article_is_about(_row("F recalls 100,000 trucks", ["GM"]), "F")
    assert article_is_about(_row("Fund managers rotate", ["GM"]), "F") is False


def test_the_coin_name_still_matches_via_company_name():
    row = _row("Ethereum ETF inflows top $1B", ["BTCUSD"])
    assert article_is_about(row, "ETHUSD", company_name="Ethereum")
    assert article_is_about(row, "ETHUSD") is False


# ── non-crypto scopes are byte-identical to before ───────────────────────────


def test_the_usd_etf_is_not_aliased():
    assert article_is_about(_row("Dollar index climbs", ["DXY"]), "USD") is False
    assert article_is_about(_row("USD strengthens against the yen", ["DXY"]), "USD")


def test_a_commodity_pair_is_not_aliased():
    # `GC` as lead tag or title token must NOT admit the gold scope.
    assert article_is_about(_row("GC futures rally", ["GC"]), "GCUSD") is False
    assert article_is_about(_row("Gold futures rally", ["GCUSD"]), "GCUSD")


# ── malformed inputs never raise ─────────────────────────────────────────────


@pytest.mark.parametrize("tags", [None, "ETH", 42, [None, "", 3], [" eth "]])
def test_malformed_related_tickers_never_raise(tags):
    row = {"headline": "ETH climbs", "related_tickers": tags}
    assert article_is_about(row, "ETHUSD") is True   # the title token carries it
    row = {"headline": "Bitcoin climbs", "related_tickers": tags}
    assert isinstance(article_is_about(row, "ETHUSD"), bool)


def test_a_lead_tag_is_normalised_before_the_alias_test():
    assert article_is_about({"headline": "x", "related_tickers": [" eth ", "BTC"]}, "ETHUSD")
    assert article_is_about({"headline": "x", "related_tickers": ["ethusd"]}, "ETHUSD")


# ── the roll-up prompt names a coin, not its pair ────────────────────────────


def test_the_prompt_names_a_coin_not_its_pair():
    """Live ETH card, 2026-09-20: "ETHUSD Sees Technical Upgrades…" — the model was
    told the subject was ETHUSD and parroted it. Equities keep their ticker."""
    from app.services.news_insight_service import NewsInsightService, _prompt_subject

    assert _prompt_subject("ETHUSD") == "Ethereum (ETH)"
    assert _prompt_subject("BTCUSD") == "Bitcoin (BTC)"
    assert _prompt_subject("ETHUSDT") == "Ethereum (ETH)"
    assert _prompt_subject("AAPL") == "AAPL"
    assert _prompt_subject("GCUSD") == "GCUSD"        # a commodity pair is not a coin
    assert _prompt_subject("FOOUSD") == "FOOUSD"      # unknown coin: no guess
    svc = object.__new__(NewsInsightService)
    prompt = svc._build_prompt("ETHUSD", [{"headline": "ETH climbs"}], "x", None, None)
    assert "for Ethereum (ETH) right now" in prompt
    assert "for ETHUSD right now" not in prompt
    equity = svc._build_prompt("AAPL", [{"headline": "A"}], "x", None, None)
    assert "for AAPL right now" in equity
