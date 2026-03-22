-- Clear holders_cache after insider classification logic fix.
-- The old logic incorrectly counted RSU vesting sales and option exercise
-- sales as informative insider selling, inflating sell volumes.
-- Clearing forces fresh computation with the corrected filters.
DELETE FROM holders_cache;
