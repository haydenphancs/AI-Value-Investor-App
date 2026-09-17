-- 170_marketing_engine.sql
--
-- Why: the zero-touch marketing engine (SYSTEM_DESIGN_GUIDELINES §12) needs a durable,
-- resumable record of what it made and what it published. It runs as TWO processes that
-- share nothing but this database:
--
--   * the MEDIA WORKER — a Railway cron service (`backend/marketing/`) with the ML image
--     (Kokoro, torch-cpu, Pillow, ffmpeg). It holds NO Supabase key at all: it reaches these tables only through the
--     token-gated internal API (`app/api/v1/endpoints/marketing_internal.py`) and uploads
--     media through short-lived Storage signed-upload URLs the API mints for it. That is the
--     least-privilege shape — a supply-chain compromise of a torch/spacy transitive dependency
--     must not become a database breach or a brand hijack. (A scoped Postgres role behind a
--     custom JWT was the first design; it is impossible here because the project's legacy
--     JWT-based API keys are disabled and the HS256 signing key is revoked — see
--     project memory `project_leaked_service_role_key`.)
--   * the PUBLISHER — a loop in the existing web lifespan (`app/main.py`, `_spawn`). It holds
--     the social-posting secrets and publishes `marketing_posts` rows that are `approved`,
--     claiming each row atomically before the first external call.
--
-- Four tables:
--   marketing_runs      one row per ET calendar day; the worker's claim + stage checkpoints
--                       (a Railway cron slot can be skipped or killed, so a run must be
--                       resumable from its last completed stage on the next hourly tick)
--   marketing_assets    every artefact (script, audio, video, cards, blog…) with its
--                       content-addressed Storage path and a two-phase upload status
--   marketing_posts     one row per (run, platform, format): the publish ledger. Rows are
--                       born `pending_review` and become `approved` by an admin (or at birth
--                       when MARKETING_AUTO_PUBLISH is on); the publisher moves them
--                       approved → queued → published | failed.
--   podcast_episodes    the rows a self-hosted RSS feed route (Phase 6 of the plan) will render;
--                       RSS is the only ingest path Apple/Spotify offer. GUIDs never change;
--                       MP3 paths are immutable.
--
-- Plus ONE PUBLIC bucket, `marketing-media`. ⚠️ PUBLIC ON PURPOSE AND MUST STAY PUBLIC:
-- Instagram/Threads/Facebook and Upload-Post FETCH the MP4 from a URL (Meta: "We cURL … so it
-- must be on a public server"), and podcast enclosures must be stable, unsigned URLs (Spotify
-- re-fetches only when the path changes; a rotating ?token= would churn every directory).
-- Every object here is a finished, published-or-about-to-be-published marketing artefact, so
-- nothing non-public may ever be written to it — and (auth.md §1a) NOTHING FMP-LICENSED may
-- ever be rendered into it. The content classes will be decided upstream by the Phase 2
-- content service, and Phase 7 of the plan adds a source-scan test that fails the build if a
-- marketing module imports `integrations/fmp.py`; until then `.claude/rules/marketing.md` is
-- the rule. Paths are content-addressed (`<run_date>/<kind>-<sha256[:16]>.<ext>`) and never
-- rewritten in place.
--
-- RLS: service-role only on all four tables (the 162 template, not the 049-era public-read
-- one). The iOS app never reads these; the only readers are the backend (service_role) and,
-- through the internal API, the worker.
--
-- Idempotent: IF NOT EXISTS everywhere, DROP POLICY IF EXISTS before CREATE, ON CONFLICT on
-- the bucket row. Safe to re-run.

BEGIN;

-- ── 1. marketing_runs ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.marketing_runs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The ET calendar day this run is FOR. One run per day; the worker's claim is the
    -- UNIQUE constraint, not a clock inference (147's lesson).
    run_date        DATE        NOT NULL UNIQUE,
    status          TEXT        NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned', 'in_progress', 'media_ready',
                                      'published', 'failed', 'skipped')),
    -- Last COMPLETED stage. The worker resumes from the stage after this one.
    stage           TEXT        NOT NULL DEFAULT 'planned'
                    CHECK (stage IN ('planned', 'selected', 'scripted', 'voiced',
                                     'rendered', 'assets_ready')),
    -- §12 content class. 'A' = educational/general (Caydex-owned material, no named-ticker
    -- value opinion); 'C' = reportorial public filings from SEC EDGAR (Phase 8). There is
    -- deliberately no 'B': ticker-specific opinions stay inside the authenticated app.
    content_class   TEXT        NOT NULL DEFAULT 'A' CHECK (content_class IN ('A', 'C')),
    template_id     TEXT,
    -- What the day is about, e.g. 'money_moves:<slug>' or 'journey:<slug>'.
    source_ref      TEXT,
    worker_version  TEXT,
    dry_run         BOOLEAN     NOT NULL DEFAULT TRUE,
    attempts        INTEGER     NOT NULL DEFAULT 0,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    -- Per-stage wall/CPU seconds, so the Railway numbers replace the laptop proxies in §12.
    timings         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.marketing_runs IS
    'One row per ET calendar day of the zero-touch marketing engine (SYSTEM_DESIGN_GUIDELINES '
    '§12). The media worker CLAIMS the day by inserting this row (UNIQUE run_date) and checkpoints '
    'its last completed stage here so a killed or skipped Railway cron slot resumes on the next '
    'hourly tick instead of starting over or double-producing. Written through the token-gated '
    'internal API (app/api/v1/endpoints/marketing_internal.py); the worker holds no Supabase key.';

