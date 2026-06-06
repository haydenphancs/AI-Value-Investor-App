-- 059_geopolitical_macro_cache.sql
--
-- Why: the Macro module's geopolitical/regulatory risk factors were emitted
-- UNGROUNDED by Stage A (the AI's training-knowledge guess), so they rendered
-- "Data unavailable" with a meaningless impact %. They should instead capture
-- REAL current events — active wars, trade wars / tariffs / sanctions, oil &
-- energy shocks, pandemics — via Gemini web-search grounding (the same engine
-- behind price_catalyst / competitor_intel / moat_intel).
--
-- These events are MARKET-WIDE (identical for every ticker) and PERSISTENT (a
-- war runs for months), so this is ONE shared, on-demand scan with a long
-- (~7-day) cache — not a per-ticker call and not a daily job. Sector relevance
-- is applied downstream via the existing sector-β in _compute_macro_threat.
--
--   * geopolitical_macro_cache — single shared row (scope='global'), ~7-day
--     serving cache. `factors` is the grounded factor list WITH source
--     citations inline. Stale-while-revalidate + keep-last-good live in the
--     service; this table just stores the current best list + expiry.
--   * geopolitical_macro_audit — append-only, permanent. Persists the grounded
--     factor list + SOURCE CITATIONS for a future report-detail PDF. Citations
--     are stored here (and on the report payload), NOT shown in the report view.
--
-- Mirrors 058_price_catalyst.sql (cache + audit + RLS + grants), but keyed on
-- a market-wide `scope` instead of `ticker`.

-- ── Serving cache (one shared row; ~7-day TTL) ────────────────────────
CREATE TABLE IF NOT EXISTS public.geopolitical_macro_cache (
    scope         TEXT PRIMARY KEY DEFAULT 'global',  -- single market-wide row
    factors       JSONB NOT NULL DEFAULT '[]'::jsonb, -- [{category,title,description,severity,trend,risk_group,sources:[{title,uri,publisher}]}]
    model_version TEXT,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_geopolitical_macro_cache_expires
    ON public.geopolitical_macro_cache(expires_at);

ALTER TABLE public.geopolitical_macro_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "geopolitical_macro_cache_service_write" ON public.geopolitical_macro_cache;
DROP POLICY IF EXISTS "geopolitical_macro_cache_public_read"   ON public.geopolitical_macro_cache;

CREATE POLICY "geopolitical_macro_cache_service_write" ON public.geopolitical_macro_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "geopolitical_macro_cache_public_read" ON public.geopolitical_macro_cache
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.geopolitical_macro_cache TO anon, authenticated;
GRANT ALL    ON public.geopolitical_macro_cache TO service_role;

-- ── Audit log (append-only, permanent — the citation store for the PDF) ─
CREATE TABLE IF NOT EXISTS public.geopolitical_macro_audit (
    id            BIGSERIAL PRIMARY KEY,
    run_id        UUID NOT NULL,
    status        TEXT NOT NULL CHECK (status IN (
        'applied',              -- grounded factors + sources written
        'no_factors',           -- grounded but no material events found
        'kept_last_good',       -- refresh empty/degraded; previous list retained
        'gemini_error',         -- grounded call threw / unparseable
        'skipped_kill_switch'   -- GEOPOLITICAL_INTEL_AI_ENABLED = false
    )),
    factor_count  INTEGER,
    factors       JSONB,                        -- full grounded list (for the PDF)
    raw_response  JSONB,                        -- raw grounded text for debugging
    search_queries TEXT[],
    tokens_used   INTEGER,
    model_version TEXT,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_geopolitical_macro_audit_run_id
    ON public.geopolitical_macro_audit(run_id);

CREATE INDEX IF NOT EXISTS idx_geopolitical_macro_audit_computed
    ON public.geopolitical_macro_audit(computed_at DESC);

ALTER TABLE public.geopolitical_macro_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "geopolitical_macro_audit_service_all" ON public.geopolitical_macro_audit;

-- Internal/back-office only (feeds the future PDF) — no anon/authenticated read.
CREATE POLICY "geopolitical_macro_audit_service_all" ON public.geopolitical_macro_audit
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT ALL ON public.geopolitical_macro_audit TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.geopolitical_macro_audit_id_seq TO service_role;
