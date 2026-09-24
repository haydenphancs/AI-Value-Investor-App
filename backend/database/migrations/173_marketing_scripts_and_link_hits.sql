-- 173_marketing_scripts_and_link_hits.sql
--
-- Why: Phase 2 of the zero-touch marketing engine (SYSTEM_DESIGN_GUIDELINES §12) needs two
-- things before any media is rendered for a day — a VALIDATED SCRIPT, and a way to count
-- who tapped the link in a caption — and it closes two defects in migration 170 found while
-- designing them. Six parts, each with its reason:
--
--   A. public.marketing_scripts — ONE row per marketing run: the day's frozen selection
--      (source_ref, template_id, the fact sheet the writer was given) and, once accepted,
--      the writer's package. Written ONLY by the web side. The media worker never writes
--      it: it kicks generation through the token-gated internal API and polls
--      (kick-and-poll), because the Gemini key stays web-side and the worker holds neither a
--      Supabase key nor a model key (rules/marketing.md §2).
--
--      WRITER OUTPUT LIVES HERE AND NOWHERE ELSE (the accepted package, and the violations
--      of the rounds that failed):
--        * never in the PUBLIC marketing-media bucket — every object there is world-readable
--          by URL, and a rejected round is, by definition, text that failed the compliance
--          validators (a named person, a price opinion, an ungrounded figure);
--        * never in marketing_runs.metadata — the run service merges that JSONB key by key
--          in a read-modify-write, which is not atomic under two writers (a stage
--          checkpoint landing mid-generation would clobber it), and it echoes the whole
--          object back to the worker on every response, i.e. raw copy would flow to the
--          least-trusted process in the engine.
--
--      FENCING LIVES IN THE ROW, not in process memory: the web service may run more than
--      one instance and a deploy kills in-flight tasks. A generation acquires the row with
--      ONE conditional UPDATE that sets a fresh `generation_id`, pushes `lease_until` out
--      and bumps `generations`; it refreshes the lease before each model call; and every
--      terminal write is conditional on that same `generation_id`, so a task that lost its
--      lease (expired, or re-kicked on another instance) writes nothing. `generations`
--      counts attempts against the writer's cap; `retry_not_before` backs off after a
--      model error; `last_error` says why.
--
--      `accepted` IS TERMINAL AND ITS `output` IMMUTABLE. The day's posts are built from
--      it (captions and titles are server-authored, never the worker's), so rewriting it
--      after posts exist would make the publish ledger disagree with what an outlet
--      actually published. `rejected` and `rest_day` are terminal too.
--
--      run_id is the PRIMARY KEY, so selection is first-write-wins: two concurrent kicks
--      INSERT the same key and the loser (23505) adopts the winner's row. ON DELETE
--      RESTRICT, like 170's children: a run row can never take the record of what the
--      day was about with it.
--
--   B. public.marketing_link_hits — per-campaign daily tap counts for the smart link
--      GET /go/{campaign} (the only link in a caption). Hits are batched in-process and
--      flushed every 60 s, one RPC per (campaign, day), so the redirect itself does no
--      I/O. `campaign` arrives from a PUBLIC URL; the route maps anything outside its
--      allow-list to a fixed value before counting, and the CHECK repeats the same pattern
--      so an upstream bug cannot store an attacker-chosen string. `day` is the ET calendar
--      day (the engine's day boundary), not UTC.
--
--   C. public.increment_marketing_link_hits(text, date, integer) — the atomic
--      upsert-increment (096 add_chat_tokens shape). A SELECT-then-UPDATE from Python
--      loses counts when two instances flush the same key. SECURITY INVOKER ON PURPOSE:
--      its only caller is service_role, which already holds the table grant, so the
--      function needs no owner rights — and an invoker function is never an escalation
--      path even if a grant on it leaked. It is still REVOKEd from PUBLIC, anon and
--      authenticated: least surface. A count outside [1, 1,000,000] raises instead of
--      writing: zero is a wasted round trip, a negative would subtract, and an absurd one
--      is a caller bug that must be loud, not averaged in.
--
--   D. RLS + grants: service_role only on both tables (the 162/170 template). iOS never
--      reads either; there is no sequence to grant (no serial column).
--
--   E. DROP 170's "marketing_media_public_read" policy on storage.objects. Same reasoning as
--      153 §B for the four other public buckets: a PUBLIC bucket is served from
--      /storage/v1/object/public/<bucket>/<path>, which BYPASSES RLS — that is how Meta,
--      Upload-Post and podcast apps fetch the media, and it keeps working. What a SELECT
--      policy for anon/authenticated actually enables is the LIST api, i.e. anyone holding
--      the shipped publishable key could enumerate every object, including media from a
--      run that was rejected at review or never published (content-addressed paths are
--      unguessable only while the bucket is not listable). The backend's own existence
--      check (`run_service`, which lists as a fallback) runs as service_role and keeps
--      "marketing_media_service_write" (FOR ALL), which stays. Signed-upload PUTs are
--      authorised by their token, not by a role policy.
--
--   F. Narrow the bucket's allowed_mime_types to exactly the media the worker renders:
--      drop text/html, text/plain and text/markdown. The worker is the least-trusted
--      process in the engine (ML dependency tree, rules/marketing.md §2); a PUBLIC bucket
--      under the brand that accepts text/html lets a compromised worker host a phishing
--      page at a caydex-branded URL. Scripts, captions and blog copy are server-authored
--      and live in marketing_scripts, never in the bucket. The list MUST equal the values
--      of app.schemas.marketing.ASSET_EXTENSIONS —
--      tests/test_marketing_run_service.py compares it against the LATEST migration that
--      sets it (this one). The new list gates FUTURE uploads only; the worker service had
--      not been deployed as of 2026-09-23, so no html/txt/md object should exist — the
--      VERIFY query below proves it rather than assuming it.
--
-- Deploy order: apply A-D BEFORE (or together with) the web deploy whose code reads
-- marketing_scripts / marketing_link_hits. E and F touch no code path and may be applied
-- at any time.
--
-- NOT destructive: no table, column or row is dropped. The policy drop removes LIST access
-- only (see E).
--
-- Idempotent: IF NOT EXISTS on tables and indexes, DROP POLICY IF EXISTS before CREATE,
-- CREATE OR REPLACE on the function, REVOKE/GRANT are declarative, and the bucket UPDATE
-- writes a constant. Safe to re-run.
--
-- VERIFY (run after applying):
--   SELECT policyname FROM pg_policies
--    WHERE schemaname = 'storage' AND tablename = 'objects'
--      AND policyname LIKE 'marketing_media%';           -- expect ONLY marketing_media_service_write
--   SELECT allowed_mime_types FROM storage.buckets WHERE id = 'marketing-media';
--                                                      -- expect the six media types, no text/*
--   SELECT name, metadata->>'mimetype' FROM storage.objects
--    WHERE bucket_id = 'marketing-media'
--      AND metadata->>'mimetype' NOT IN ('video/mp4', 'image/png', 'image/jpeg',
--                                        'audio/mpeg', 'audio/mp4', 'application/json');
--                                                      -- expect 0 rows
--   SELECT has_function_privilege('anon',
--          'public.increment_marketing_link_hits(text, date, integer)', 'EXECUTE');  -- expect f
--   SELECT relname, relrowsecurity FROM pg_class
--    WHERE relname IN ('marketing_scripts', 'marketing_link_hits');   -- expect t, t

BEGIN;

-- ── A. marketing_scripts ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.marketing_scripts (
    -- One script per run; the PK is the first-write-wins selection claim.
    run_id           UUID        PRIMARY KEY
                     REFERENCES public.marketing_runs (id) ON DELETE RESTRICT,
    -- rest_day / accepted / rejected are terminal. selected → generating while a generation
    -- holds the lease; a model error sends it back to selected (with retry_not_before); an
    -- expired lease is re-acquired from generating by the next kick.
    status           TEXT        NOT NULL DEFAULT 'selected'
                     CHECK (status IN ('rest_day', 'selected', 'generating', 'accepted',
                                       'rejected')),
    -- Frozen at selection, e.g. 'money_moves:<slug>'. Mirrored onto marketing_runs by the
    -- server; never taken from the worker.
    source_ref       TEXT,
    template_id      TEXT,
    -- The cleaned fact sheet the writer was given, frozen at selection so a later corpus
    -- edit cannot change what a retry is grounded against.
    fact_sheet       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- The accepted package (incl. the composed per-platform copy). NULL until accepted;
    -- immutable once accepted.
    output           JSONB,
    -- The last round's compliance/grounding violations (why a draft was repaired or rejected).
    violations       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    -- Fencing token of the generation that holds the lease.
    generation_id    UUID,
    lease_until      TIMESTAMPTZ,
    generations      INTEGER     NOT NULL DEFAULT 0 CHECK (generations >= 0),
    retry_not_before TIMESTAMPTZ,
    last_error       TEXT,
    -- The model REQUESTED (the writer's constant), and the prompt revision that produced
    -- `output`, so a quality regression can be traced to a prompt change.
    model            TEXT,
    prompt_version   TEXT,
    tokens_used      INTEGER     NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.marketing_scripts IS
    'One row per marketing run: the day''s frozen selection (source_ref, template_id, '
    'fact_sheet) and the class-A writer''s output. Written ONLY by the web side (kick-and-poll '
    'through the internal API; the worker never writes it). Writer output lives here and never '
    'in the public marketing-media bucket or in marketing_runs.metadata, whose key-by-key merge is not '
    'atomic and is echoed to the worker on every response. A generation holds the row through '
    'lease_until plus a fresh generation_id taken by one conditional UPDATE, and every terminal '
    'write is fenced on that generation_id, so a task that lost its lease writes nothing. '
    'accepted is terminal and its output immutable: the day''s posts are built from it.';

COMMENT ON COLUMN public.marketing_scripts.generation_id IS
    'Fencing token. Set by the conditional UPDATE that acquires the lease; every later write of '
    'that generation is conditional on it, so a superseded generation cannot land.';
COMMENT ON COLUMN public.marketing_scripts.output IS
    'The accepted package. NULL until status = accepted, immutable afterwards.';

-- The kick path and a stuck-generation sweep look rows up by state and age.
CREATE INDEX IF NOT EXISTS idx_marketing_scripts_status
    ON public.marketing_scripts (status, updated_at);

-- ── B. marketing_link_hits ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.marketing_link_hits (
    -- Same pattern the /go/{campaign} route enforces before counting.
    campaign   TEXT        NOT NULL CHECK (campaign ~ '^[a-z0-9_-]{1,40}$'),
    -- ET calendar day of the taps (the engine's day boundary), not UTC.
    day        DATE        NOT NULL,
    hits       BIGINT      NOT NULL DEFAULT 0 CHECK (hits >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign, day)
);

COMMENT ON TABLE public.marketing_link_hits IS
    'Per-campaign daily tap counts for the smart link GET /go/{campaign}. Hits are batched '
    'in-process and flushed every 60 s through increment_marketing_link_hits (one call per '
    'campaign and ET day), so the redirect does no I/O. campaign comes from a public URL, so '
    'the CHECK repeats the allow-pattern the route enforces before counting.';

-- ── C. increment_marketing_link_hits ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.increment_marketing_link_hits(
    p_campaign TEXT,
    p_day      DATE,
    p_count    INTEGER
)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_hits BIGINT;
BEGIN
    IF p_count IS NULL OR p_count < 1 OR p_count > 1000000 THEN
        RAISE EXCEPTION 'increment_marketing_link_hits: p_count must be between 1 and 1000000, got %',
            p_count
            USING ERRCODE = '22023';  -- invalid_parameter_value
    END IF;

    INSERT INTO public.marketing_link_hits (campaign, day, hits, updated_at)
    VALUES (p_campaign, p_day, p_count, now())
    ON CONFLICT (campaign, day) DO UPDATE
        SET hits = marketing_link_hits.hits + EXCLUDED.hits,
            updated_at = now()
    RETURNING hits INTO v_hits;

    RETURN v_hits;
END;
$$;

COMMENT ON FUNCTION public.increment_marketing_link_hits(TEXT, DATE, INTEGER) IS
    'Atomic upsert-increment of one (campaign, day) counter; returns the new total. Runs with '
    'the caller''s privileges (INVOKER): its only caller is service_role, which holds the table '
    'grant. Raises 22023 for a count outside 1..1000000.';

-- Not on the public RPC surface. REVOKE before GRANT; both idempotent.
REVOKE ALL ON FUNCTION public.increment_marketing_link_hits(TEXT, DATE, INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.increment_marketing_link_hits(TEXT, DATE, INTEGER)
    TO service_role;

-- ── D. RLS + grants: service_role only (162/170 template) ────────────────────────
ALTER TABLE public.marketing_scripts   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.marketing_link_hits ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "marketing_scripts_service_all" ON public.marketing_scripts;
CREATE POLICY "marketing_scripts_service_all" ON public.marketing_scripts
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "marketing_link_hits_service_all" ON public.marketing_link_hits;
CREATE POLICY "marketing_link_hits_service_all" ON public.marketing_link_hits
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.marketing_scripts   FROM anon, authenticated;
REVOKE ALL ON public.marketing_link_hits FROM anon, authenticated;
-- A table with no GRANT is owner-only, not open (169's lesson): the policy admits nobody
-- until service_role holds the table privilege.
GRANT ALL ON public.marketing_scripts   TO service_role;
GRANT ALL ON public.marketing_link_hits TO service_role;

-- ── E. The media bucket stops being LISTABLE (153 §B precedent) ──────────────────
-- ⚠️ Do NOT "restore" this policy to fix a broken media URL: public object URLs bypass RLS
-- and never needed it. It only ever enabled POST /storage/v1/object/list/marketing-media.
DROP POLICY IF EXISTS "marketing_media_public_read" ON storage.objects;

-- ── F. The media bucket accepts media only ───────────────────────────────────────
-- Must equal the values of app.schemas.marketing.ASSET_EXTENSIONS (test-pinned).
UPDATE storage.buckets
   SET allowed_mime_types = ARRAY['video/mp4', 'image/png', 'image/jpeg', 'audio/mpeg',
                                  'audio/mp4', 'application/json']
 WHERE id = 'marketing-media';

COMMIT;
