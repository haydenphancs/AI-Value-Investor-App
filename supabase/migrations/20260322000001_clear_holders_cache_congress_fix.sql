-- Clear holders cache to recompute with fixed logic:
-- Congress fixes:
--   1. "Over $X" amount parsing now uses 1.5x midpoint
--   2. "exchange" transaction type now counted as buy
--   3. Symbol-filtered disclosure endpoints for better coverage
-- Recent Activities fixes:
--   4. Quarter label now shows previous quarter (matches FMP data)
--   5. Flow summary computed from all activities, not just top 10
DELETE FROM holders_cache;
