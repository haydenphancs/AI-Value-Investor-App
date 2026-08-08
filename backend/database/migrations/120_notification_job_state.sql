-- 120_notification_job_state.sql
--
-- Why: the notification overhaul adds scheduled senders (earnings, insider, whale/
-- congress) that must run AT MOST ONCE PER ET TRADING DAY across an unknown number of
-- Railway instances. Nothing in the codebase provides that today:
--
--   * `notification_events`' dedup claim (119) prevents a duplicate BUZZ, but it does
--     not prevent two instances both walking a 200-ticker universe and spending
--     ~200 FMP calls each. The claim is the last line of defence, not the first.
--   * `updates_insight_state` (088) is per-SCOPE state for the 5-minute sweeper —
--     wrong grain, wrong lifecycle, and widening it would couple two schedulers.
--
-- Modelled directly on 088/089's `claim_updates_insight_scope`, including the two
-- non-obvious parts that migration learned the hard way:
--
--   1. IT MUST BE AN RPC. A read-then-write claim from the client has a textbook ABA
--      race (both instances read claim_at IS NULL, both write), and PostgREST cannot
--      express `runs_today = runs_today + 1` at all.
--   2. THE DAY KEY IS THE ET CALENDAR DATE, NOT UTC. 089 exists solely because the UTC
--      day rolls at 19:00 ET under EST — one hour INSIDE the sweep window — silently
--      granting every scope a second full daily allowance every winter evening. The
--      senders here run at 16:00 and 18:00 ET, i.e. squarely in that same danger zone,
--      so this function keys on America/New_York from day one.
--
-- `enabled` is an OPERATOR KILL SWITCH that needs no deploy: flip it in Supabase Studio
-- and `claim_notification_job` refuses the claim within seconds. A config env var would
-- cost a Railway restart, which is exactly the wrong tool when a sender is misbehaving
-- in front of users.
--
-- `last_cursor` is the whale sender's ingest high-water mark (`whale_trades.created_at`).
-- It advances ONLY on a successful pass, which is what makes that job idempotent across
-- restarts — a crashed pass re-reads the same window rather than skipping it.
--
-- No FK anywhere: this table is keyed on a job NAME, not a user.

BEGIN;

CREATE TABLE IF NOT EXISTS notification_job_state (
    job            TEXT        PRIMARY KEY,

    -- Operator kill switch. See header.
    enabled        BOOLEAN     NOT NULL DEFAULT TRUE,

    -- ET trading date of the last SUCCESSFUL run. A run that fails deliberately leaves
    -- this untouched so the next wake retries the same day.
    run_day        DATE,

    -- Non-NULL = an instance holds the claim. Cleared by finish_notification_job.
    claim_at       TIMESTAMPTZ,

    last_run_at    TIMESTAMPTZ,
    -- Ingest high-water mark (whale sender). NULL = no pass has ever succeeded.
    last_cursor    TIMESTAMPTZ,

    runs_today     INTEGER     NOT NULL DEFAULT 0,
    notified_today INTEGER     NOT NULL DEFAULT 0,
    last_error     TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT notification_job_state_counts_check
        CHECK (runs_today >= 0 AND notified_today >= 0)
);

COMMENT ON TABLE notification_job_state IS
    'Cross-instance claim + daily budget for the scheduled notification senders '
    '(earnings, smart_money, ...). One row per job name. `enabled` is a no-deploy kill '
    'switch. Claim via claim_notification_job(), release via finish_notification_job().';


