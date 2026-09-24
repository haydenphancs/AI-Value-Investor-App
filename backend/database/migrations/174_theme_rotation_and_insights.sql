-- 174_theme_rotation_and_insights.sql
--
-- Why: the Home "Emerging Frontiers" theme cards (trending_themes, migration 081) carry a
-- hand-curated `tickers` array that nothing has updated since migration 084. The owner asked
-- (2026-09-23) for each theme's stocks to stay relevant AUTOMATICALLY: every theme is
-- re-scored on the first US trading day of each month and changes only when a better
-- on-theme stock clearly outranks a member — at most ~30% of the list, usually 0-3 names,
-- and a removed stock may come back. The same request added a daily "why it's moving"
-- summary and performance vs an S&P 500 ETF for each theme. Code:
-- backend/app/services/theme_rotation/ and backend/app/services/theme_insights_service.py.
--
-- Parts:
--   A. theme_rotation_runs      — one row per (month, mode): the month-level "already done"
--      record for the monthly job. The CONCURRENCY claim is the existing day-keyed
--      notification_job_state claim (147); this row is what stops a second run on day 2..7 of
--      the catch-up window, which a day-keyed claim alone would allow.
--   B. theme_rotation_decisions — every (run, theme, ticker) decision with its reason code and
--      score breakdown. History (tenure, strikes, returning stocks) is read ONLY from
--      published LIVE runs, so a dry run can never cause a live removal.
--   C. theme_relevance_cache    — the AI fit verdict on a company description, keyed by the
--      description's hash and the prompt/definition versions (NOT by month), so the verdict
--      cannot flip at random from one month to the next.
--   D. theme_daily_insights     — per (theme, trading day): equal-weight performance vs the
--      benchmark ETF, the index series, and the dated "why it's moving" summary.
--   E. trending_themes          — `tickers_as_of` (the review date shown on the card),
--      `rotation_enabled` (per-theme opt-out), `pinned_tickers` / `blocked_tickers` (editor
--      overrides). "The New Oil" is re-labelled Critical Minerals — its basket already holds
--      lithium, copper and uranium miners (084), and the job scores against the category.
--      AND its anon/authenticated read grant is CLOSED: `tickers` becomes the output of a
--      pipeline that scores FMP-licensed data (End-User Display Rights, auth.md §1a), the
--      pins/blocks are editorial controls, and the iOS app has no Supabase client — the
--      backend (service_role) is the only reader.
--   F. publish_theme_rotation() — writes every theme's new basket in ONE transaction, and only
--      if each basket still equals what the run read and no editor override changed under
--      it (a mid-run Studio edit — to `tickers`, a new block, or rotation switched off —
--      wins, and the run fails loudly instead of overwriting it).
--   G. notification_job_state rows for the two jobs (their `enabled` = kill switch).
--
-- Idempotent: IF NOT EXISTS on tables, columns and indexes; DROP POLICY IF EXISTS before
-- CREATE; CREATE OR REPLACE on the function; REVOKE/GRANT are declarative; the seed rows and
-- the category re-label are guarded.
--
-- VERIFY after applying:
--   SELECT grantee, privilege_type FROM information_schema.role_table_grants
--    WHERE table_name IN ('trending_themes','theme_rotation_runs','theme_rotation_decisions',
--                         'theme_relevance_cache','theme_daily_insights')
--      AND grantee IN ('anon','authenticated');          -- expect 0 rows

BEGIN;

-- A. Monthly run record ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.theme_rotation_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_month     DATE NOT NULL CHECK (EXTRACT(DAY FROM run_month) = 1),
    mode          TEXT NOT NULL CHECK (mode IN ('live', 'dry_run', 'preview')),
    status        TEXT NOT NULL DEFAULT 'in_progress'
                  CHECK (status IN ('in_progress', 'computed', 'published', 'failed')),
    attempts      INTEGER NOT NULL DEFAULT 1,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    published_at  TIMESTAMPTZ,
    definitions_version TEXT,
    params        JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary       JSONB NOT NULL DEFAULT '{}'::jsonb,
    fmp_calls     INTEGER NOT NULL DEFAULT 0,
    llm_tokens    INTEGER NOT NULL DEFAULT 0,
    error         TEXT
);

