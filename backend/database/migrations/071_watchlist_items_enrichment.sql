-- 071_watchlist_items_enrichment.sql
--
-- Why: Portfolio Insights diversification scoring needs more signals than the
-- current `sector` + `country` columns. The modern score breaks a portfolio
-- down by market-cap mix (mega/large/mid/small) and geography, and the donut
-- needs a real sector. Today `sector`/`country` are populated ONLY on the
-- holdings-add path, so watchlist-only rows score as "Other 100%". This adds
-- the missing profile fields and lets PortfolioInsightsService lazy-backfill
-- all of them from the FMP company profile on the next insights read.
--
-- Schema: three nullable profile columns on the existing (RLS-enabled) user
-- table `watchlist_items`. No new RLS/grants needed — columns inherit the
-- table's existing row-level policies. A partial index supports the
-- "rows still missing enrichment" scan the service runs.

ALTER TABLE public.watchlist_items ADD COLUMN IF NOT EXISTS industry   TEXT;
ALTER TABLE public.watchlist_items ADD COLUMN IF NOT EXISTS market_cap NUMERIC(24,2);
ALTER TABLE public.watchlist_items ADD COLUMN IF NOT EXISTS beta       NUMERIC(10,4);

-- Hot path: the insights service scans a user's rows whose `sector` is still
-- null to decide which tickers need an FMP profile fetch. Partial index keeps
-- that scan cheap and tiny (only un-enriched rows are indexed).
CREATE INDEX IF NOT EXISTS idx_watchlist_items_needs_enrich
    ON public.watchlist_items(user_id) WHERE sector IS NULL;