CREATE INDEX IF NOT EXISTS idx_marketing_runs_status
    ON public.marketing_runs (status, run_date DESC);

-- ── 2. marketing_assets ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.marketing_assets (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- RESTRICT, not CASCADE: an asset row is the only record of an object in the public
    -- bucket; deleting a run must not orphan objects silently.
    run_id           UUID        NOT NULL REFERENCES public.marketing_runs (id) ON DELETE RESTRICT,
    kind             TEXT        NOT NULL
                     CHECK (kind IN ('manifest', 'script', 'audio', 'podcast_audio', 'video',
                                     'card', 'carousel', 'caption', 'blog')),
    -- Object key inside the marketing-media bucket. Content-addressed and immutable:
    -- <run_date>/<kind>-<sha256[:16]>.<ext>. Never overwritten (podcast directories and Meta
    -- cache by URL).
    storage_path     TEXT        NOT NULL UNIQUE,
    content_type     TEXT        NOT NULL,
    bytes            BIGINT,
    sha256           TEXT        NOT NULL,
    duration_seconds NUMERIC(8, 3),
    -- Two-phase: the API mints a signed upload URL and inserts `pending_upload`; the worker
    -- PUTs the bytes; the API verifies the object exists (HEAD) and flips to `ready`.
    status           TEXT        NOT NULL DEFAULT 'pending_upload'
                     CHECK (status IN ('pending_upload', 'ready', 'failed')),
    metadata         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.marketing_assets IS
    'Every artefact the marketing engine produces for a run (script, narration, podcast MP3, '
    'MP4, card PNGs, captions, blog HTML), keyed by its content-addressed path in the PUBLIC '
    'marketing-media bucket. Two-phase upload: pending_upload while the worker holds a signed '
    'upload URL, ready once the API has verified the object exists. Nothing FMP-licensed may '
    'be rendered into this bucket (auth.md §1a).';

CREATE INDEX IF NOT EXISTS idx_marketing_assets_run
    ON public.marketing_assets (run_id, kind);

-- ── 3. marketing_posts ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.marketing_posts (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- RESTRICT, not CASCADE: this is the publish ledger (external ids, cost). A run row can
    -- never take the record of what was actually posted down with it.
    run_id          UUID        NOT NULL REFERENCES public.marketing_runs (id) ON DELETE RESTRICT,
    platform        TEXT        NOT NULL
                    CHECK (platform IN ('tiktok', 'youtube', 'instagram', 'facebook', 'linkedin',
                                        'threads', 'bluesky', 'x', 'pinterest', 'mastodon',
                                        'podcast', 'blog', 'hashnode', 'devto')),
    format          TEXT        NOT NULL
                    CHECK (format IN ('video', 'carousel', 'image', 'text', 'podcast', 'article')),
    -- pending_review → approved (admin, or at birth under MARKETING_AUTO_PUBLISH)
    --                → queued (claimed by the publisher, atomically, before any external call)
    --                → published | failed.  rejected / skipped / retracted are terminal.
    status          TEXT        NOT NULL DEFAULT 'pending_review'
                    CHECK (status IN ('pending_review', 'approved', 'rejected', 'queued',
                                      'published', 'failed', 'skipped', 'retracted')),
    title           TEXT,
    caption         TEXT        NOT NULL DEFAULT '',
    asset_ids       UUID[]      NOT NULL DEFAULT '{}',
    -- The key a platform adapter presents to the outlet (Phase 5: Upload-Post's Idempotency-Key
    -- / external_id), so a publisher restart mid-fan-out can never double-post.
    -- Stable: <run_date>:<platform>:<format>.
    idempotency_key TEXT        NOT NULL UNIQUE,
    external_id     TEXT,
    external_url    TEXT,
    attempts        INTEGER     NOT NULL DEFAULT 0,
    last_error      TEXT,
    -- Micro-dollars (1 USD = 1,000,000). X pay-per-use is $0.015 per post ($0.20 with a URL,
    -- $0.005 media metadata) — fractions of a cent, so an integer-cents column cannot hold it.
    cost_micros     BIGINT      NOT NULL DEFAULT 0,
    metrics         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    claimed_at      TIMESTAMPTZ,
    approved_at     TIMESTAMPTZ,
    approved_by     TEXT,
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, platform, format)
);

