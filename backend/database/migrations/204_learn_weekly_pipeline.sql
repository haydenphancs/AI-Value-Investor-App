-- 204_learn_weekly_pipeline.sql
--
-- Why: the owner approved (2026-10-22) the Wiser weekly content engine — a Friday "Feature Deep
-- Dive" (and, later, a category piece on a fixed weekday) written from SEC filings and a
-- human-verified case library, narrated in the Learn voice, approved in Telegram, and published
-- into Money Moves with no app release. Money Moves today is 23 hand-made articles seeded by
-- hand; nothing about it is automated, so the section goes stale. The plan is
-- ~/.claude/plans/improve-or-add-wiser-snoopy-sun.md §1.2; the code is
-- backend/app/services/learn_weekly/ (the ledger: ledger.py; the shared vocabulary:
-- contracts.py, whose PIECE_STATUSES / JOB_STATUSES / CATEGORIES / ENGINES the CHECK lists below
-- must equal — pinned by tests/test_learn_weekly_ledger.py).
--
-- Parts:
--   A. learn_weekly_pieces  — one row per PERIOD (`2026-W43:deep_dive`, `2026-10-26:slot`). The
--      UNIQUE period_key is the week-level exactly-once claim (147's lesson: claims are rows,
--      not clocks). A draft lives ONLY here until the owner approves it — it reaches
--      money_move_articles (and so the app) only at publish. Fencing is copied from
--      marketing_scripts (173): a builder holds the row through lease_until + a fresh
--      generation_id taken by ONE conditional UPDATE, and every later write of that generation
--      is conditional on it, so a redeploy's overlapping containers can never both write.
--      dry_run is frozen at creation: a dry-run piece ends `rehearsed` and can never reach
--      `publishing` / `published` (CHECK below, mirrored by the ledger's own fence).
--   B. learn_narration_jobs — the narrator worker's queue (a Railway cron service holding NO
--      Supabase key; it reaches this table only through the token-gated internal API). One row
--      per (piece, round): "Retry narration" opens a new round, never rewrites an old one. The
--      engine and voice are frozen into the job — the worker's environment never decides them.
--      A claim bumps `attempts` and mints a fresh `claim_nonce`; every worker write is fenced
--      on both, so a zombie worker whose lease was re-claimed writes nothing. Worker-reported
--      failures and lease re-claims are capped SEPARATELY (contracts.MAX_JOB_WORKER_FAILURES /
--      MAX_JOB_LEASE_RECLAIMS): an infra kill of a two-hour render must not burn the same budget
--      as a real synthesis failure.
--   C. money_move_articles  —
--      * `source` ('bundle' | 'weekly'): which rows the pipeline owns. The seeder and the
--        bundled JSON stay 'bundle'; a published weekly piece is 'weekly'.
--      * a UNIQUE index on lower(title): iOS keys cards, related tiles and the audio refresh by
--        TITLE, so two rows with one title would collide on device. The bundle holds 0
--        duplicates today; a DO block refuses (and rolls the whole migration back) with the
--        offending titles if production holds any, instead of a bare 23505 from the index.
--      * ⚠️ the anon / authenticated read door is CLOSED. 065 granted SELECT to both and created
--        the `money_moves_select_all` policy (USING true) in Studio. The iOS app has no Supabase
--        client — every reader is the backend's service_role — so that grant was an unused
--        second door, and from this migration on the table will hold AI-assisted weekly pieces
--        the owner can unpublish: an unpublished row must not stay readable through the anon key
--        that ships in the binary. `money_moves_service_all` is recreated in the 162 template
--        (FOR ALL TO service_role) — the Studio original was a PUBLIC policy gated on
--        auth.role(), which is the shape every other service-role-only table has left.
--      `lessons` (Investor Journey) keeps its client grant for now — an open owner decision.
--   D. notification_job_state rows for the pipeline's three day-claimed jobs (their `enabled`
--      = the no-deploy kill switch): learn_weekly_topic (Thursday topic card),
--      learn_weekly_build (the build of every due period), learn_weekly_deadman (Friday
--      "no Deep Dive in review yet" alert).
--
-- Every new table is service_role only (the 162 template): the iOS app never reads them.
--
-- ⚠️ Apply BEFORE deploying the backend that reads them. Nothing reads them while
-- LEARN_WEEKLY_ENABLED is False (the default), so the order only matters once it is turned on.
--
-- Idempotent: IF NOT EXISTS on tables, columns and indexes (an inline CHECK rides its ADD COLUMN,
-- so a replay is a no-op); DROP POLICY IF EXISTS before CREATE; REVOKE / GRANT are declarative;
-- the job rows are ON CONFLICT DO NOTHING. Safe to re-run. One transaction: a refused duplicate
-- title leaves nothing half-applied.

