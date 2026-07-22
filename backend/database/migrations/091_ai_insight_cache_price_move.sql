-- 091_ai_insight_cache_price_move.sql
--
-- Why: the Updates insight card can now carry a grounded "why did it move" block
-- for a big (Unusual/Extreme) price move — {tier, change_percent, catalyst_tag,
-- reason} — sourced from the web-search-grounded price_catalyst_service (real
-- source citations, hallucination guard). It is a SEPARATE, optional field from
-- the Flash-Lite news bullets so the two have distinct provenance (the move
-- reason is cited; the news bullets are the news roll-up).
--
-- Additive + nullable: a card without a big move stores NULL, an old client
-- simply ignores the column, and a malformed block is coerced/dropped by the
-- service before it reaches here — so this never blocks or fails a news card.
--
-- Idempotent. Apply manually (Supabase Studio / CLI).

BEGIN;

ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS price_move JSONB;

COMMIT;
