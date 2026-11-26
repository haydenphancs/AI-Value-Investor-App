-- 210_marketing_run_lanes.sql
--
-- Why: owner decision 2026-11-15 — "Add a second daily post" on "Tue, Thu, Sat", "Full set: video +
-- picture", in its own morning slot. That post is a SECOND marketing run on the same ET day: the
-- SPOTLIGHT lane, beside the MAIN lane (the 16:00 ET run every day has had since migration 170).
-- Claims are rows, not clocks (.claude/rules/marketing.md §2): the worker claims a day by INSERTing its
-- marketing_runs row, and 170 made `run_date` UNIQUE, so a day could hold exactly ONE run. A second
-- claim on the same day would 23505 and adopt the first run. This migration makes the claim
-- (run_date, lane):
--
--   * marketing_runs.lane — 'main' or 'spotlight'. NOT NULL DEFAULT 'main', so every existing row is
--     the main lane, and so is every INSERT the deployed web makes (it never sends the column). The
--     new web sends `lane` only for a spotlight claim, so the main lane's INSERT stays byte for byte
--     what it is today. Written once, by the claiming INSERT; nothing updates it
--     (run_service.update_run has no parameter for it).
--   * UNIQUE (run_date, lane) replaces UNIQUE (run_date) (`marketing_runs_run_date_key` in
--     schema_snapshot.sql). The new key is ADDED FIRST; then a DO block drops every UNIQUE constraint
--     on the table whose definition is exactly `UNIQUE (run_date)`. It matches by DEFINITION, not by
--     name, so the drop works whatever Postgres named the inline constraint, and a re-run finds
--     nothing left to drop. The claim still detects a lost race by the 23505 CODE
--     (app/utils/supabase_errors.is_unique_violation), never by a constraint name, so the deployed
--     web's claim behaves exactly as before: its INSERT defaults to 'main' and conflicts on
--     (run_date, 'main') just as it conflicted on run_date.
--   * marketing_scripts.lane — the same column, default and CHECK. The script row stays keyed by
--     run_id (its PK); the lane rides beside it so the readers that take one script per DAY (the
--     digest's token read, the cross-lane exclusion) can tell a day's two scripts apart from one
--     table. The web derives it from the run row in ONE place (run_service.insert_script), never
--     from a separate literal; no constraint ties the two columns, so that code is the only writer.
--   * marketing_scripts.reject_reason gains 'nothing_qualified': a spotlight day whose chain finds no
--     record writes a `rejected` script with this reason and the run closes `skipped`. Never the
--     lesson, and never `rest_day`, so the day stays visible: the weekly digest lists it with its
--     selection trail. The nightly run-health check and the next day's final word stay QUIET on an
--     ordinary quiet day (every series simply had nothing), and ALERT, naming the trail, when a
--     series' data source timed out, was unavailable, errored or ran out of time, or when the trail
--     never reached the run (digest_service._quiet_nothing_qualified). To hear about every quiet
--     spotlight day too, remove 'nothing_qualified' from digest_service._QUIET_SKIPS (an open owner
--     question, 2026-11-15).
--
-- Idempotent: ADD COLUMN IF NOT EXISTS skips an existing column; every named constraint is
-- DROP CONSTRAINT IF EXISTS + ADD, so a re-run re-creates the same definitions; the DO block drops
-- only what is still there; COMMENT is re-runnable. One transaction: a failure part-way leaves the
-- table as it was, never without a claim key.
--
-- Safe on the live tables: both ADD COLUMNs carry a constant default, which is a catalog-only change
-- (no rewrite); the CHECKs validate against a few dozen rows that all hold 'main'; the new unique
-- index builds in milliseconds on ~60 rows. A run in flight feels only that brief lock. No grant,
-- policy, trigger, index or data change beyond the constraint swap: 170's / 173's service_role
-- GRANT ALL covers the new columns, and their service-role-only policies are table-wide.
--
-- Deploy order: apply BEFORE the web deploy that carries the spotlight lane. That web filters every
-- run read on `lane` (run_service.get_run_by_date), which fails 42703 (undefined_column) on a table
-- without it — no marketing day could run. Applying it first is safe for the web and worker deployed
-- today: their INSERT omits `lane` (→ 'main'), only main rows exist, their run_date read finds the
-- one main row, and the reject-reason CHECK is a superset of the one they write.
-- Never apply it between 16:00 ET and that day's media_ready (the main run's window), out of caution.
-- The worker may deploy before or after it: an older web answers the spotlight claim route 404, and
-- the worker then skips that lane with a WARNING.
--
-- Rollback: once a spotlight row exists, never roll the WEB back to a build from before the lanes
-- while a spotlight row is dated today or yesterday (ET). The old web reads a day's run by run_date
-- alone (`.eq('run_date').limit(1)`), so it can hand the SPOTLIGHT row to a MAIN claim: a spotlight
-- run that reads media_ready leaves the main day un-resumed; one that reads failed is re-claimed as
-- main and its posts are adopted under main keys (one lane's content lost, no error); and its token
-- read raises on a day with two scripts (the digest's cost line reads "unreadable"). To roll back:
-- set MARKETING_SPOTLIGHT_ENABLED=false, wait until no spotlight row is dated today or yesterday ET
-- (about two days), then roll back — or roll back only to another lane-aware build. Never drop
-- this migration's columns or key while a lane-aware web is deployed.
-- Even after that wait, the old web MISREPORTS the first two Monday digests whose window still
-- holds a spotlight row (its digest reads 14 days of scripts and 7 + 7 days of runs). Its runs
-- read stops at one row per day (`.limit(days + 1)`, written for UNIQUE (run_date)), so the
-- week's last days drop out — a posting day can read "no run" — and a spotlight row can stand
-- in for that day's main run (the week before's worker figure is cut the same way); its token
-- read raises on a day with two scripts, so the cost line reads "unreadable". Ignore those two
-- digests, or roll back only to another lane-aware build. Nothing is posted or lost.
--
-- Rolling the WORKER back: set MARKETING_SPOTLIGHT_ENABLED=false (web) FIRST. An older worker
-- never calls the spotlight claim route, so with the lane on no spotlight run is ever claimed:
-- every spotlight day reads "the worker never claimed it" in the nightly check (it points at the
-- cron, not the rollback), and a spotlight run in flight is abandoned until the sweep closes it.
--
-- Verify after applying (read-only):
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'public.marketing_runs'::regclass AND contype = 'u';
--       -- expect exactly ONE row: marketing_runs_run_date_lane_key | UNIQUE (run_date, lane)
--   SELECT table_name, column_default, is_nullable FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name IN ('marketing_runs', 'marketing_scripts')
--      AND column_name = 'lane';
--       -- expect two rows: 'main'::text | NO
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conname IN ('marketing_runs_lane_valid', 'marketing_scripts_lane_valid',
--                      'marketing_scripts_reject_reason_valid');
--       -- expect three rows; the reject-reason list ends in 'nothing_qualified'
--   SELECT lane, count(*) FROM public.marketing_runs GROUP BY lane;
--       -- expect 'main' only, until MARKETING_SPOTLIGHT_ENABLED is switched on
--
-- AFTER APPLYING: re-run scripts/dump_schema.sh and scripts/generate_schema_doc.py (no new table, so
-- no schema_curation.py entry).

BEGIN;

-- (a) marketing_runs.lane
ALTER TABLE public.marketing_runs ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'main';

ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS marketing_runs_lane_valid;
ALTER TABLE public.marketing_runs
    ADD CONSTRAINT marketing_runs_lane_valid CHECK (lane IN ('main', 'spotlight'));

-- (b) the claim key: (run_date, lane) first, then the old run_date-only key goes
ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS marketing_runs_run_date_lane_key;
ALTER TABLE public.marketing_runs
    ADD CONSTRAINT marketing_runs_run_date_lane_key UNIQUE (run_date, lane);

DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT conname
          FROM pg_constraint
         WHERE conrelid = 'public.marketing_runs'::regclass
           AND contype = 'u'
           AND pg_get_constraintdef(oid) = 'UNIQUE (run_date)'
    LOOP
        EXECUTE format('ALTER TABLE public.marketing_runs DROP CONSTRAINT %I', c.conname);
        RAISE NOTICE 'marketing_runs: dropped the run_date-only claim key %', c.conname;
    END LOOP;
END
$$;

-- (c) marketing_scripts.lane
ALTER TABLE public.marketing_scripts ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'main';

ALTER TABLE public.marketing_scripts DROP CONSTRAINT IF EXISTS marketing_scripts_lane_valid;
ALTER TABLE public.marketing_scripts
    ADD CONSTRAINT marketing_scripts_lane_valid CHECK (lane IN ('main', 'spotlight'));

-- (d) the spotlight's "nothing qualified" day (176's four reasons + one)
ALTER TABLE public.marketing_scripts DROP CONSTRAINT IF EXISTS marketing_scripts_reject_reason_valid;
ALTER TABLE public.marketing_scripts
    ADD CONSTRAINT marketing_scripts_reject_reason_valid
    CHECK (reject_reason IS NULL OR reject_reason IN
           ('content', 'writer_unavailable', 'empty_pool', 'source_ineligible', 'nothing_qualified'));

