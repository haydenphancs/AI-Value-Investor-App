-- Remove Michael Saylor from the whales table.
-- MicroStrategy does not file 13F, and the hydration engine skips
-- non-13F/non-congressional whales, so the profile was permanently
-- blank. The cryptoWhales UI category is being dropped alongside.
--
-- All dependent tables (whale_follows, whale_filing_snapshots,
-- whale_trade_groups, whale_trades, whale_holdings,
-- whale_sector_allocations, whale_alerts, whale_profile_cache)
-- declare whale_id with ON DELETE CASCADE, so this single DELETE
-- cleans up all related rows.

DELETE FROM whales WHERE name = 'Michael Saylor';
