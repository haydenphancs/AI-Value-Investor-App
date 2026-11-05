-- 207_learn_narration_catalog_jobs.sql
--
-- Why: the owner decided on 2026-11-01 ("Yes, build it") that the Railway learn-narrator renders
-- two hand-written Money Moves CATALOG articles (Coca-Cola vs. PepsiCo, Kodak) so nothing heavy
-- runs on the 16 GB Mac ("i don't want my laptop on fire"). The narrator already renders in the
-- catalog's exact voice (the same reference wav, exaggeration 0.65, cfg 0.40, 165 WPM, 0.6 s block
-- pause, 96k AAC) and the server already produces the catalog's exact SENTENCE readAlong /
-- itemsReadAlong shape (WAV2VEC2 aligner, MIT). But migration 204 built its queue for weekly
-- pieces only: `learn_narration_jobs.piece_id` is NOT NULL with a foreign key to
-- learn_weekly_pieces, and a catalog article has no piece row (it is authored by hand, lives in
-- the bundled money_moves.json, and is seeded by scripts/seed_money_moves.py).
--
-- So a narration job now narrates exactly ONE of two subjects:
--   * a weekly piece  — piece_id set, catalog_slug NULL (every row before this migration);
--   * a catalog article — piece_id NULL, catalog_slug = the article's slug.
-- The worker (backend/learn_narrator/) is unchanged and never learns which: it claims, renders,
-- uploads to the path the server names and reports timings. The server decides the rest
-- (app/services/learn_weekly/narration.py): a catalog job's audio goes to the content-addressed
-- `audio/catalog/<slug>-<sha256[:12]>.m4a` in the same private money-moves-media bucket; its
-- completion moves NO piece and sends the owner one Telegram line; the owner then runs
-- scripts/learn_catalog_fetch.py, which downloads the file and merges the timings into the
-- article. Jobs are queued by scripts/learn_catalog_narrate.py (ledger.enqueue_catalog_job_sync),
-- first write wins on (catalog_slug, round) exactly as weekly jobs do on (piece_id, round).
--
-- Parts:
--   1. piece_id DROP NOT NULL (its FK and ON DELETE CASCADE stay: a NULL passes a foreign key).
--   2. catalog_slug TEXT (nullable).
--   3. learn_narration_jobs_one_subject — exactly one of piece_id / catalog_slug is set.
--   4. learn_narration_jobs_catalog_slug_check — the lowercase kebab-case slug shape the bundle
--      and learn_weekly_pieces.slug use (<= 160 characters).
--   5. learn_narration_jobs_catalog_round — UNIQUE (catalog_slug, round), a FULL unique
--      constraint (not a partial index), so PostgREST's upsert `on_conflict=catalog_slug,round`
--      can infer it. NULLs stay distinct, so weekly rows (catalog_slug NULL) never collide on it,
--      and catalog rows (piece_id NULL) never collide on 204's UNIQUE (piece_id, round). Its index
--      also serves the per-slug read (ledger.list_catalog_jobs_sync).
--   6. Comments: the new column, piece_id, the table, and learn_weekly_pieces.dry_run, which
--      gains the one exception the owner approved on 2026-11-01 (ledger.promote_rehearsed_sync,
--      scripts/learn_weekly_promote.py — the W44 rehearsal published live).
--
-- No new table: no schema_curation entry. Grants and RLS: unchanged — 204's
-- `GRANT ALL ON public.learn_narration_jobs TO service_role` covers the new column and its
-- service-role-only policy is table-wide; anon / authenticated still hold nothing.
--
-- Safe on the live table: DROP NOT NULL and ADD COLUMN of a nullable column with no default are
-- catalog-only changes (no rewrite). The two CHECKs and the UNIQUE scan the table once (a handful of
-- rows a week); every existing row has piece_id set and catalog_slug NULL, so all three hold.
--
-- Deploy order: apply this BEFORE the web deploy that carries the catalog branch. The ledger's
-- JOB_COLUMNS now names catalog_slug, but nothing READS it until a catalog job exists, and nothing
-- writes one until scripts/learn_catalog_narrate.py --yes runs — so a web deploy that lands first
-- keeps narrating weekly pieces exactly as before; the narrate script refuses (PGRST204 / 42P10)
-- against a table without this migration.
-- FULL ORDER: (1) apply 207, (2) deploy the web that carries the catalog branch, (3) only THEN run
-- scripts/learn_catalog_narrate.py --yes. A catalog job claimed by an OLDER web finds no piece, sits
-- in_progress until its lease re-claims run out and ends failed with no alert.
--
-- Idempotent: DROP NOT NULL on a nullable column is a no-op; ADD COLUMN IF NOT EXISTS; each named
-- constraint is added inside a DO block guarded on pg_constraint (Postgres has no ADD CONSTRAINT IF
-- NOT EXISTS); COMMENT is re-runnable. One transaction: a refused constraint leaves nothing
-- half-applied.

