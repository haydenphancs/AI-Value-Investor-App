-- 111_chat_sessions_guest_partition.sql
--
-- Why: EVERY signed-out user currently shares ONE chat history. `chat_sessions.user_id` is
-- FK-bound to `public.users`, so a synthetic per-install uuid cannot be stored there and
-- `get_current_user_or_guest` resolves every guest to the single shared `GUEST_USER_ID`
-- sentinel. Both read paths filter on exactly that column:
--
--     GET /chat/sessions          .eq("user_id", user["id"])
--     GET /chat/sessions/{id}     .eq("user_id", user["id"])
--
-- So guest A asks Cay AI about their holdings, and guest B — a different person on a
-- different device — opens the chat history tab and sees A's conversations. B can open them,
-- rename them, and DELETE them.
--
-- This is the same defect migration 110 fixed for `research_reports` and 108 for
-- `watchlist_items`, and it is the worst remaining place for it: chat is where people paste
-- portfolios and ask personal financial questions. It was a known gap — `SYSTEM_DESIGN_GUIDELINES.md`
-- §9.3 records that guest chat-history isolation "resolves when real login ships". It didn't.
--
-- The blocker was this FK: a per-INSTALL id is a synthetic UUID5 with no `public.users` row,
-- so it cannot satisfy `chat_sessions_user_id_fkey`. Dropping it matches the deliberate no-FK
-- design of `user_learn_progress` / `user_book_progress` / `chat_usage_budget`, and the FKs
-- already dropped by 108 and 110.
--
-- ⚠️ CONSEQUENCE — ACCOUNT DELETION. The dropped constraint is ON DELETE CASCADE, and
-- `DELETE /users/me` RELIES on that cascade to remove a user's chats. Without a matching code
-- change, deleting an account would silently leave every conversation behind — the exact
-- incomplete-deletion bug fixed earlier in the launch prep, and a privacy-policy violation.
-- `chat_sessions` is added to `_UNLINKED_USER_TABLES` in `app/api/v1/endpoints/users.py` in the
-- SAME change. Do not apply this migration without that code deployed.
--
-- `chat_messages` needs no entry of its own: `chat_messages_session_id_fkey` references
-- `chat_sessions(id)` ON DELETE CASCADE, which is untouched here, so deleting a session still
-- deletes its messages.
--
-- ⚠️ EXISTING GUEST ROWS BECOME INVISIBLE ON DEPLOY. iOS sends `X-Guest-Id` on EVERY request
-- (APIClient.buildRequest), so after deploy each existing guest install resolves to its own new
-- partition and its chat history renders EMPTY. The legacy `GUEST_USER_ID` rows survive but
-- become unreachable by any client (RLS leaves them service-role-only).
--
-- Accepted deliberately, and preferable to the alternative: those rows are a SHARED bucket
-- whose contents belong to several different people, so they cannot be attributed to any one
-- install and must not be handed to whoever happens to ask first. There is no backfill and no
-- one-time claim. Clear them in Studio if you want the bucket empty.
--
-- ⚠️ NOT PRACTICALLY REVERSIBLE. Once guest rows exist, re-adding the FK fails validation until
-- every row whose user_id has no `public.users` match is deleted.
--
-- ⚠️ DO NOT REPLAY MIGRATION 047 AFTER THIS. `047_data_integrity_hardening.sql` §3 re-creates
-- `chat_sessions_user_id_fkey` inside an `IF NOT EXISTS` block and then VALIDATEs it, so a
-- replay would silently undo this migration — or, once guest rows exist, fail at VALIDATE.
-- Worse, 047's own apply-time note tells the operator to fix that by clearing "orphaned
-- user_id rows in chat_sessions / portfolios / watchlist_items", which after 108 and 111 is
-- real guest data, not orphans. 047 is superseded for `chat_sessions`, `portfolios` and
-- `watchlist_items`; it still stands for everything else it touches. (Migration 110 escaped
-- this because `research_reports` is not in 047.)
--
-- KNOWN, ACCEPTED: (a) no reaper — these rows are user-VISIBLE data, same argument as 108;
-- (b) the per-install daily-turn budget (`chat_usage_budget`, 096/097) still keys on the
-- client-supplied `X-Guest-Id`, so a caller rotating that header still resets their 60-turn
-- daily allowance. This migration fixes the PRIVACY leak, not that cost hole; a chat turn is
-- 1 credit against a report's 20, and the rate limit plus the Gemini circuit breaker still
-- bound it. Gating chat behind an account was the alternative and was deliberately not chosen.
--
-- Signing IN does migrate a guest's chats: `POST /users/me/claim-guest-data` gains
-- `chat_sessions` alongside watchlist / portfolios / learn progress / research reports.

BEGIN;

-- Idempotent: IF EXISTS so a re-run after a partial apply is a no-op.
ALTER TABLE chat_sessions
    DROP CONSTRAINT IF EXISTS chat_sessions_user_id_fkey;

COMMENT ON COLUMN chat_sessions.user_id IS
    'A real public.users id, OR a per-INSTALL synthetic uuid from guest_user_id_for() '
    'for signed-out users. NO foreign key by design (migration 111), same as watchlist_items '
    'and research_reports. Account deletion therefore clears this table EXPLICITLY via '
    '_UNLINKED_USER_TABLES, not via a cascade; chat_messages still cascades from chat_sessions.';

COMMIT;