BEGIN;

-- ── A. learn_weekly_pieces ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.learn_weekly_pieces (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The period this piece is FOR. UNIQUE = the week-level exactly-once claim.
    period_key            TEXT        NOT NULL UNIQUE,
    kind                  TEXT        NOT NULL,
    -- Money Moves category (the `money_move_category` enum values). NULL until chosen.
    category              TEXT,
    scheduled_publish_at  TIMESTAMPTZ NOT NULL,
    -- After this instant an unapproved piece expires and Approve answers "too late".
    hold_until            TIMESTAMPTZ NOT NULL,
    -- Frozen at creation (LEARN_WEEKLY_DRY_RUN at build time). Defaults TRUE: fail closed.
    dry_run               BOOLEAN     NOT NULL DEFAULT TRUE,
    status                TEXT        NOT NULL DEFAULT 'planned',

    -- Fencing (marketing_scripts, 173).
    generation_id         UUID,
    lease_until           TIMESTAMPTZ,
    generations           INTEGER     NOT NULL DEFAULT 0,
    content_rejections    INTEGER     NOT NULL DEFAULT 0,
    writer_failures       INTEGER     NOT NULL DEFAULT 0,
    -- Failed narration-ENQUEUE attempts of the current draft (reset by each new draft; capped by
    -- build.py), durable rather than an in-memory count.
    build_attempts        INTEGER     NOT NULL DEFAULT 0,
    -- Finished sections, so a deploy mid-build resumes instead of re-paying for them.
    progress              JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- Content.
    topic                 JSONB,
    subjects              JSONB       NOT NULL DEFAULT '[]'::jsonb,
    fact_sheet            JSONB,
    fact_sheet_sha256     TEXT,
    slug                  TEXT        UNIQUE,
    title                 TEXT,
    draft                 JSONB,
    draft_sha256          TEXT,
    validation            JSONB,
    tokens_used           INTEGER     NOT NULL DEFAULT 0,

    -- Review and publish.
    narration_job_id      UUID,
    review_round          INTEGER     NOT NULL DEFAULT 0,
    review_message_ids    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    reviewed_by           TEXT,
    reviewed_at           TIMESTAMPTZ,
    reject_reason         TEXT,
    publish_lease_until   TIMESTAMPTZ,
    published_at          TIMESTAMPTZ,
    unpublished_at        TIMESTAMPTZ,
    last_error            TEXT,
    last_stage            TEXT,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT learn_weekly_pieces_kind_check
        CHECK (kind IN ('deep_dive', 'slot')),
    -- The key's suffix must name the row's own kind. A Deep Dive is keyed by the ISO week it
    -- publishes in, a slot piece by its ET publish date (contracts.deep_dive_period_key /
    -- slot_period_key). The ledger validates the calendar itself (a real ISO week, a real date);
    -- this is the coarse backstop.
    CONSTRAINT learn_weekly_pieces_period_key_check
        CHECK ((kind = 'deep_dive'
                AND period_key ~ '^[0-9]{4}-W(0[1-9]|[1-4][0-9]|5[0-3]):deep_dive$')
            OR (kind = 'slot'
                AND period_key ~ '^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01]):slot$')),
    CONSTRAINT learn_weekly_pieces_category_check
        CHECK (category IS NULL OR category IN ('blueprints', 'valueTraps', 'battles')),
    CONSTRAINT learn_weekly_pieces_status_check
        CHECK (status IN ('planned', 'topic_pending', 'building', 'drafted', 'media_pending',
                          'ready_for_review', 'in_review', 'approved', 'publishing', 'published',
                          'rehearsed', 'skipped', 'failed', 'owner_rejected', 'expired',
                          'unpublished')),
    CONSTRAINT learn_weekly_pieces_hold_after_publish
        CHECK (hold_until >= scheduled_publish_at),
    -- A dry-run piece can never be live. The ledger fences every write into these two
    -- statuses on dry_run = false as well; this is the database's own refusal.
    CONSTRAINT learn_weekly_pieces_dry_run_never_publishes
        CHECK (NOT (dry_run AND status IN ('publishing', 'published'))),
    CONSTRAINT learn_weekly_pieces_counts_check
        CHECK (generations >= 0 AND content_rejections >= 0 AND writer_failures >= 0
               AND build_attempts >= 0 AND tokens_used >= 0 AND review_round >= 0),
    CONSTRAINT learn_weekly_pieces_json_shapes
        CHECK (jsonb_typeof(progress) = 'object'
               AND jsonb_typeof(subjects) = 'array'
               AND jsonb_typeof(review_message_ids) = 'array'
               AND (topic IS NULL OR jsonb_typeof(topic) = 'object')
               AND (fact_sheet IS NULL OR jsonb_typeof(fact_sheet) = 'object')
               AND (draft IS NULL OR jsonb_typeof(draft) = 'object')
               AND (validation IS NULL OR jsonb_typeof(validation) = 'object')),
    CONSTRAINT learn_weekly_pieces_sha_check
        CHECK ((fact_sheet_sha256 IS NULL OR fact_sheet_sha256 ~ '^[0-9a-f]{64}$')
               AND (draft_sha256 IS NULL OR draft_sha256 ~ '^[0-9a-f]{64}$')),
    -- `<kebab-title>-<yyyy-mm-dd>` (plan §1.7); the same shape as the bundle's slugs.
    CONSTRAINT learn_weekly_pieces_slug_check
        CHECK (slug IS NULL OR (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(slug) <= 160)),
    CONSTRAINT learn_weekly_pieces_text_caps
        CHECK ((title IS NULL OR char_length(title) BETWEEN 1 AND 300)
               AND (reviewed_by IS NULL OR char_length(reviewed_by) <= 200)
               AND (reject_reason IS NULL OR char_length(reject_reason) <= 2000)
               AND (last_error IS NULL OR char_length(last_error) <= 2000)
               AND (last_stage IS NULL OR char_length(last_stage) <= 100))
);

