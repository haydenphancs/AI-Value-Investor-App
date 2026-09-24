-- 176_marketing_scripts_caps_and_run_date.sql
--
-- Why: migration 173 was applied in its first form (2026-09-23). The adversarial review of the
-- marketing engine that followed (SYSTEM_DESIGN_GUIDELINES §12.5, 2026-09-24) changed what the
-- kick-and-poll state machine needs from `public.marketing_scripts`, and 173 itself cannot carry
-- that: its `CREATE TABLE IF NOT EXISTS` is skipped on a table that already exists, so a re-run
-- adds nothing. This migration carries the delta. Three columns, one index swap:
--
--   * `run_date` — the run's ET day, copied from the run in the SAME first-write-wins INSERT
--     that selects the day's item. Selection's "recent picks" window reads it from this table.
--     It used to read the `source_ref` mirror on `marketing_runs`, which is written best-effort
--     after the INSERT, so one failed mirror write made the next posting day repeat an item.
--   * `content_rejections` — generations the validators REJECTED. With `generations` (every
--     generation started) it gives the two caps: 4 content rejections → `rejected` with reason
--     'content'; 4 generations that ended WITHOUT a verdict (generations - content_rejections:
--     a Gemini failure, a ledger blip, a crash, a cancellation, an owner that died) →
--     `rejected` with 'writer_unavailable'. One shared cap used to let a Gemini outage burn the
--     day's content attempts and report it to the worker as a compliance failure.
--   * `reject_reason` — why a `rejected` row was closed; the worker maps it to the run's
--     `metadata.skip_reason`. Only 'content' is a compliance verdict.
--   * the `(status, updated_at)` index served a stuck-generation sweep that was never built
--     (no query filters by status without run_id, the PK); the one non-PK read is "the last N
--     days before D", by run_date. The CHECKs are NAMED so a later reason can be added with a
--     DROP/ADD CONSTRAINT instead of hunting for Postgres's generated name.
--
-- Safe on the live table: marketing_scripts held 0 rows when this was written (the worker
-- service is not deployed yet), and `run_date` is still backfilled from the run before NOT NULL
-- in case a row appeared since. Every statement is idempotent: ADD COLUMN IF NOT EXISTS skips
-- the whole clause (its CHECK included) when the column exists, the backfill touches only NULLs,
-- SET NOT NULL and COMMENT are re-runnable, and the indexes use IF [NOT] EXISTS.
--
-- Grants and RLS: unchanged — 173's `GRANT ALL ON public.marketing_scripts TO service_role`
-- covers new columns, and its service-role-only policy is table-wide.
--
-- Deploy order: apply this BEFORE the web service that carries the hardened script service —
-- that code writes all three columns on every selection and every terminal write, so against
-- the 173-only table every kick fails with 42703 (undefined_column).
--
-- Verify after applying:
--   SELECT column_name, is_nullable, column_default FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name = 'marketing_scripts'
--      AND column_name IN ('run_date', 'content_rejections', 'reject_reason');
--                                   -- expect run_date NO, content_rejections NO / 0, reject_reason YES
--   SELECT indexname FROM pg_indexes WHERE tablename = 'marketing_scripts';
--                                   -- expect idx_marketing_scripts_run_date, no ..._status

BEGIN;

ALTER TABLE public.marketing_scripts ADD COLUMN IF NOT EXISTS run_date DATE;

UPDATE public.marketing_scripts AS s
   SET run_date = r.run_date
  FROM public.marketing_runs AS r
 WHERE s.run_id = r.id
   AND s.run_date IS NULL;

ALTER TABLE public.marketing_scripts ALTER COLUMN run_date SET NOT NULL;

ALTER TABLE public.marketing_scripts
    ADD COLUMN IF NOT EXISTS content_rejections INTEGER NOT NULL DEFAULT 0
        CONSTRAINT marketing_scripts_content_rejections_nonneg CHECK (content_rejections >= 0);

ALTER TABLE public.marketing_scripts
    ADD COLUMN IF NOT EXISTS reject_reason TEXT
        CONSTRAINT marketing_scripts_reject_reason_valid
        CHECK (reject_reason IS NULL OR reject_reason IN
               ('content', 'writer_unavailable', 'empty_pool', 'source_ineligible'));

-- Not data: an index nothing reads (see the header). Dropping it frees write amplification only.
DROP INDEX IF EXISTS public.idx_marketing_scripts_status;

CREATE INDEX IF NOT EXISTS idx_marketing_scripts_run_date
    ON public.marketing_scripts (run_date);

COMMENT ON TABLE public.marketing_scripts IS
    'One row per marketing run: the day''s frozen selection (run_date, source_ref, template_id) '
    'and the class-A writer''s output with the fact sheet it was grounded on. Written ONLY by the '
    'web side (kick-and-poll through the internal API; the worker never writes it). Writer output '
    'lives here and never in the public marketing-media bucket or in marketing_runs.metadata, '
    'whose key-by-key merge is not atomic and is echoed to the worker on every response. A '
    'generation holds the row through lease_until plus a fresh generation_id taken by one '
    'conditional UPDATE, and every terminal write is fenced on that generation_id, so a task '
    'that lost its lease writes nothing. accepted is terminal and its output immutable: the '
    'day''s posts are built from it. Two caps: 4 content rejections (reject_reason content) and '
    '4 generations ending without a verdict (writer_unavailable); a cap reached under an expired '
    'lease is closed by the next kick.';

COMMENT ON COLUMN public.marketing_scripts.run_date IS
    'The run''s ET day, written in the selecting INSERT. Selection''s "recent picks" window reads '
    'this table by it, never the best-effort mirror on marketing_runs.';
COMMENT ON COLUMN public.marketing_scripts.content_rejections IS
    'Generations the validators rejected (the content cap). generations - content_rejections is '
    'the writer-failure count, which has its own cap.';
COMMENT ON COLUMN public.marketing_scripts.reject_reason IS
    'Why a rejected row was closed: content (the validators, 4 times) | writer_unavailable (4 '
    'generations ended without a verdict) | empty_pool | source_ineligible.';
COMMENT ON COLUMN public.marketing_scripts.fact_sheet IS
    'The item''s cleaned fact sheet, for audit: written at selection and REWRITTEN with the '
    'accepted package. Each generation grounds against the LIVE bundle, so the accepted row '
    'records the sheet its output was actually validated against.';
COMMENT ON COLUMN public.marketing_scripts.violations IS
    'Why the latest generation was repaired or rejected: on rejection, every round of that '
    'generation, each entry tagged with its round; on acceptance, the surviving package''s '
    'dropped-outlet violations.';

COMMIT;
