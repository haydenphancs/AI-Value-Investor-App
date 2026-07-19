-- 087_chat_session_context_snapshot.sql
--
-- Why: the "Ask Cay AI" chat grounds a stock-detail question on the on-screen
-- snapshot iOS ships in SendChatMessageRequest.context. But the STOCK resolver
-- branch is a deliberate no-op (ChatContextResolver._resolve_stock → None), so
-- that snapshot is used transiently and never persisted. On a HISTORY REOPEN
-- iOS sends context=NULL, so the chat loses the exact on-screen data the user
-- saw (analyst targets, technicals, benchmark, the tab they were on) and
-- degrades to the stock_id summary enrichment only.
--
-- Migration 085 already persists context_type + reference_id on the session so
-- a reopen re-grounds — but ONLY for the branches that rebuild from the symbol
-- (ETF/CRYPTO/INDEX/TICKER_REPORT). STOCK (and COMMODITY, which appends to the
-- client_context) can't rebuild the tab-specific snapshot from the symbol.
--
-- Fix: persist the last on-screen snapshot on the SESSION. The endpoints write
-- request.context here (best-effort, guarded) and, on a turn where iOS sends
-- none, fall back to it (effective_context = request.context or context_snapshot)
-- so a reopened chat replays the exact snapshot the user last saw.
--   * Existing rows read NULL → no snapshot → identical to today's behavior.
--   * Older app builds keep working unchanged (column is additive + nullable).
--   * The stored value is the SEED-TIME snapshot (iOS freezes it), so a reopen
--     days later shows as-of-open numbers — matching the freshness the live
--     session already had; it restores exactness, not liveness.
--
-- No new index: chat_sessions is always fetched by id / user_id, never by this
-- column. RLS is already enabled on chat_sessions and its existing row policies
-- (user_id isolation) automatically cover the new column — no new policy/GRANT.
--
-- Idempotent: safe to apply multiple times.
--
-- ROLLOUT: apply this BEFORE deploying the code. The endpoints write the new
-- column best-effort (their own guarded try/except → logger.warning), so a code
-- deploy that races ahead of this migration cannot break a chat turn — it just
-- won't persist the snapshot until the column exists.

ALTER TABLE public.chat_sessions
    ADD COLUMN IF NOT EXISTS context_snapshot TEXT;

COMMENT ON COLUMN public.chat_sessions.context_snapshot IS
    'Last on-screen grounding snapshot iOS sent (SendChatMessageRequest.context), persisted so a history reopen (where iOS sends no context) can replay the exact data the user saw. Written best-effort by the chat endpoints; NULL for legacy/general chats and any turn that never carried a client snapshot.';
