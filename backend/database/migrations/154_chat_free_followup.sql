-- 154_chat_free_followup.sql
--
-- Why: chat is priced at a flat 1 credit per turn, and that price is deliberately
-- permanent (see the note at the bottom). The cost of that decision is "meter anxiety":
-- a user who has to spend a credit to ask "what does that mean?" learns to stop asking,
-- and chat is the retention loop for this product. This grants every CHARGED turn one
-- free follow-up in the same session, valid for a short window.
--
-- Why a column and not a counter table: the allowance is per SESSION and per TURN, and
-- it must survive a Railway restart and be correct with more than one instance — so an
-- in-memory dict is wrong on both counts, and `chat_usage_budget` is the wrong grain
-- (it is keyed per (identity, day), not per conversation). Hanging it off chat_sessions
-- also means it is cleaned up by the session's own delete; there is nothing to sweep.
--
-- Why BOTH sides are RPCs. The claim has to be one statement because it can double-spend:
-- two turns racing on one session could each read a live window and both go free, so the
-- read-and-clear is a single UPDATE ... RETURNING. The grant needs an RPC for a different
-- reason — the CLOCK. The claim compares against Postgres NOW(), so if the grant computed
-- its expiry from the API server's clock instead, any drift between Railway and Supabase
-- would silently lengthen or shorten every window. Both sides now read the same clock.
--
-- NOTE ON user_id: chat_sessions is guest-writable and has NO foreign key to
-- public.users (migration 111 dropped it so a signed-out caller can be partitioned per
-- install). Do not model this on an account-only table. Guests never reach the credit
-- branch at all — they are metered by the daily-turn budget — so the column simply stays
-- NULL for them and `claim_free_followup` returns FALSE.
--
-- Schema: chat_sessions.free_followup_until TIMESTAMPTZ NULL — the instant the current
-- allowance expires. NULL = no allowance (the default, and what a claim leaves behind).

BEGIN;

ALTER TABLE chat_sessions
    ADD COLUMN IF NOT EXISTS free_followup_until TIMESTAMPTZ;

COMMENT ON COLUMN chat_sessions.free_followup_until IS
    'When the session''s one free follow-up expires. Set after a CHARGED turn; cleared '
    'atomically by claim_free_followup(). NULL means no allowance. A free turn never '
    'sets it — that is what stops a free streak.';

-- Partial index: the only query is "is there a LIVE allowance for this session id", and
-- the vast majority of rows are NULL. A partial index keeps it small and skips them.
-- (Not a UNIQUE index — see migration 146: a partial UNIQUE index can never be a
-- PostgREST upsert target. This one is a plain lookup index and is never conflict-inferred.)
CREATE INDEX IF NOT EXISTS idx_chat_sessions_free_followup
    ON chat_sessions (id)
    WHERE free_followup_until IS NOT NULL;


-- Atomically consume the session's free follow-up, if one is live.
--
-- Returns TRUE  → this turn is FREE; the allowance has been consumed (set to NULL).
-- Returns FALSE → no live allowance; the caller must charge normally.
--
-- The UPDATE ... RETURNING is the whole point: claim and clear happen in one statement,
-- so two concurrent turns on the same session cannot both be told "free". The second
-- one's WHERE no longer matches, `v_claimed` stays NULL, and it pays.
--
-- Deliberately does NOT check `user_id`: the caller has already proven session ownership
-- (both chat endpoints 404 on a session that is not the caller's before reaching quota),
-- and the function is service-role-only, so adding the check would only duplicate a gate
-- that has already run.
CREATE OR REPLACE FUNCTION claim_free_followup(p_session_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_claimed BOOLEAN;
BEGIN
    IF p_session_id IS NULL THEN
        RETURN FALSE;
    END IF;

    UPDATE chat_sessions
       SET free_followup_until = NULL
     WHERE id = p_session_id
       AND free_followup_until IS NOT NULL
       AND free_followup_until > NOW()
    RETURNING TRUE INTO v_claimed;

    -- No row updated => no live allowance => NULL, not FALSE. Normalise it.
    RETURN COALESCE(v_claimed, FALSE);
END;
$$;

-- Open the session's free-follow-up window, starting NOW. Called after a turn that was
-- actually CHARGED; a free turn must never call this, or a user could ride one credit
-- indefinitely by always answering within the window.
--
-- `p_seconds <= 0` clears the allowance rather than opening a zero-length one, so setting
-- CHAT_FREE_FOLLOWUP_SECONDS to 0 is a true kill switch: existing windows go too, instead
-- of lingering until they expire on their own.
CREATE OR REPLACE FUNCTION grant_free_followup(p_session_id UUID, p_seconds INTEGER)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
BEGIN
    IF p_session_id IS NULL THEN
        RETURN;
    END IF;

    UPDATE chat_sessions
       SET free_followup_until = CASE
               WHEN COALESCE(p_seconds, 0) > 0
                   THEN NOW() + make_interval(secs => p_seconds)
               ELSE NULL
           END
     WHERE id = p_session_id;
END;
$$;

-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by Postgres default, and Supabase
-- exposes every public function at POST /rest/v1/rpc/<name>. Without these REVOKEs a
-- holder of the shipped anon key could not mint credits directly, but could burn any
-- session's allowance by id (a griefing vector) — and the function bypasses RLS by
-- design (row_security = off). REVOKE must precede GRANT; both are idempotent.
REVOKE ALL ON FUNCTION claim_free_followup(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_free_followup(UUID) TO service_role;

-- grant_free_followup is the one that HANDS OUT free turns. Reachable by the shipped anon
-- key it would let anyone open an unlimited free window on any session id they can guess.
REVOKE ALL ON FUNCTION grant_free_followup(UUID, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION grant_free_followup(UUID, INTEGER) TO service_role;

COMMIT;

-- ── Why chat stays 1 credit ─────────────────────────────────────────────────
-- Recorded here because this migration is the visible consequence of the decision.
--
-- Charging more for "harder" turns was considered and rejected: the expensive path
-- (`mode = 'synthesize'`) is chosen by an LLM classification, so the same question can
-- price differently on different days; and asking several questions in ONE message is
-- CHEAPER for us than three separate turns, so per-question pricing would push users
-- toward the costlier behaviour. Raising the price later is also blocked — credit packs
-- are consumables sold as "130 credits, never expire", and repricing what a credit buys
-- devalues an already-purchased one (App Store Guideline 3.1.1).
--
-- The variance is therefore bounded on the COST side instead: CHAT_MAX_SPECIALISTS caps
-- the synthesize fan-out, and this allowance is funded by that saving.