COMMENT ON TABLE public.theme_rotation_runs IS
    'One row per (month, mode) of the monthly Emerging Frontiers theme rotation '
    '(services/theme_rotation). Month-level idempotency record; the concurrency claim is '
    'notification_job_state (job theme_rotation_monthly). A preview run is never unique and '
    'never published.';

-- One live and one dry run per month; previews are unlimited.
CREATE UNIQUE INDEX IF NOT EXISTS uq_theme_rotation_runs_month_mode
    ON public.theme_rotation_runs (run_month, mode)
    WHERE mode IN ('live', 'dry_run');
CREATE INDEX IF NOT EXISTS idx_theme_rotation_runs_mode_status_month
    ON public.theme_rotation_runs (mode, status, run_month DESC);

-- B. Per-ticker decisions -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.theme_rotation_decisions (
    id          BIGSERIAL PRIMARY KEY,
    run_id      UUID NOT NULL REFERENCES public.theme_rotation_runs (id) ON DELETE CASCADE,
    run_month   DATE NOT NULL,
    slug        TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN (
                    'kept', 'added', 'returned', 'removed', 'deferred', 'rejected', 'bench')),
    reason_code TEXT NOT NULL,
    reason_text TEXT,
    score       NUMERIC,
    score_parts JSONB NOT NULL DEFAULT '{}'::jsonb,
    rank        INTEGER,
    was_member  BOOLEAN NOT NULL DEFAULT FALSE,
    strike      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, slug, ticker)
);

COMMENT ON TABLE public.theme_rotation_decisions IS
    'Every (run, theme, ticker) decision of the monthly theme rotation with its reason code '
    'and score breakdown. Tenure / strikes / returning-stock history is read ONLY from rows '
    'of published live runs.';

CREATE INDEX IF NOT EXISTS idx_theme_rotation_decisions_history
    ON public.theme_rotation_decisions (slug, ticker, run_month DESC);
CREATE INDEX IF NOT EXISTS idx_theme_rotation_decisions_run
    ON public.theme_rotation_decisions (run_id);

-- C. AI fit verdicts ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.theme_relevance_cache (
    ticker              TEXT NOT NULL,
    slug                TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    definitions_version TEXT NOT NULL,
    description_hash    TEXT NOT NULL,
    verdict             TEXT NOT NULL CHECK (verdict IN ('core', 'adjacent', 'not_related')),
    pure_play_band      TEXT,
    rationale           TEXT,
    model               TEXT,
    tokens_used         INTEGER NOT NULL DEFAULT 0,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, slug, prompt_version, definitions_version, description_hash)
);

COMMENT ON TABLE public.theme_relevance_cache IS
    'Tier-2 cache of the AI verdict on whether a company''s own description is on-theme. '
    'A verdict can only BLOCK a stock from joining a theme; it never adds points. Failures '
    'are never cached. `rationale` is internal audit text and is never shown to users.';

-- D. Daily theme insights --------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.theme_daily_insights (
    slug             TEXT NOT NULL,
    as_of            DATE NOT NULL,
    performance      JSONB NOT NULL DEFAULT '{}'::jsonb,
    series           JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary_headline TEXT,
    summary_text     TEXT,
    summary_as_of    DATE,
    drivers          JSONB NOT NULL DEFAULT '[]'::jsonb,
    news_fingerprint TEXT,
    model            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (slug, as_of)
);

