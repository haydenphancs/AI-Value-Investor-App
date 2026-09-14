"""Insights enrichment only asks FMP for profiles of classes FMP profiles.

`profile?symbol=BTCUSD` is crypto data we do not licence, and a coin row never gains a
sector, so it stayed "missing" and the call repeated on EVERY insights load (every 30 s
refresh that re-fetched insights), with an INFO/WARNING line each time.
"""
import pytest

from app.services import portfolio_insights_service as pis


class _FMP:
    def __init__(self): self.asked = []
    async def get_company_profiles_batch(self, symbols):
        self.asked.append(list(symbols))
        return []


@pytest.mark.asyncio
async def test_coin_index_and_commodity_rows_are_never_sent_to_fmp_profiles(monkeypatch):
    fmp = _FMP()
    monkeypatch.setattr(pis, "get_fmp_client", lambda: fmp)
    svc = pis.PortfolioInsightsService.__new__(pis.PortfolioInsightsService)
    rows = [
        {"ticker": "BTCUSD", "asset_type": "crypto", "sector": None, "market_cap": None},
        {"ticker": "ETHUSD", "asset_type": None, "sector": None, "market_cap": None},
        {"ticker": "^GSPC", "asset_type": "index", "sector": None, "market_cap": None},
        {"ticker": "GCUSD", "asset_type": "commodity", "sector": None, "market_cap": None},
        {"ticker": "AAPL", "asset_type": "Stock", "sector": None, "market_cap": None},
        {"ticker": "SPY", "asset_type": "etf", "sector": None, "market_cap": None},
        {"ticker": "MSFT", "asset_type": "stock", "sector": "Technology", "market_cap": 3e12},
    ]
    await svc._enrich_missing("u", rows)
    assert fmp.asked == [["AAPL", "SPY"]], fmp.asked


@pytest.mark.asyncio
async def test_nothing_missing_means_no_call(monkeypatch):
    fmp = _FMP()
    monkeypatch.setattr(pis, "get_fmp_client", lambda: fmp)
    svc = pis.PortfolioInsightsService.__new__(pis.PortfolioInsightsService)
    await svc._enrich_missing("u", [{"ticker": "BTCUSD", "sector": None, "market_cap": None}])
    assert fmp.asked == []
