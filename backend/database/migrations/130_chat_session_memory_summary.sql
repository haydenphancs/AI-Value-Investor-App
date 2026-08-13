-- 130_chat_session_memory_summary.sql
--
-- Why: ChatService._condense_history keeps the last _RECENT_TURNS (6) messages
-- verbatim and rolls everything older into a flash-lite SUMMARY. That summary is
-- regenerated from scratch on EVERY turn once a chat passes 6 messages, even
-- though the older slice barely changes between consecutive turns — so a long
-- conversation pays for one extra LLM call per turn to re-derive almost exactly
-- the same 3-5 bullets. It is also a SERIAL hop on the time-to-first-token path:
-- the summary must come back before the answer prompt can be assembled.
--
-- Fix: persist the summary on the session and reuse it until the older slice has
-- actually moved on. `memory_summary_upto` is the created_at of the NEWEST message
-- included in the summary; the service counts how many messages in the current
-- older slice are newer than that watermark and only regenerates once that count
-- reaches CHAT_SUMMARY_REFRESH_AFTER_MESSAGES (4). Cost becomes one call per ~4
-- turns instead of one per turn.
--
-- A timestamp watermark rather than a message COUNT on purpose: the history window
-- is capped (_get_recent_messages limit=20), so a count saturates at the cap and
-- would never trigger a refresh again on a long chat. created_at is monotonic and
-- keeps working past the window.
--
-- What staleness this buys, precisely: only the SUMMARY of older turns can lag,
-- and by at most 3 messages. The most recent 6 messages are always passed verbatim,
-- so nothing the user just said can ever be stale.
--   * Existing rows read NULL → no stored summary → regenerate, i.e. today's behavior.
--   * Both columns are additive + nullable; no backfill.
--
-- No new index: chat_sessions is always fetched by id, never by these columns. RLS
-- is already enabled on chat_sessions and its existing user_id row policies cover
-- new columns automatically — no new policy or GRANT.
--
-- Idempotent: safe to apply multiple times.
--
-- ROLLOUT: safe in EITHER order. The service reads and writes these columns inside
-- their own guarded try/except (→ logger.warning), so code deployed ahead of this
-- migration simply regenerates the summary every turn exactly as it does today.

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS memory_summary TEXT;

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS memory_summary_upto TIMESTAMPTZ;

COMMENT ON COLUMN public.chat_sessions.memory_summary IS
    'Rolling flash-lite summary of the conversation turns older than the verbatim recent window, cached so it is not regenerated every turn. Written best-effort by ChatService._condense_history; NULL until a chat grows past _RECENT_TURNS.';

COMMENT ON COLUMN public.chat_sessions.memory_summary_upto IS
    'created_at of the newest chat_message included in memory_summary. The service regenerates the summary once CHAT_SUMMARY_REFRESH_AFTER_MESSAGES older-slice messages are newer than this watermark. NULL means no usable cached summary.';