COMMENT ON TABLE public.marketing_posts IS
    'Publish ledger of the marketing engine: one row per (run, platform, format). Born '
    'pending_review; an admin (or MARKETING_AUTO_PUBLISH) makes it approved; the publisher loop in '
    'app/main.py claims it (approved → queued, atomically) before the first external call, then '
    'records published | failed with the platform''s id/URL, the attempt count and the cost in '
    'micro-dollars. idempotency_key is the key presented to the outlet, so a restart cannot double-post.';

CREATE INDEX IF NOT EXISTS idx_marketing_posts_status
    ON public.marketing_posts (status, created_at);
CREATE INDEX IF NOT EXISTS idx_marketing_posts_run
    ON public.marketing_posts (run_id);

-- ── 4. podcast_episodes ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.podcast_episodes (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The RSS <guid>. Apple: "never changes". Minted once, here, never derived from the path.
    guid             UUID        NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    run_id           UUID        REFERENCES public.marketing_runs (id) ON DELETE SET NULL,
    title            TEXT        NOT NULL,
    description      TEXT        NOT NULL DEFAULT '',
    -- Object key in marketing-media. Immutable: Spotify re-downloads ONLY on a path change.
    mp3_path         TEXT        NOT NULL UNIQUE,
    bytes            BIGINT      NOT NULL,
    duration_seconds INTEGER     NOT NULL,
    published_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.podcast_episodes IS
    'Episodes of the self-hosted podcast RSS feed. Apple Podcasts and Spotify offer NO upload API: '
    'they ingest an RSS feed submitted once and re-poll it, so this table IS the podcast. guid never '
    'changes and mp3_path is immutable (Spotify only re-fetches an enclosure whose path changed).';

CREATE INDEX IF NOT EXISTS idx_podcast_episodes_published
    ON public.podcast_episodes (published_at DESC);

-- ── 5. RLS + grants: service_role only (162 template) ────────────────────────────
ALTER TABLE public.marketing_runs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketing_assets  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketing_posts   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.podcast_episodes  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "marketing_runs_service_all" ON public.marketing_runs;
CREATE POLICY "marketing_runs_service_all" ON public.marketing_runs
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "marketing_assets_service_all" ON public.marketing_assets;
CREATE POLICY "marketing_assets_service_all" ON public.marketing_assets
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "marketing_posts_service_all" ON public.marketing_posts;
CREATE POLICY "marketing_posts_service_all" ON public.marketing_posts
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "podcast_episodes_service_all" ON public.podcast_episodes;
CREATE POLICY "podcast_episodes_service_all" ON public.podcast_episodes
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.marketing_runs    FROM anon, authenticated;
REVOKE ALL ON public.marketing_assets  FROM anon, authenticated;
REVOKE ALL ON public.marketing_posts   FROM anon, authenticated;
REVOKE ALL ON public.podcast_episodes  FROM anon, authenticated;
-- A table with no GRANT is owner-only, not open (169's lesson): the policy admits nobody
-- until service_role holds the table privilege.
GRANT ALL ON public.marketing_runs    TO service_role;
GRANT ALL ON public.marketing_assets  TO service_role;
GRANT ALL ON public.marketing_posts   TO service_role;
GRANT ALL ON public.podcast_episodes  TO service_role;

-- ── 6. The public media bucket ───────────────────────────────────────────────────
-- Mirrors 136/137 (journey-images / money-moves-images): world-readable by design, written
-- only by service_role (the API mints signed upload URLs for the worker; the PUT through a
-- signed URL is authorised by the token, not by a role). 300 MB is Instagram's Reel cap and
-- the largest object the pipeline may ever produce; MP3s and PNGs are far smaller.
-- DO UPDATE re-publicises a bucket someone had made private — intended, matching 133/136.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'marketing-media', 'marketing-media', true, 314572800,
    ARRAY['video/mp4', 'image/png', 'image/jpeg', 'audio/mpeg', 'audio/mp4',
          'application/json', 'text/html', 'text/plain', 'text/markdown']
)
ON CONFLICT (id) DO UPDATE
    SET public = EXCLUDED.public,
        file_size_limit = EXCLUDED.file_size_limit,
        allowed_mime_types = EXCLUDED.allowed_mime_types;

DROP POLICY IF EXISTS "marketing_media_public_read" ON storage.objects;
CREATE POLICY "marketing_media_public_read" ON storage.objects
    FOR SELECT TO anon, authenticated
    USING (bucket_id = 'marketing-media');

DROP POLICY IF EXISTS "marketing_media_service_write" ON storage.objects;
CREATE POLICY "marketing_media_service_write" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'marketing-media')
    WITH CHECK (bucket_id = 'marketing-media');

COMMIT;