-- The every-tick sweeps (review send, publish, expiry) read by status, oldest due first.
CREATE INDEX IF NOT EXISTS idx_learn_weekly_pieces_status_due
    ON public.learn_weekly_pieces (status, scheduled_publish_at);
-- The no-repeat windows and the cache-sync stamp read the newest published pieces of a kind.
CREATE INDEX IF NOT EXISTS idx_learn_weekly_pieces_kind_published
    ON public.learn_weekly_pieces (kind, published_at DESC);

COMMENT ON TABLE public.learn_weekly_pieces IS
    'Wiser weekly content engine (migration 204): one row per period (2026-W43:deep_dive | '
    '2026-10-26:slot) — the week-level exactly-once claim and the piece''s whole life: topic, '
    'SEC/case-library fact sheet, the draft (iOS shape) while it is reviewed, review state and '
    'publish record. A draft reaches money_move_articles only after the owner approves it in '
    'Telegram. A builder holds the row through lease_until + a fresh generation_id (one '
    'conditional UPDATE) and every write is fenced on it (the marketing_scripts model). '
    'dry_run is frozen at creation and a dry-run piece can never be publishing/published. '
    'Written only by app/services/learn_weekly/ledger.py; service_role only.';

COMMENT ON COLUMN public.learn_weekly_pieces.period_key IS
    'The period the piece is for: <ISO year>-W<ISO week>:deep_dive (the week it publishes in) or '
    '<ET publish date>:slot. UNIQUE: a second create for the same period adopts the first row.';
