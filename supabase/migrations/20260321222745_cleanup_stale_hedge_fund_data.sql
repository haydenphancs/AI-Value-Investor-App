-- Clean up stale hedge fund quarterly data with one-sided buy/sell volumes.
-- These rows were computed before the _estimate_buy_sell algorithm was added.
-- After deletion, the backend will re-fetch from FMP and compute proper two-sided volumes.

DELETE FROM hedge_fund_quarters
WHERE net_flow != 0
  AND buyers_count > 0
  AND sellers_count > 0
  AND (buy_volume = 0 OR sell_volume = 0);

-- Clear the holders_cache so all tickers get fresh hedge fund data.
-- Cache rebuilds automatically on next request (< 30 seconds per ticker).
DELETE FROM holders_cache;
