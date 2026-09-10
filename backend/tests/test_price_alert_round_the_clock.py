"""Crypto price alerts must be able to fire when the US equity market is closed.

Two independent defects made a crypto alert useless, and BOTH had to be fixed:

  1. `price_service.get_quotes` dropped every symbol `is_blocked_symbol` refused, which
     includes crypto. The symbol was simply absent from the batch, so the rule saw no
     observation — no fire, no error, no log line. Silent.
  2. `run_price_alert_loop` gated the whole cycle on `session_phase() != "closed"`, the
     NYSE calendar. Bitcoin trades 24/7, so an alert could not fire overnight or across
     an entire weekend — the majority of the hours the asset actually moves.

`only_round_the_clock` narrows the universe instead of skipping the cycle. It defaults to
False so every pre-existing caller keeps its exact behaviour and stays independent of
wall-clock time — a test that changes meaning at 4pm ET is worse than no test.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.price_alert_service import PriceAlertService


class _NoQuotes:
    """The documented `price_source` seam (`price_service.price_source`): an instance
    attribute named `price` wins over the singleton.

    Without it `evaluate_once` reached the REAL price service for every non-empty
    universe — FMP for the equities, CoinGecko for BTCUSD. The hermeticity guard blocked
    those, `evaluate_once`'s own `except Exception` swallowed the failure and returned
    early, and every assertion below still held because `stats["tickers"]` is set BEFORE
    the fetch. Green either way, which is exactly the failure mode
    `.claude/rules/testing.md` describes.
    """

    async def get_quotes_list(self, tickers):
        return []


def _svc(universe):
    svc = PriceAlertService()
    svc.price = _NoQuotes()
    return svc, [
        patch.object(PriceAlertService, "_active_universe", lambda self: list(universe)),
        patch.object(PriceAlertService, "_active_rules", lambda self, tickers: []),
    ]


async def _run(universe, **kw):
    svc, patches = _svc(universe)
    for p in patches:
        p.start()
    try:
        return await svc.evaluate_once(**kw)
    finally:
        for p in patches:
            p.stop()


MIXED = ["AAPL", "MSFT", "BTCUSD", "GCUSD", "^GSPC"]


@pytest.mark.asyncio
async def test_the_open_session_still_evaluates_every_ticker():
    """The default path is unchanged — this is the anti-regression half."""
    stats = await _run(MIXED)
    assert stats["tickers"] == len(MIXED)


@pytest.mark.asyncio
async def test_the_closed_session_keeps_only_round_the_clock_assets():
    """Assert on the COUNT of the crypto subset, and on the identity below."""
    stats = await _run(MIXED, only_round_the_clock=True)
    assert stats["tickers"] == 1, "only BTCUSD trades while the NYSE is shut"


@pytest.mark.asyncio
async def test_the_surviving_ticker_is_the_crypto_one_by_identity():
    """A count alone would pass if the filter kept `^GSPC` and dropped BTCUSD."""
    seen: list[list[str]] = []

    svc = PriceAlertService()
    svc.price = _NoQuotes()
    with patch.object(PriceAlertService, "_active_universe", lambda self: list(MIXED)), \
         patch.object(PriceAlertService, "_active_rules",
                      lambda self, tickers: seen.append(list(tickers)) or []):
        await svc.evaluate_once(only_round_the_clock=True)

    assert seen == [["BTCUSD"]], f"filtered universe was {seen}"


@pytest.mark.asyncio
async def test_a_closed_cycle_with_no_crypto_spends_no_upstream_call():
    """CoinGecko bills 100,000 calls/MONTH — a wasted call/minute overnight is 14%.

    The short-circuit must happen BEFORE the quote fetch, not after it returns nothing.

    ⚠️ COUNT the calls; do not raise from the stub. `evaluate_once` wraps its quote fetch
    in `except Exception` and degrades to an empty cycle, so a stub that raises is
    swallowed and the test passes whether or not the call was made — verified by hand:
    moving the short-circuit after the fetch left an assert-raising version fully green.
    """
    svc = PriceAlertService()
    calls: list[tuple] = []

    def _record(*a, **kw):
        calls.append((a, kw))
        raise AssertionError("unreachable")

    with patch.object(PriceAlertService, "_active_universe",
                      lambda self: ["AAPL", "MSFT", "^GSPC"]), \
         patch.object(PriceAlertService, "_active_rules", lambda self, t: []), \
         patch("app.services.price_alert_service.price_source", _record):
        stats = await svc.evaluate_once(only_round_the_clock=True)

    assert calls == [], "the quote fetch ran for an empty universe"
    assert stats["tickers"] == 0


@pytest.mark.asyncio
async def test_an_empty_universe_is_still_a_no_op_in_both_modes():
    for kw in ({}, {"only_round_the_clock": True}):
        stats = await _run([], **kw)
        assert stats == {"tickers": 0, "rules": 0, "fired": 0, "sent": 0}
