-- Grant permissions on holders_cache and hedge_fund_quarters for service_role
GRANT ALL ON holders_cache TO service_role;
GRANT ALL ON hedge_fund_quarters TO service_role;

-- Clear stale holders_cache so insiders get recomputed with ownership percentages
DELETE FROM holders_cache;