BEGIN;

ALTER TABLE public.learn_narration_jobs ALTER COLUMN piece_id DROP NOT NULL;
ALTER TABLE public.learn_narration_jobs ADD COLUMN IF NOT EXISTS catalog_slug TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'learn_narration_jobs_one_subject'
                      AND conrelid = 'public.learn_narration_jobs'::regclass) THEN
        ALTER TABLE public.learn_narration_jobs
            ADD CONSTRAINT learn_narration_jobs_one_subject
            CHECK ((piece_id IS NULL) <> (catalog_slug IS NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'learn_narration_jobs_catalog_slug_check'
                      AND conrelid = 'public.learn_narration_jobs'::regclass) THEN
        ALTER TABLE public.learn_narration_jobs
            ADD CONSTRAINT learn_narration_jobs_catalog_slug_check
            CHECK (catalog_slug IS NULL
                   OR (catalog_slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' AND char_length(catalog_slug) <= 160));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'learn_narration_jobs_catalog_round'
                      AND conrelid = 'public.learn_narration_jobs'::regclass) THEN
        ALTER TABLE public.learn_narration_jobs
            ADD CONSTRAINT learn_narration_jobs_catalog_round UNIQUE (catalog_slug, round);
    END IF;
END $$;

COMMENT ON COLUMN public.learn_narration_jobs.catalog_slug IS
    'Set (and piece_id NULL) when the job narrates a hand-written Money Moves CATALOG article rather '
    'than a weekly piece (migration 207, owner 2026-11-01): the article''s slug, lowercase kebab-case. '
    'Its audio goes to audio/catalog/<slug>-<sha256[:12]>.m4a in the private money-moves-media bucket; '
    'completing it moves no piece and alerts the owner, who downloads it with '
    'scripts/learn_catalog_fetch.py. Queued by scripts/learn_catalog_narrate.py; first write wins on '
    'UNIQUE (catalog_slug, round). Exactly one of piece_id / catalog_slug is set (CHECK).';
COMMENT ON COLUMN public.learn_narration_jobs.piece_id IS
    'The weekly piece this job narrates (FK, ON DELETE CASCADE), or NULL for a catalog article''s job '
    '(catalog_slug set instead — migration 207). Exactly one of the two is set (CHECK).';

COMMENT ON TABLE public.learn_narration_jobs IS
    'Wiser weekly content engine (migration 204): the narrator worker''s queue, one job per '
    '(piece, round) — Retry narration opens a new round — or, since migration 207, per (catalog_slug, '
    'round) for a hand-written Money Moves catalog article rendered on the narrator instead of a laptop. '
    'engine / voice / pipeline_version are frozen per job. A claim bumps attempts and mints claim_nonce; '
    'every worker write is fenced on both, so a re-claimed zombie writes nothing. Worker failures (<= 2) '
    'and lease re-claims (<= 4) are capped separately. The worker holds no Supabase key: it reaches this '
    'table only through the token-gated internal API (/api/v1/internal/learn). service_role only.';

COMMENT ON COLUMN public.learn_weekly_pieces.dry_run IS
    'Frozen at creation from LEARN_WEEKLY_DRY_RUN. TRUE = the piece ends rehearsed; it can never be '
    'publishing or published (CHECK). The ONE exception (owner 2026-11-01): '
    'ledger.promote_rehearsed_sync (scripts/learn_weekly_promote.py) moves a rehearsed dry-run piece to '
    'approved with dry_run FALSE in one fenced UPDATE — the W44 rehearsal the owner approved and chose to '
    'publish. No other code path ever rewrites dry_run.';

COMMIT;

-- VERIFY (read-only) after applying → one row: t | t | t | t | t
-- SELECT
--   EXISTS (SELECT 1 FROM information_schema.columns
--            WHERE table_schema = 'public' AND table_name = 'learn_narration_jobs'
--              AND column_name = 'catalog_slug' AND data_type = 'text' AND is_nullable = 'YES'),
--   EXISTS (SELECT 1 FROM information_schema.columns
--            WHERE table_schema = 'public' AND table_name = 'learn_narration_jobs'
--              AND column_name = 'piece_id' AND is_nullable = 'YES'),
--   (SELECT count(*) FROM pg_constraint
--     WHERE conrelid = 'public.learn_narration_jobs'::regclass
--       AND conname IN ('learn_narration_jobs_one_subject', 'learn_narration_jobs_catalog_slug_check',
--                       'learn_narration_jobs_catalog_round')) = 3,
--   NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants
--                WHERE table_schema = 'public' AND table_name = 'learn_narration_jobs'
--                  AND grantee IN ('anon', 'authenticated')),
--   (SELECT count(*) FROM public.learn_narration_jobs WHERE catalog_slug IS NOT NULL) = 0;
--                                   -- 0 until scripts/learn_catalog_narrate.py --yes runs
