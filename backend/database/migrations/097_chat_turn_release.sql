-- 097_chat_turn_release.sql
--
-- Why: migration 096 claims a daily chat turn (increments turn_count) BEFORE the Gemini call so
-- concurrent turns can't both slip past the cap (pre-charge, like credits). But a claimed turn was
-- never released when generation FAILED — a Gemini outage (quota/timeout/500) would burn a user's
-- whole daily allowance on requests that produced no answer, and a stream-fail + client fallback
-- could double-count one user action. This adds a release (refund) RPC so the endpoints can hand a
-- turn back on the failure path (best-effort; mirrors credit_service.refund).
--
-- release_chat_turn decrements turn_count with a floor of 0 (GREATEST) so a stray double-release
-- can never drive the count negative and silently grant extra turns. It does NOT touch token_count.

BEGIN;

CREATE OR REPLACE FUNCTION release_chat_turn(
    p_user_id UUID,
    p_day     DATE
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
    UPDATE chat_usage_budget
       SET turn_count = GREATEST(0, turn_count - 1),
           updated_at = NOW()
     WHERE user_id = p_user_id
       AND budget_day = p_day
    RETURNING turn_count INTO v_count;

    -- No row (never claimed today) => nothing to release.
    RETURN COALESCE(v_count, 0);
END;
$$;

-- Same lock-down as 096: SECURITY DEFINER functions are EXECUTE-to-PUBLIC by default and exposed at
-- POST /rest/v1/rpc/<name>; without the REVOKE a holder of the anon key could loop release_chat_turn
-- to hand themselves unlimited turns. REVOKE precedes GRANT; both idempotent.
REVOKE ALL ON FUNCTION release_chat_turn(UUID, DATE) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION release_chat_turn(UUID, DATE) TO service_role;

COMMIT;
