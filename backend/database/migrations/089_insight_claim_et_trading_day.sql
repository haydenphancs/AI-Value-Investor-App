-- 089_insight_claim_et_trading_day.sql
--
-- Why: `claim_updates_insight_scope` (migration 088) keys the per-scope daily
-- regeneration budget on the UTC date:
--
--     v_today DATE := (p_now AT TIME ZONE 'UTC')::date;
--
-- The Updates insight sweeper runs 04:00-20:00 ET. Under EDT the UTC day rolls
-- at 20:00 ET — precisely when the sweeper stops — so the boundary fell in a
-- dead window and the bug was invisible all summer. Under EST (roughly
-- November-March) ET is UTC-5, so the UTC day rolls at 19:00 ET: one hour
-- INSIDE the sweep window.
--
-- Two consequences, both live only in winter:
--
--   1. The 19:00-20:00 ET after-hours hour is billed to the NEXT calendar day's
--      budget. That hour is the earnings window, so the generations most likely
--      to land there are exactly the ones that then pre-spend the next
--      morning's pre-market reserve (updates_materiality.premarket_cap_for) and
--      starve 04:00-09:30 — the window that reserve exists to protect.
--   2. Every scope silently receives a second full daily allowance at 19:00 ET,
--      because `regen_day IS DISTINCT FROM v_today` resets `regen_count_today`
--      to 0. A spend ceiling that resets mid-session is not a ceiling.
--
-- Fix: key on the America/New_York calendar date, which rolls at ET midnight —
-- far outside 04:00-20:00 — and is what "trading day" means anyway. This
-- function is the AUTHORITATIVE half of the cap (the Python gate in
-- updates_materiality._decide_inner is the advisory half); the two MUST agree
-- or they roll on different days for an hour every winter evening. The matching
-- Python change lands in the same commit.
--
-- `p_now` is passed as an ISO-8601 string with offset and the column is
-- TIMESTAMPTZ, so `AT TIME ZONE 'America/New_York'` converts a real instant —
-- it does not reinterpret a naive local time.
--
-- Rollout note: on the first sweep after this is applied, a state row whose
-- `regen_day` still holds a UTC date may compare unequal to the new ET date and
-- roll the counter once more than it should. That is a one-time, one-scope-wide
-- grant of a single extra allowance on the changeover day, bounded by the
-- durable global cap in `consume_updates_insight_budget`. No backfill needed.
--
-- Idempotent: CREATE OR REPLACE FUNCTION, no schema change, no data change.
-- Signature is byte-identical to 088 so no GRANT/REVOKE churn and no drop.
--
-- Apply between ET midnight and 19:00 ET and the changeover is a literal no-op
-- for existing rows: UTC and ET dates only differ after 19:00/20:00 ET.

BEGIN;

CREATE OR REPLACE FUNCTION claim_updates_insight_scope(
    p_scope TEXT,
    p_now TIMESTAMPTZ,
    p_stale_seconds INTEGER,
    p_attempt_cap INTEGER,
    p_daily_cap INTEGER
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_ok BOOLEAN;
    -- ET trading date, not UTC. See the header comment.
    v_today DATE := (p_now AT TIME ZONE 'America/New_York')::date;
BEGIN
    INSERT INTO updates_insight_state (scope, regen_day, updated_at)
    VALUES (p_scope, v_today, p_now)
    ON CONFLICT (scope) DO NOTHING;

    UPDATE updates_insight_state s
       SET regen_day = v_today,
           attempts_today    = CASE WHEN s.regen_day IS DISTINCT FROM v_today
                                    THEN 1 ELSE s.attempts_today + 1 END,
           regen_count_today = CASE WHEN s.regen_day IS DISTINCT FROM v_today
                                    THEN 0 ELSE s.regen_count_today END,
           claim_at = p_now,
           updated_at = p_now
     WHERE s.scope = p_scope
       AND (s.claim_at IS NULL
            OR s.claim_at <= p_now - make_interval(secs => p_stale_seconds))
       AND (s.regen_day IS DISTINCT FROM v_today
            OR (s.attempts_today < p_attempt_cap
                AND s.regen_count_today < p_daily_cap))
    RETURNING TRUE INTO v_ok;

    RETURN COALESCE(v_ok, FALSE);
END;
$$;

-- Mirrors 088. CREATE OR REPLACE preserves the existing ACL, so on the normal
-- path this is a no-op re-assertion. It matters when 089 runs against a database
-- where the function is ABSENT (a rebuild, a restore, a partial replay): there
-- CREATE OR REPLACE *creates* it with the SECURITY DEFINER default of
-- EXECUTE TO PUBLIC, and Supabase exposes every public-schema function at
-- POST /rest/v1/rpc/<name> — so the anon key could claim arbitrary scopes with
-- row_security off, burning attempts_today and freezing the Insights feature.
REVOKE ALL ON FUNCTION claim_updates_insight_scope(
    TEXT, TIMESTAMPTZ, INTEGER, INTEGER, INTEGER
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION claim_updates_insight_scope(
    TEXT, TIMESTAMPTZ, INTEGER, INTEGER, INTEGER
) TO service_role;

COMMIT;