COMMENT ON COLUMN public.learn_weekly_pieces.status IS
    'planned → topic_pending → building → drafted → media_pending → ready_for_review → in_review '
    '→ approved → publishing → published (→ unpublished); a dry run ends rehearsed. Other ends: '
    'skipped, failed, owner_rejected, expired. Every change is a compare-and-swap on the observed status.';
COMMENT ON COLUMN public.learn_weekly_pieces.dry_run IS
    'Frozen at creation from LEARN_WEEKLY_DRY_RUN. TRUE = the piece ends rehearsed; it can never '
    'be publishing or published (CHECK).';
COMMENT ON COLUMN public.learn_weekly_pieces.generation_id IS
    'Fencing token. Set by the conditional UPDATE that acquires the build lease; every later write '
    'of that generation is conditional on it, so a superseded generation cannot land.';
COMMENT ON COLUMN public.learn_weekly_pieces.hold_until IS
    'End of the review hold. An unapproved piece expires after it, and Approve is refused at or '
    'after it. Never earlier than scheduled_publish_at (CHECK).';
COMMENT ON COLUMN public.learn_weekly_pieces.subjects IS
    'The piece''s subjects, a JSON array of {name, cik, ticker} (contracts.Subject) — read with '
    'fact_sheet.theme_slug by the no-repeat windows (theme 4 weeks, company 12 weeks).';
COMMENT ON COLUMN public.learn_weekly_pieces.narration_job_id IS
    'Soft pointer to the narration job the review bundle carries (no FK: the job''s piece_id FK '
    'is the enforced edge, and a circular FK would make re-applying this migration non-idempotent).';
COMMENT ON COLUMN public.learn_weekly_pieces.topic IS
    'The topic decision: the top 3 with their scores, the owner''s pick and the peg facts with '
    'their SEC URLs. FMP-derived numbers in it are nulled by the FMP-termination purge statement.';

