-- 095_price_catalyst_cache_context.sql
--
-- Why: the "why it moved" catalyst cache (price_catalyst_cache, migration 058)
-- is keyed on `ticker` ALONE, but a catalyst reason is specific to the MOVE it
-- explains — its window and its direction. Two callers share the table: the
-- Updates sweeper (intraday session move, window "today") and the report
-- collector (multi-day window move). With a ticker-only key, one caller's cached
-- reason was served for the other's move for up to the 24h TTL — including the
-- OPPOSITE direction (a +22% 30-day-rally reason shown on a -7% intraday drop),
-- silently defeating the grounding/hallucination-guard investment.
--
-- Fix: store the move context on the row. The service now read-validates that a
-- cached row's (window_label, sign(change_pct)) matches the request and treats a
-- mismatch — or a legacy NULL row written before this migration — as a cache
-- miss (regenerate). One row per ticker is kept (the two callers evict each other
-- only on same-ticker overlap, which is rare and bounded).
--
-- Additive + nullable + idempotent. Safe to apply before OR after the code:
-- the read degrades to a cache miss until the columns exist.

BEGIN;

ALTER TABLE public.price_catalyst_cache
    ADD COLUMN IF NOT EXISTS window_label TEXT;

ALTER TABLE public.price_catalyst_cache
    ADD COLUMN IF NOT EXISTS change_pct DOUBLE PRECISION;

COMMIT;
