"""`get_top_watchlist_tickers` returns whatever users watch — since the crypto/index
rebuild that includes ^GSPC, BTCUSD/ETHUSD/DOGEUSD and GCUSD. Every consumer that hands
those to an equity-only FMP endpoint pays a refused call (and a WARNING) per symbol per
run. `warm_ticker_collection` is gated in ticker_data_cache; these two are the other
consumers the 2026-09-11 audit found unguarded."""

from __future__ import annotations

import app.main as main_mod
from app.services.notification_senders.smart_money_sender import equity_tickers


_MIXED = ["AAPL", "^GSPC", "BTCUSD", "ETHUSD", "DOGEUSD", "GCUSD", "SPY", "BTC", "AAPL", "", "NVDA"]


def test_volatility_precompute_keeps_only_what_fmp_history_can_serve():
    out = main_mod._fmp_history_servable(_MIXED)
    assert "^GSPC" not in out and "BTCUSD" not in out and "ETHUSD" not in out and "GCUSD" not in out
    assert out[0] == "AAPL" and "SPY" in out and "NVDA" in out
    assert "BTC" in out, "bare BTC is the Grayscale ETF — FMP serves its history"
    assert out.count("AAPL") == 1 and "" not in out


def test_insider_phase_keeps_companies_only():
    out = equity_tickers(_MIXED)
    assert set(out) == {"AAPL", "SPY", "BTC", "NVDA"}
    assert out.count("AAPL") == 1
