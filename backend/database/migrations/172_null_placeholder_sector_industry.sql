-- 172_null_placeholder_sector_industry.sql
--
-- Why: `watchlist_items.sector` (and `industry`) hold the literal string 'N/A' for some
-- rows, and the Portfolio Insights card drew it as a sector of its own — a legend row
-- reading "N/A 0%" beside "Other", with `sector_count` and the Sector Spread HHI counting
-- one bucket too many (TestFlight 1.0 (8), tracking_watchlist_portfolio E2).
--
-- How the string got there: `stock_overview_service._build_sector_industry` renders an
-- empty FMP sector as 'N/A' for the ticker detail screen and stores that formatted dict
-- in `company_profile_cache.profile_json`; the Tracking feed's `_backfill_classification`
-- then copied `profile_json.sector` onto the watchlist row, rejecting only ''/NULL. A
-- broad-market ETF the user opened once (SPY, QQQ — FMP has no sector for them) is the
-- typical victim. The rows were STICKY: both healers (`_backfill_classification` and
-- `PortfolioInsightsService._enrich_missing`) test falsiness to decide what is "missing",
-- so a row holding 'N/A' was never revisited even when FMP later answered.
--
-- The code half ships in the same change: every writer into these columns now goes
-- through `_classification_common.is_placeholder_text` (POST /watchlist, POST
-- /tracking/holdings, the feed backfill, `_enrich_missing`), the feed normalises `sector`
-- on read, and `score_holdings` folds any surviving placeholder into "Other". This
-- migration is the one-shot clean-up of what is already stored, so those rows re-enter
-- the healers' "missing" set and can be classified properly.
--
-- Consequence worth knowing: for a symbol FMP genuinely has no sector for, the healers
-- will re-read the profile cache once per feed build and heal nothing — one bounded
-- Supabase read per build, the same class of repeat as any never-classified ETF. A
-- `classified_at` marker is the follow-up if it shows in the logs.
--
-- NOT destructive in the data sense: the only values touched are placeholders that mean
-- "unknown" — the same set as PLACEHOLDER_TEXT in
-- backend/app/services/_classification_common.py (deliberately WITHOUT 'unknown', which
-- is a size-bucket label the service emits, not a value it reads). `country` is left
-- alone: it has a 'US' default and no placeholder was observed there. Idempotent: a
-- second run matches zero rows.

UPDATE public.watchlist_items
   SET sector = NULL
 WHERE sector IS NOT NULL
   AND lower(btrim(sector)) IN ('', 'n/a', 'na', 'n.a.', 'none', 'null', 'nan', '-', '—', '–');

UPDATE public.watchlist_items
   SET industry = NULL
 WHERE industry IS NOT NULL
   AND lower(btrim(industry)) IN ('', 'n/a', 'na', 'n.a.', 'none', 'null', 'nan', '-', '—', '–');

-- VERIFY (run after applying) — both must return 0:
--   SELECT count(*) FROM public.watchlist_items
--    WHERE lower(btrim(sector))   IN ('', 'n/a', 'na', 'n.a.', 'none', 'null', 'nan', '-', '—', '–');
--   SELECT count(*) FROM public.watchlist_items
--    WHERE lower(btrim(industry)) IN ('', 'n/a', 'na', 'n.a.', 'none', 'null', 'nan', '-', '—', '–');
