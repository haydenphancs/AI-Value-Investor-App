"""The MAX_UNIVERSE cap must never permanently exclude an alerted ticker.

The paged read (`fetch_all_rows`) fixed the PostgREST 1,000-row clamp, but the cap that
followed it was a fixed prefix over an id-ordered list — the same newest tickers were
dropped every cycle, and the crypto (round-the-clock) filter ran AFTER the cut, so a
Bitcoin alert past the 500th distinct equity never fired, overnight or in session.
"""
from unittest.mock import patch

from app.services import price_alert_service as pas
from app.services.price_alert_service import PriceAlertService, MAX_UNIVERSE


def _rows(tickers):
    return [{"ticker": t} for t in tickers]


def _svc(rows):
    svc = PriceAlertService.__new__(PriceAlertService)
    svc.supabase = object()
    return svc, patch.object(pas, "fetch_all_rows", lambda *a, **k: rows)


def test_a_coin_past_the_equity_cap_is_still_evaluated_overnight():
    equities = [f"EQ{i:04d}" for i in range(MAX_UNIVERSE + 1)]
    svc, p = _svc(_rows(equities + ["BTCUSD"]))
    with p:
        assert svc._active_universe(only_round_the_clock=True) == ["BTCUSD"]


def test_the_cap_rotates_so_every_ticker_is_quoted_within_a_few_cycles():
    universe = [f"T{i:04d}" for i in range(MAX_UNIVERSE * 2 + 137)]
    svc, p = _svc(_rows(universe))
    seen = set()
    with p:
        windows = [svc._active_universe() for _ in range(3)]
    for w in windows:
        assert len(w) == MAX_UNIVERSE
        seen.update(w)
    assert windows[0] != windows[1] != windows[2]
    assert seen == set(universe), "some tickers were never reached"


def test_below_the_cap_the_order_and_set_are_untouched():
    universe = ["AAPL", "MSFT", "BTCUSD", "aapl", None, ""]
    svc, p = _svc(_rows(universe))
    with p:
        assert svc._active_universe() == ["AAPL", "MSFT", "BTCUSD"]
        assert svc._active_universe(only_round_the_clock=True) == ["BTCUSD"]


def test_an_empty_or_failed_read_is_an_empty_universe():
    svc, p = _svc([])
    with p:
        assert svc._active_universe() == []
    svc2 = PriceAlertService.__new__(PriceAlertService)
    svc2.supabase = object()
    def _boom(*a, **k):
        raise RuntimeError("edge 520")
    with patch.object(pas, "fetch_all_rows", _boom):
        assert svc2._active_universe(only_round_the_clock=True) == []
