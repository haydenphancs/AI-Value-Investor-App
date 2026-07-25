-- 096_chat_usage_budget.sql
--
-- Why: the "Ask Cay AI" chat + streaming endpoints spend Gemini tokens on every
-- turn, and until now had NO per-user ceiling — a scripted caller could loop the
-- endpoint and run up an unbounded Gemini bill (OWASP LLM10, "denial of wallet").
-- This adds a DURABLE per-user daily budget: a turn is claimed atomically BEFORE
-- the model call; over the cap the endpoint returns 409 CHAT_DAILY_LIMIT_REACHED.
--
-- Mirrors the migration-088 `ai_insight_budget` template (atomic increment-and-cap
-- RPC, SECURITY DEFINER, service-role-only, REVOKE-before-GRANT on the RPCs). An
-- in-memory counter would evaporate on every Railway deploy, so the ceiling lives
-- in Postgres.
--
-- Schema: (user_id, budget_day) PK. `turn_count` gates generation; `token_count` is
-- best-effort spend observability + a soft secondary ceiling.
--
-- NOTE ON user_id: this table intentionally has NO foreign key to public.users.
-- The key is the chat abuse/cost BUCKET (a real user id, OR a per-INSTALL synthetic
-- guest id from guest_user_id_for()), which need not correspond to a users row. This
-- is the deliberate difference from chat_sessions.user_id (FK-bound; migration 047).

BEGIN;

CREATE TABLE IF NOT EXISTS chat_usage_budget (
    user_id     UUID    NOT NULL,
    budget_day  DATE    NOT NULL,
    turn_count  INTEGER NOT NULL DEFAULT 0,
    token_count BIGINT  NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, budget_day)
);

-- A per-day cleanup sweep deletes by budget_day; the PK already serves point lookups
-- on (user_id, budget_day).
CREATE INDEX IF NOT EXISTS idx_chat_usage_budget_day
    ON chat_usage_budget (budget_day);


-- Atomic claim-a-turn: increment turn_count for (user, day) and return the new count,
-- or -1 when the daily cap is already reached (or the limit is a kill-switch <= 0).
-- Doing this as a SELECT-then-UPDATE from Python races between Railway instances and
-- under-counts, letting spend drift past the ceiling. SECURITY DEFINER so the service
-- role can call it regardless of RLS; row_security = off since the caps are global.
CREATE OR REPLACE FUNCTION claim_chat_turn(
    p_user_id UUID,
    p_day     DATE,
    p_turn_limit INTEGER
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    -- Guard the INSERT path: the cap only constrains the DO UPDATE branch, so without
    -- this a limit of 0 (intended kill switch) or NULL (missing env var) would still
    -- grant the day's FIRST turn — the one case the operator most wants blocked.
    IF p_turn_limit IS NULL OR p_turn_limit <= 0 THEN
        RETURN -1;
    END IF;

    INSERT INTO chat_usage_budget (user_id, budget_day, turn_count, updated_at)
    VALUES (p_user_id, p_day, 1, NOW())
    ON CONFLICT (user_id, budget_day) DO UPDATE
        SET turn_count = chat_usage_budget.turn_count + 1,
            updated_at = NOW()
        WHERE chat_usage_budget.turn_count < p_turn_limit
    RETURNING turn_count INTO v_count;

    -- No row returned => the WHERE on DO UPDATE failed => the cap is reached.
    IF v_count IS NULL THEN
        RETURN -1;
    END IF;
    RETURN v_count;
END;
$$;


-- Best-effort token accumulation for spend observability (+ a soft secondary gate).
-- Upserts the day row so it works even if called before claim (it never is today).
CREATE OR REPLACE FUNCTION add_chat_tokens(
    p_user_id UUID,
    p_day     DATE,
    p_tokens  INTEGER
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_tokens BIGINT;
BEGIN
    IF p_tokens IS NULL OR p_tokens <= 0 THEN
        p_tokens := 0;
    END IF;

    INSERT INTO chat_usage_budget (user_id, budget_day, token_count, updated_at)
    VALUES (p_user_id, p_day, p_tokens, NOW())
    ON CONFLICT (user_id, budget_day) DO UPDATE
        SET token_count = chat_usage_budget.token_count + p_tokens,
            updated_at = NOW()
    RETURNING token_count INTO v_tokens;

    RETURN COALESCE(v_tokens, 0);
END;
$$;


-- ── RLS + grants ─────────────────────────────────────────────────────────────
-- Backend-internal working table: no anon/authenticated access at all (like
-- updates_insight_state / ai_insight_budget in migration 088).

ALTER TABLE chat_usage_budget ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "chat_usage_budget_service_all" ON chat_usage_budget;
CREATE POLICY "chat_usage_budget_service_all" ON chat_usage_budget
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT ALL ON chat_usage_budget TO service_role;

-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by Postgres default, and Supabase
-- exposes every public function at POST /rest/v1/rpc/<name>. Without these REVOKEs a
-- holder of the shipped anon key could loop claim_chat_turn to exhaust a victim's cap
-- (or inflate token_count), bypassing RLS by design (row_security = off). REVOKE must
-- precede GRANT; both are idempotent.
REVOKE ALL ON FUNCTION claim_chat_turn(UUID, DATE, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_chat_turn(UUID, DATE, INTEGER) TO service_role;

REVOKE ALL ON FUNCTION add_chat_tokens(UUID, DATE, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION add_chat_tokens(UUID, DATE, INTEGER) TO service_role;

COMMIT;
