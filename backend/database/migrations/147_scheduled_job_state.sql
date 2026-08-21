-- 147_scheduled_job_state.sql
--
-- Why: the daily whale full hydration (02:00 UTC) has no durable record that it ran. It
-- INFERS "today's run already happened" from the boot clock (app/main.py, the
-- `last_full_run_date = _boot.date() if _boot.hour >= 2` seed), so a redeploy or OOM at
-- 02:07 — mid-run — boots a process that skips the REST OF THE DAY. The un-swept whales
-- keep yesterday's data and nothing downstream can compensate, because
-- `_get_or_process_latest` prefers the stored snapshot whenever `last_hydrated_at` is set
-- and never rebuilds on age. It self-heals the next day, which is why this was previously
-- treated as a visibility problem rather than a correctness one.
--
-- Second, unrelated-looking but identical need: the latency measurement
-- (scripts/measure_whale_latency.sh) cannot tell "the sweep ran and wrote nothing"
-- from "the sweep never ran". Both are needed, and both are the same missing fact:
-- a durable per-run record carrying HOW MANY whales actually took the write path.
--
-- ⚠️ Why NOT `max(whales.last_hydrated_at)` as the marker: the 6-hourly politician branch
--    re-stamps that column, so it would read "ran today" every single day and suppress the
--    daily full run forever. It is also written ONLY when a whale's data CHANGED — it
--    means "last CHANGED", not "last checked". Verified in production 2026-08-20: the only
--    stamps that day were 5 congressional_house rows at 17:00, from the politician sweep.
--
-- What this does:
--   1. Adds `items_written` to notification_job_state — a generic "rows this job actually
--      wrote on its last run", which is the signal the measurement needs.
--   2. Adds claim_scheduled_job() / finish_scheduled_job(), which are the existing
--      claim_notification_job() / finish_notification_job() with the day-boundary timezone
--      PARAMETERISED instead of hardcoded to America/New_York.
--   3. Seeds the `whale_hydration_full` row.
--
-- ⚠️ Why NEW functions rather than adding a parameter to the existing pair: the existing
--    ones are on the live notification path (earnings / smart_money / profile_match). A
--    DEFAULTed 4th argument would make `claim_notification_job(text,timestamptz,integer)`
--    ambiguous against the 3-arg form and break every current call with "function is not
--    unique"; and dropping the 3-arg form to replace it discards its GRANTs and re-owns
--    the function. The notification path is therefore left byte-identical and untouched.
--    New callers use the *_scheduled_job pair; notification jobs can migrate later.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE FUNCTION, ON CONFLICT DO NOTHING.

BEGIN;

-- 1. Generic write-volume counter -------------------------------------------------
ALTER TABLE public.notification_job_state
    ADD COLUMN IF NOT EXISTS items_written INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.notification_job_state.items_written IS
    'Rows the job actually WROTE on its last run (0 = it ran but had nothing to do). '
    'Distinct from notified_today, which counts notifications delivered and accumulates '
    'across a day. Lets a consumer tell "ran and wrote nothing" from "never ran" — the '
    'whale latency measurement depends on exactly that distinction.';

COMMENT ON TABLE public.notification_job_state IS
    'Cross-instance claim + daily budget for scheduled background jobs. Originally the '
    'notification senders (earnings, smart_money, profile_match), now also non-notification '
    'jobs such as whale_hydration_full. One row per job name. `enabled` is a no-deploy kill '
    'switch. Claim via claim_notification_job() (ET day boundary) or claim_scheduled_job() '
    '(caller-chosen timezone); release via the matching finish_*() function.';

-- 2. Timezone-parameterised claim/finish ------------------------------------------
CREATE OR REPLACE FUNCTION claim_scheduled_job(
    p_job           TEXT,
    p_now           TIMESTAMPTZ,
    p_stale_seconds INTEGER,
    p_timezone      TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_ok BOOLEAN;
    v_today DATE := (p_now AT TIME ZONE p_timezone)::date;
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
       AND s.run_day IS DISTINCT FROM v_today     -- at most one successful run per day
       AND (s.claim_at IS NULL
            OR s.claim_at <= p_now - make_interval(secs => GREATEST(p_stale_seconds, 0)))
    RETURNING TRUE INTO v_ok;

    RETURN COALESCE(v_ok, FALSE);
END;
$$;

CREATE OR REPLACE FUNCTION finish_scheduled_job(
    p_job      TEXT,
    p_now      TIMESTAMPTZ,
    p_success  BOOLEAN,
    p_items    INTEGER DEFAULT 0,
    p_error    TEXT DEFAULT NULL,
    p_timezone TEXT DEFAULT 'UTC'
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_today DATE := (p_now AT TIME ZONE p_timezone)::date;
BEGIN
    UPDATE notification_job_state s
       SET claim_at      = NULL,
           -- run_day advances ONLY on success, so a failed pass is retried on the next
           -- wake instead of being recorded as this day's run.
           run_day       = CASE WHEN p_success THEN v_today ELSE s.run_day END,
           last_run_at   = p_now,
           -- Overwritten, not accumulated: this is "what the LAST run wrote", which is
           -- what a consumer polling mid-window needs to read.
           items_written = COALESCE(p_items, 0),
           last_error    = p_error,
           updated_at    = p_now
     WHERE s.job = p_job;
END;
$$;

REVOKE ALL ON FUNCTION claim_scheduled_job(TEXT, TIMESTAMPTZ, INTEGER, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_scheduled_job(TEXT, TIMESTAMPTZ, INTEGER, TEXT) TO service_role;

REVOKE ALL ON FUNCTION finish_scheduled_job(
    TEXT, TIMESTAMPTZ, BOOLEAN, INTEGER, TEXT, TEXT
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION finish_scheduled_job(
    TEXT, TIMESTAMPTZ, BOOLEAN, INTEGER, TEXT, TEXT
) TO service_role;

-- 3. Seed the whale hydration row --------------------------------------------------
-- Seeded with run_day NULL so the first sweep after deploy is allowed to run.
INSERT INTO public.notification_job_state (job, enabled, updated_at)
VALUES ('whale_hydration_full', TRUE, NOW())
ON CONFLICT (job) DO NOTHING;

COMMIT;
