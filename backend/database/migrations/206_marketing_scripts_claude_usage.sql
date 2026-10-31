-- 206_marketing_scripts_claude_usage.sql
--
-- Why: the marketing writer and judge can now ask Claude first, with the same call on Gemini as the
-- fallback (2026-10-30, `app/services/llm_provider.py`; every switch defaults to gemini). Each
-- generation's tokens — Gemini's and Claude's together — are summed into
-- `marketing_scripts.tokens_used`, and the Monday digest's weekly cost line priced ALL of them at the
-- one blended Gemini rate ($1.50 per 1M). Claude costs more (Sonnet 5.5 $2 in / $10 out, Opus 5.5
-- $4 / $20 per 1M), so a Claude day was reported several times too cheap. The cost line needs the
-- Claude part of `tokens_used` apart, per MODEL and per KIND:
--
--   * per model: Claude's writer and judge are necessarily DIFFERENT models (llm_provider refuses a
--     judge that equals the writer), at different prices — two integer columns would force one price
--     on both;
--   * per kind: input and output tokens are priced 1:5, and cache reads / writes at their own rates.
--
-- So one nullable JSONB column, `claude_usage`:
--     {"<served model>": {"input": n, "output": n, "cache_read": n, "cache_write": n}, ...}
-- the Claude tokens INSIDE `tokens_used` (never in addition to it), summed over every generation of
-- the run like `tokens_used` is, failed Claude attempts included. NULL = no Claude token was billed:
-- every row written before this migration, and every row while the switches stay on gemini.
-- `tokens_used` keeps meaning "every billed token"; the digest prices `tokens_used` minus the Claude
-- tokens at the Gemini rate and `claude_usage` at Claude's list prices
-- (digest_service.CLAUDE_MICROS_PER_MILLION_TOKENS).
--
-- Written ONLY by app/services/marketing/script_service.py (`_claude_patch`), and only when a
-- generation billed Claude tokens — so with the switches on gemini the web writes exactly what it
-- wrote before. Read only by the digest (`run_service.script_tokens_between`).
--
-- Not `output`: that column is written only for an ACCEPTED package and is immutable, while a
-- rejected or failed generation bills too.
--
-- Safe on the live table: ADD COLUMN IF NOT EXISTS of a nullable column with no default is a
-- catalog-only change (no rewrite, no backfill). The CHECK is NAMED, dropped and re-added, so the
-- migration re-runs cleanly; COMMENT is re-runnable.
--
-- Grants and RLS: unchanged — 173's `GRANT ALL ON public.marketing_scripts TO service_role` covers a
-- new column, and its service-role-only policy is table-wide.
--
-- Deploy order: apply this BEFORE the web deploy that carries the change. The digest's token read
-- selects the column, so against a table without it that read fails with 42703 (undefined_column)
-- and the Monday cost line shows "Gemini unreadable" until it is applied — a floor, never a wrong
-- number. And never turn a MARKETING_*_PROVIDER switch to claude before it is applied: the first
-- Claude-billed generation would write the column, and its terminal write would fail.
--
-- Verify after applying:
--   SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name = 'marketing_scripts' AND column_name = 'claude_usage';
--                                   -- expect claude_usage | jsonb | YES | NULL
--   SELECT conname FROM pg_constraint WHERE conname = 'marketing_scripts_claude_usage_object';
--                                   -- expect one row
--   SELECT count(*) FROM public.marketing_scripts WHERE claude_usage IS NOT NULL;
--                                   -- expect 0 until a MARKETING_*_PROVIDER switch is set to claude

BEGIN;

ALTER TABLE public.marketing_scripts ADD COLUMN IF NOT EXISTS claude_usage JSONB;

ALTER TABLE public.marketing_scripts DROP CONSTRAINT IF EXISTS marketing_scripts_claude_usage_object;
ALTER TABLE public.marketing_scripts
    ADD CONSTRAINT marketing_scripts_claude_usage_object
    CHECK (claude_usage IS NULL OR jsonb_typeof(claude_usage) = 'object');

COMMENT ON COLUMN public.marketing_scripts.claude_usage IS 'The Claude tokens inside tokens_used (never in addition to it), by served model and kind: {"<model>": {"input", "output", "cache_read", "cache_write"}}, summed over every generation of the run, failed Claude attempts included (migration 206). NULL = no Claude token was billed (the MARKETING_*_PROVIDER switches on gemini, or a row older than 206). Written only by app/services/marketing/script_service.py, and only when a generation billed Claude; the weekly digest prices it at Claude list prices and tokens_used minus it at the Gemini rate.';

COMMIT;
