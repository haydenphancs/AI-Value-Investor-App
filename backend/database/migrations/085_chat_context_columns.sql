-- 085_chat_context_columns.sql
--
-- Why: the "Ask Cay AI" chat is becoming context-aware. iOS now sends
-- {context_type, reference_id} instead of a big client-built context string
-- (e.g. TickerReportView used to ship the first 800 chars of the executive
-- summary on every new chat). The backend's ChatContextResolver fetches the
-- already-cached data for that screen and injects a compact grounding block.
--
-- Persisting context_type + reference_id on the SESSION (not just the message)
-- lets a history reload re-ground the AI on the same cached source without the
-- client re-sending anything. Both columns are nullable and additive:
--   * Existing rows read NULL → the resolver treats that as "no context"
--     (or falls back to stock_id, which is kept for back-compat).
--   * Older app builds that don't send the fields keep working unchanged.
--
-- No new index: chat_sessions is always fetched by id / user_id, never by
-- context_type. RLS is already enabled on chat_sessions and its existing row
-- policies (user_id isolation) automatically cover the new columns, so no new
-- policy or GRANT is required.
--
-- Idempotent: safe to apply multiple times.

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS context_type TEXT;

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS reference_id TEXT;

COMMENT ON COLUMN public.chat_sessions.context_type IS
    'Screen the chat is grounded on (ChatContextType): TICKER_REPORT, STOCK, ETF, CRYPTO, INDEX, COMMODITY, MONEY_MOVES_ARTICLE, JOURNEY_LESSON, BOOK, NONE. NULL for legacy/general chats.';

COMMENT ON COLUMN public.chat_sessions.reference_id IS
    'Identifier the ChatContextResolver fetches by: ticker, "TICKER|persona" (reports), article slug, book curriculum order, etc. NULL when context_type is NONE/absent.';