-- ── Claim ────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION claim_notification_job(
    p_job           TEXT,
    p_now           TIMESTAMPTZ,
    p_stale_seconds INTEGER
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_ok BOOLEAN;
    -- ET trading date, NOT UTC. See the header comment and migration 089.
    -- p_now arrives as an ISO-8601 string with offset into a TIMESTAMPTZ parameter, so
    -- AT TIME ZONE converts a real instant — it does not reinterpret a naive local time.
    v_today DATE := (p_now AT TIME ZONE 'America/New_York')::date;
BEGIN
    INSERT INTO notification_job_state (job, updated_at)
    VALUES (p_job, p_now)
    ON CONFLICT (job) DO NOTHING;

    UPDATE notification_job_state s
       SET claim_at   = p_now,
           runs_today = CASE WHEN s.run_day IS DISTINCT FROM v_today
                             THEN 1 ELSE s.runs_today + 1 END,
           updated_at = p_now
     WHERE s.job = p_job
       AND s.enabled                              -- the kill switch
       AND s.run_day IS DISTINCT FROM v_today     -- at most one successful run per ET day
       -- `claim_at <= p_now - interval`, NOT `>=`. An instance running ahead of us
       -- writes a FUTURE claim_at; this predicate leaves that alone rather than
       -- stealing the claim out from under a run that is genuinely in flight.
       AND (s.claim_at IS NULL
            OR s.claim_at <= p_now - make_interval(secs => GREATEST(p_stale_seconds, 0)))
    RETURNING TRUE INTO v_ok;

    RETURN COALESCE(v_ok, FALSE);
END;
$$;


-- ── Release ──────────────────────────────────────────────────────────────────
--
-- p_success FALSE leaves `run_day` untouched so the next wake retries the same ET day —
-- that is how a transient FMP failure becomes a retry instead of a silently skipped day.
-- The claim is ALWAYS cleared either way; a failed pass must not park the row for the
-- full stale window.
--
-- p_cursor is only written when non-NULL AND the pass succeeded, so a failed pass can
-- never advance the whale high-water mark past rows it did not actually process.

CREATE OR REPLACE FUNCTION finish_notification_job(
    p_job      TEXT,
    p_now      TIMESTAMPTZ,
    p_success  BOOLEAN,
    p_notified INTEGER DEFAULT 0,
    p_cursor   TIMESTAMPTZ DEFAULT NULL,
    p_error    TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_today DATE := (p_now AT TIME ZONE 'America/New_York')::date;
BEGIN
    UPDATE notification_job_state s
       SET claim_at       = NULL,
           run_day        = CASE WHEN p_success THEN v_today ELSE s.run_day END,
           last_run_at    = p_now,
           last_cursor    = CASE WHEN p_success AND p_cursor IS NOT NULL
                                 THEN p_cursor ELSE s.last_cursor END,
           notified_today = CASE WHEN s.run_day IS DISTINCT FROM v_today
                                 THEN COALESCE(p_notified, 0)
                                 ELSE s.notified_today + COALESCE(p_notified, 0) END,
           last_error     = p_error,
           updated_at     = p_now
     WHERE s.job = p_job;
END;
$$;


-- ── Function grants ──────────────────────────────────────────────────────────
-- Mirrors 089. On a database where these are ABSENT (rebuild / restore / partial
-- replay) CREATE OR REPLACE *creates* them with the SECURITY DEFINER default of
-- EXECUTE TO PUBLIC, and Supabase exposes every public-schema function at
-- POST /rest/v1/rpc/<name> — so the shipped anon key could claim or release arbitrary
-- jobs with row_security off, freezing every scheduled sender.

REVOKE ALL ON FUNCTION claim_notification_job(TEXT, TIMESTAMPTZ, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_notification_job(TEXT, TIMESTAMPTZ, INTEGER) TO service_role;

REVOKE ALL ON FUNCTION finish_notification_job(
    TEXT, TIMESTAMPTZ, BOOLEAN, INTEGER, TIMESTAMPTZ, TEXT
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finish_notification_job(
    TEXT, TIMESTAMPTZ, BOOLEAN, INTEGER, TIMESTAMPTZ, TEXT
) TO service_role;


-- ── RLS + grants ─────────────────────────────────────────────────────────────

ALTER TABLE notification_job_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "notification_job_state_service_all" ON notification_job_state;
CREATE POLICY "notification_job_state_service_all" ON notification_job_state
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON notification_job_state FROM anon, authenticated;
GRANT ALL ON notification_job_state TO service_role;

COMMIT;