COMMENT ON TABLE public.theme_daily_insights IS
    'Per (theme, US trading day): equal-weight performance of the theme''s CURRENT stocks vs '
    'the benchmark ETF (1M / YTD / 1Y + index series) and the dated "why it''s moving" '
    'summary. Written once per theme after the close by theme_insights_service; served to '
    'iOS through the Home theme endpoints. FMP-derived — service_role only.';

CREATE INDEX IF NOT EXISTS idx_theme_daily_insights_latest
    ON public.theme_daily_insights (slug, as_of DESC);

-- E. trending_themes: review date, per-theme opt-out, editor overrides, relabel, lock ---
ALTER TABLE public.trending_themes ADD COLUMN IF NOT EXISTS tickers_as_of DATE;
ALTER TABLE public.trending_themes ADD COLUMN IF NOT EXISTS rotation_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE public.trending_themes ADD COLUMN IF NOT EXISTS pinned_tickers TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE public.trending_themes ADD COLUMN IF NOT EXISTS blocked_tickers TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN public.trending_themes.tickers_as_of IS
    'Date the monthly rotation last reviewed this basket (shown as "Updated <date>"). NULL '
    'until the first published run.';
COMMENT ON COLUMN public.trending_themes.rotation_enabled IS
    'FALSE = the monthly rotation copies this basket unchanged.';
COMMENT ON COLUMN public.trending_themes.pinned_tickers IS
    'Never rotated out (only a delisting removes one). Editor override.';
COMMENT ON COLUMN public.trending_themes.blocked_tickers IS
    'Never kept or added, and removed on the next run. Editor override; wins over pinned.';

UPDATE public.trending_themes
   SET category = 'Critical Minerals', updated_at = NOW()
 WHERE slug = 'the-new-oil' AND category = 'Rare Earth Mining';

-- The anon/authenticated read door (081) is closed; the backend is the only reader.
DROP POLICY IF EXISTS "trending_themes_select_all" ON public.trending_themes;

-- F. Atomic publish -------------------------------------------------------------------
-- SECURITY INVOKER (the default): the only caller is the backend's service_role, which can
-- already write these tables — elevation would add risk and nothing else.
CREATE OR REPLACE FUNCTION public.publish_theme_rotation(
    p_run_id  UUID,
    p_baskets JSONB,
    p_as_of   DATE
)
RETURNS TEXT
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_status    TEXT;
    v_mode      TEXT;
    v_published TIMESTAMPTZ;
    v_slug      TEXT;
    v_entry     JSONB;
    v_current   TEXT[];
    v_blocked   TEXT[];
    v_enabled   BOOLEAN;
    v_expected  TEXT[];
    v_new       TEXT[];
