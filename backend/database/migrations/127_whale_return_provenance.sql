-- 127_whale_return_provenance.sql
--
-- Why: the Whale Profile screen shows "+5.9%" captioned "13F Portfolio CAGR", and the
-- number carries no provenance — so the caption can be, and sometimes is, false.
--
--   * The window is `1 / len(year_end_returns)`: a 2-year and a 10-year figure render
--     identically, and the user cannot tell which they are looking at.
--   * When FMP returned no December-31 rows, the old code fell back to a SINGLE latest
--     1-year return and still labelled it a compound annual growth rate.
--   * A NULL return was coerced to 0.0 and rendered as a confident green "+0.0%", making
--     "we have no data" indistinguishable from "this whale was flat".
--
-- These two columns let the API say what the number IS, so the client can caption it
-- honestly ("13F Portfolio CAGR · 5-yr") or refuse to draw it at all (an em-dash under
-- "Not enough history") instead of always printing a percent.
--
-- ⚠️ APPLY BEFORE DEPLOYING THE BACKEND.
-- `_sync_to_whale_tables` writes these alongside `portfolio_value`, `behavior_summary`
-- and `sentiment_summary` inside ONE try block (whale_service.py). Against a missing
-- column PostgREST rejects the whole statement with PGRST204, so deploying first does
-- not merely skip the new fields — it silently stops updating the portfolio value and
-- the sentiment summary too. Reads are safe in either order: `whale.get(...)` yields
-- None, which the serve path already maps to the legacy "" status.
--
-- No column is needed for the portfolio as-of quarter or the filing date. Both already
-- live on `whale_filing_snapshots` (`filing_period`, `filing_date`) and are derived at
-- serve time by `period_labels.filing_period_display`.
--
-- Safe to re-run.

BEGIN;

ALTER TABLE public.whales
    ADD COLUMN IF NOT EXISTS return_window_years INTEGER;

COMMENT ON COLUMN public.whales.return_window_years IS
    'Calendar years compounded into ytd_return. NULL means the figure is not a '
    'multi-year CAGR — the client then omits the "· N-yr" suffix rather than '
    'implying a window it does not know. See migration 127.';

ALTER TABLE public.whales
    ADD COLUMN IF NOT EXISTS return_status TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN public.whales.return_status IS
    'ok | insufficient_history | unavailable. Drives whether the Whale Profile draws a '
    'percent or an em-dash. Empty string = a row written before migration 127; the serve '
    'path treats that as "trust the legacy value" so nothing blanks during rollout. '
    'insufficient_history and unavailable are deliberately distinct: only the former is a '
    'judgement about the whale, and only the former may clear a stored ytd_return — an '
    'upstream fetch failure must never blank good data. See migration 127.';

COMMIT;

-- ── After applying ───────────────────────────────────────────────────────────────────
--
-- Existing rows keep whatever `ytd_return` they already hold, including values the old
-- 1-year fallback produced. Those are only corrected by a re-hydration, and the
-- hydration job SHORT-CIRCUITS on an unchanged `raw_hash` — so the repair needs:
--
--     python -m scripts.hydrate_whales --force
--
-- and then a cache bust, because `whale_profile_cache` replays assembled JSON for 24h:
--
--     DELETE FROM whale_profile_cache;
--
-- Deliberately NOT done as an UPDATE here: this migration cannot tell a bogus 1-year
-- value from a legitimate multi-year CAGR, so blanking them wholesale would em-dash
-- roughly 45 healthy whales until the next hydration run.