-- (e) comments
COMMENT ON TABLE public.marketing_runs IS
    'One row per (ET calendar day, lane) of the zero-touch marketing engine (SYSTEM_DESIGN_GUIDELINES '
    '§12). The media worker CLAIMS a day''s run by inserting this row (UNIQUE (run_date, lane), '
    'migration 210: the main lane every day, the spotlight lane on its own weekdays) and checkpoints '
    'its last completed stage here so a killed or skipped Railway cron slot resumes on the next hourly '
    'tick instead of starting over or double-producing. Written through the token-gated internal API '
    '(app/api/v1/endpoints/marketing_internal.py); the worker holds no Supabase key.';

COMMENT ON COLUMN public.marketing_runs.lane IS
    'Which daily run this row is (migration 210): main (the afternoon run every day has had since '
    'migration 170; the claiming INSERT never sends the column, so it defaults here) or spotlight (the '
    'second, morning run on MARKETING_SPOTLIGHT_WEEKDAYS, claimed through its own route). Written once '
    'by the claim and never updated.';

COMMENT ON COLUMN public.marketing_scripts.lane IS
    'The lane of the run this script belongs to (migration 210), copied from marketing_runs.lane by the '
    'selecting INSERT (run_service.insert_script, the only writer). Lets the readers that take one '
    'script per day tell the main and spotlight scripts of one day apart.';

COMMENT ON COLUMN public.marketing_scripts.reject_reason IS
    'Why a rejected row was closed: content (the validators, 4 times) | writer_unavailable (4 '
    'generations ended without a verdict) | empty_pool | source_ineligible | nothing_qualified (a '
    'spotlight day whose series chain found no record; the run closes skipped, never the lesson).';

COMMIT;