BEGIN
    SELECT status, mode, published_at INTO v_status, v_mode, v_published
      FROM theme_rotation_runs WHERE id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'theme_rotation_run_not_found:%', p_run_id;
    END IF;
    -- published_at as well as status: a failure write that raced a lost publish answer
    -- must never make a published month publishable again.
    IF v_status = 'published' OR v_published IS NOT NULL THEN
        RETURN 'already_published';
    END IF;
    IF v_mode <> 'live' OR v_status <> 'computed' THEN
        RAISE EXCEPTION 'theme_rotation_not_publishable:%:%', v_mode, v_status;
    END IF;

    FOR v_slug, v_entry IN SELECT key, value FROM jsonb_each(p_baskets) LOOP
        SELECT tickers, blocked_tickers, rotation_enabled
          INTO v_current, v_blocked, v_enabled
          FROM trending_themes WHERE slug = v_slug FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'theme_basket_missing:%', v_slug;
        END IF;
        v_expected := ARRAY(SELECT e FROM jsonb_array_elements_text(v_entry -> 'expected')
                                     WITH ORDINALITY AS t(e, n) ORDER BY n);
        v_new      := ARRAY(SELECT e FROM jsonb_array_elements_text(v_entry -> 'tickers')
                                     WITH ORDINALITY AS t(e, n) ORDER BY n);
        -- Compared sorted, so a reorder in Studio is not a content change. The service
        -- sends `expected` exactly as it read the array (case and duplicates included).
        IF (SELECT COALESCE(array_agg(x ORDER BY x), '{}') FROM unnest(v_current) x)
           IS DISTINCT FROM
           (SELECT COALESCE(array_agg(x ORDER BY x), '{}') FROM unnest(v_expected) x) THEN
            RAISE EXCEPTION 'theme_basket_changed:%', v_slug;
        END IF;
        IF COALESCE(cardinality(v_new), 0) = 0 THEN
            RAISE EXCEPTION 'theme_basket_empty:%', v_slug;
        END IF;
        -- The editor's overrides win too, not only `tickers`: rotation switched off for the
        -- theme, or a stock blocked, while the run was computing → refuse (the run retries
        -- and re-reads them). Blocked entries are compared trimmed and upper-cased, as the
        -- service reads them.
        IF NOT COALESCE(v_enabled, TRUE)
           OR v_new && ARRAY(SELECT upper(btrim(b)) FROM unnest(COALESCE(v_blocked, '{}')) b) THEN
            RAISE EXCEPTION 'theme_basket_changed:%', v_slug;
        END IF;
        UPDATE trending_themes
           SET tickers = v_new, tickers_as_of = p_as_of, updated_at = NOW()
         WHERE slug = v_slug;
    END LOOP;

    UPDATE theme_rotation_runs
       SET status = 'published', published_at = NOW(), finished_at = NOW()
     WHERE id = p_run_id;
    RETURN 'published';
END;
$$;

REVOKE ALL ON FUNCTION public.publish_theme_rotation(UUID, JSONB, DATE) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.publish_theme_rotation(UUID, JSONB, DATE) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.publish_theme_rotation(UUID, JSONB, DATE) TO service_role;

-- RLS + grants: service_role only --------------------------------------------------------
ALTER TABLE public.theme_rotation_runs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.theme_rotation_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.theme_relevance_cache    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.theme_daily_insights     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "theme_rotation_runs_service_all" ON public.theme_rotation_runs;
CREATE POLICY "theme_rotation_runs_service_all" ON public.theme_rotation_runs
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "theme_rotation_decisions_service_all" ON public.theme_rotation_decisions;
CREATE POLICY "theme_rotation_decisions_service_all" ON public.theme_rotation_decisions
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "theme_relevance_cache_service_all" ON public.theme_relevance_cache;
CREATE POLICY "theme_relevance_cache_service_all" ON public.theme_relevance_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "theme_daily_insights_service_all" ON public.theme_daily_insights;
CREATE POLICY "theme_daily_insights_service_all" ON public.theme_daily_insights
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.theme_rotation_runs      FROM anon, authenticated;
REVOKE ALL ON public.theme_rotation_decisions FROM anon, authenticated;
REVOKE ALL ON public.theme_relevance_cache    FROM anon, authenticated;
REVOKE ALL ON public.theme_daily_insights     FROM anon, authenticated;
REVOKE ALL ON public.trending_themes          FROM anon, authenticated;

-- A table with no GRANT is owner-only, not open (169's lesson): grant service_role explicitly.
GRANT ALL ON public.theme_rotation_runs      TO service_role;
GRANT ALL ON public.theme_rotation_decisions TO service_role;
GRANT ALL ON public.theme_relevance_cache    TO service_role;
GRANT ALL ON public.theme_daily_insights     TO service_role;
GRANT ALL ON public.trending_themes          TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.theme_rotation_decisions_id_seq TO service_role;

-- G. Job ledger rows (enabled = the no-deploy kill switch) ----------------------------------
INSERT INTO public.notification_job_state (job, enabled, updated_at)
VALUES ('theme_rotation_monthly', TRUE, NOW()),
       ('theme_insights_daily',   TRUE, NOW())
ON CONFLICT (job) DO NOTHING;

COMMIT;