-- ── B. learn_narration_jobs ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.learn_narration_jobs (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    piece_id          UUID        NOT NULL REFERENCES public.learn_weekly_pieces (id) ON DELETE CASCADE,
    round             INTEGER     NOT NULL,
    -- The server's narration blocks (the worker sees only i / text / role).
    script            JSONB       NOT NULL,
    script_sha256     TEXT        NOT NULL,
    -- Frozen per job: the worker's environment never decides the engine or the voice.
    engine            TEXT        NOT NULL,
    voice             TEXT        NOT NULL,
    pipeline_version  TEXT        NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'queued',
    -- Claims ever made; every claim bumps it and mints a fresh claim_nonce.
    attempts          INTEGER     NOT NULL DEFAULT 0,
    worker_failures   INTEGER     NOT NULL DEFAULT 0,
    lease_reclaims    INTEGER     NOT NULL DEFAULT 0,
    claim_nonce       TEXT,
    claimed_at        TIMESTAMPTZ,
    heartbeat_at      TIMESTAMPTZ,
    lease_until       TIMESTAMPTZ,
    -- Results.
    audio_path        TEXT,
    audio_bytes       BIGINT,
    duration_seconds  DOUBLE PRECISION,
    timings           JSONB,
    checks            JSONB,
    error             TEXT,
    finished_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One job per (piece, round): enqueue is first-write-wins on this key.
    CONSTRAINT learn_narration_jobs_piece_round UNIQUE (piece_id, round),
    CONSTRAINT learn_narration_jobs_round_check CHECK (round >= 1),
    CONSTRAINT learn_narration_jobs_status_check
        CHECK (status IN ('queued', 'in_progress', 'ready', 'failed', 'cancelled')),
    CONSTRAINT learn_narration_jobs_engine_check
        CHECK (engine IN ('chatterbox', 'kokoro')),
    CONSTRAINT learn_narration_jobs_counts_check
        CHECK (attempts >= 0 AND worker_failures >= 0 AND lease_reclaims >= 0),
    CONSTRAINT learn_narration_jobs_script_check
        CHECK (jsonb_typeof(script) IN ('array', 'object') AND script_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT learn_narration_jobs_text_caps
        CHECK (char_length(voice) BETWEEN 1 AND 80
               AND char_length(pipeline_version) BETWEEN 1 AND 80
               AND (claim_nonce IS NULL OR claim_nonce ~ '^[0-9a-f]{16}$')
               AND (error IS NULL OR char_length(error) <= 2000)),
    -- A claimed job always carries the lease the re-claim sweep reads: an in_progress row with a
    -- NULL lease would match no `lease_until < now` filter and sit claimed forever.
    CONSTRAINT learn_narration_jobs_claimed_has_lease
        CHECK (status <> 'in_progress' OR (lease_until IS NOT NULL AND claim_nonce IS NOT NULL)),
    -- A ready job always carries its audio.
    CONSTRAINT learn_narration_jobs_ready_has_audio
        CHECK (status <> 'ready' OR (audio_path IS NOT NULL AND audio_bytes IS NOT NULL
                                     AND duration_seconds IS NOT NULL)),
    CONSTRAINT learn_narration_jobs_result_ranges
        CHECK ((audio_bytes IS NULL OR audio_bytes > 0)
               AND (duration_seconds IS NULL OR duration_seconds > 0))
);

-- The claim reads queued jobs and in_progress jobs whose lease ran out.
CREATE INDEX IF NOT EXISTS idx_learn_narration_jobs_status_lease
    ON public.learn_narration_jobs (status, lease_until);

COMMENT ON TABLE public.learn_narration_jobs IS
    'Wiser weekly content engine (migration 204): the narrator worker''s queue, one job per '
    '(piece, round) — Retry narration opens a new round. engine / voice / pipeline_version are '
    'frozen per job. A claim bumps attempts and mints claim_nonce; every worker write is fenced on '
    'both, so a re-claimed zombie writes nothing. Worker failures (<= 2) and lease re-claims '
    '(<= 4) are capped separately. The worker holds no Supabase key: it reaches this table only '
    'through the token-gated internal API (/api/v1/internal/learn). service_role only.';

COMMENT ON COLUMN public.learn_narration_jobs.attempts IS
    'Claims ever made. Each claim sets attempts + 1 and a fresh claim_nonce; the worker presents '
    '<attempts>.<nonce> on every call and every write is fenced on both.';
COMMENT ON COLUMN public.learn_narration_jobs.worker_failures IS
    'Failures the worker reported. Back to queued while below the cap, failed at it.';
COMMENT ON COLUMN public.learn_narration_jobs.lease_reclaims IS
    'Times an in_progress job whose lease ran out (a killed render) was claimed again. At the cap '
    'the next expiry closes the job failed instead.';
COMMENT ON COLUMN public.learn_narration_jobs.engine IS
    'chatterbox | kokoro, frozen when the job is enqueued (LEARN_NARRATOR_ENGINE at that time). A '
    'worker is offered only the jobs whose engine it declares it can render.';

-- ── RLS + grants: service_role only ───────────────────────────────────────────────────
ALTER TABLE public.learn_weekly_pieces  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.learn_narration_jobs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "learn_weekly_pieces_service_all" ON public.learn_weekly_pieces;
CREATE POLICY "learn_weekly_pieces_service_all" ON public.learn_weekly_pieces
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "learn_narration_jobs_service_all" ON public.learn_narration_jobs;
CREATE POLICY "learn_narration_jobs_service_all" ON public.learn_narration_jobs
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.learn_weekly_pieces  FROM anon, authenticated;
REVOKE ALL ON public.learn_narration_jobs FROM anon, authenticated;

-- A table with no GRANT is owner-only, not open (169's lesson): grant service_role explicitly.
-- UUID primary keys: no sequence to grant.
GRANT ALL ON public.learn_weekly_pieces  TO service_role;
GRANT ALL ON public.learn_narration_jobs TO service_role;

-- ── C. money_move_articles ─────────────────────────────────────────────────────────────
ALTER TABLE public.money_move_articles
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'bundle'
        CHECK (source IN ('bundle', 'weekly'));

COMMENT ON COLUMN public.money_move_articles.source IS
    'bundle = seeded from the bundled money_moves.json by scripts/seed_money_moves.py; weekly = '
    'published by the Wiser weekly pipeline from an approved learn_weekly_pieces row (and deleted '
    'by its Unpublish). Migration 204.';

-- Refuse loudly, before the index, if production already holds a case-folded duplicate title:
-- CREATE UNIQUE INDEX would fail with a bare 23505 naming no title.
DO $dup$
DECLARE
    dup TEXT;
BEGIN
    SELECT string_agg(t, ' | ' ORDER BY t) INTO dup
      FROM (SELECT lower(title) AS t
              FROM public.money_move_articles
             GROUP BY lower(title)
            HAVING count(*) > 1) d;
    IF dup IS NOT NULL THEN
        RAISE EXCEPTION
            '204: money_move_articles holds duplicate titles (case-folded): %. iOS keys cards and the audio refresh by title; rename or delete the duplicates, then re-run 204. Nothing was applied.',
            dup;
    END IF;
END
$dup$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_money_move_articles_title_lower
    ON public.money_move_articles (lower(title));

-- Close the client read door (see the header) and keep the backend's.
DROP POLICY IF EXISTS "money_moves_select_all" ON public.money_move_articles;
DROP POLICY IF EXISTS "money_moves_service_all" ON public.money_move_articles;
CREATE POLICY "money_moves_service_all" ON public.money_move_articles
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.money_move_articles FROM anon, authenticated;
GRANT ALL ON public.money_move_articles TO service_role;

COMMENT ON TABLE public.money_move_articles IS
    'Money Moves articles (Learn): investment case studies — blueprints, value traps and battles. '
    'source = bundle (seeded from the bundled JSON) or weekly (published by the Wiser weekly '
    'pipeline after owner approval). Titles are unique case-folded (iOS keys by title). '
    'service_role only since migration 204: the app reads it through the backend.';

-- ── D. Job ledger rows (enabled = the no-deploy kill switch) ────────────────────────────
INSERT INTO public.notification_job_state (job, enabled, updated_at)
VALUES ('learn_weekly_topic',   TRUE, NOW()),
       ('learn_weekly_build',   TRUE, NOW()),
       ('learn_weekly_deadman', TRUE, NOW())
ON CONFLICT (job) DO NOTHING;

COMMIT;

-- VERIFY (read-only) after applying → one row: t | t | t | t | t | t
-- SELECT
--   to_regclass('public.learn_weekly_pieces') IS NOT NULL,
--   to_regclass('public.learn_narration_jobs') IS NOT NULL,
--   EXISTS (SELECT 1 FROM information_schema.columns
--            WHERE table_schema = 'public' AND table_name = 'money_move_articles'
--              AND column_name = 'source'),
--   NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants
--                WHERE table_schema = 'public'
--                  AND table_name IN ('learn_weekly_pieces', 'learn_narration_jobs', 'money_move_articles')
--                  AND grantee IN ('anon', 'authenticated')),
--   NOT EXISTS (SELECT 1 FROM pg_policies
--                WHERE schemaname = 'public' AND tablename = 'money_move_articles'
--                  AND policyname = 'money_moves_select_all'),
--   (SELECT count(*) FROM public.notification_job_state
--     WHERE job IN ('learn_weekly_topic', 'learn_weekly_build', 'learn_weekly_deadman')) = 3;
