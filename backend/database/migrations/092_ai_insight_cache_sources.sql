-- 092_ai_insight_cache_sources.sql
--
-- Why: the Updates Insights card can now carry the list of source articles it was
-- summarised from — [{title, url}] — so tapping the card opens a screen with the
-- summary on top and the underlying stories below, each tappable to the publisher.
-- These are the LITERAL corpus inputs the card was built from (headline + article
-- url captured at generation), not a model self-claim, so they are 100% accurate
-- and need no per-bullet attribution.
--
-- Additive + nullable: a card without sources stores NULL, an old client simply
-- ignores the column, and a malformed value is coerced/dropped by the service
-- before it reaches here — so this never blocks or fails a news card. Unlike the
-- LLM-output columns, this needs NO prompt_version bump: existing cards gain
-- `sources` on their next natural regeneration.
--
-- Idempotent. Apply manually (Supabase Studio / CLI) BEFORE deploying the code
-- that writes it — `_store` includes the column unconditionally.

BEGIN;

ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS sources JSONB;

COMMIT;
