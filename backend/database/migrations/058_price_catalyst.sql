-- 058_price_catalyst.sql
--
-- Why: the FMP-news keyword catalyst detector behind "Recent Price Movement"
-- was measured (backend/scripts/eval_price_catalyst.py, 60 big-move cases) at
-- 43% coverage / 35% precision, and lost 0-55 head-to-head vs a Gemini
-- web-search oracle (it misses or mislabels earnings, the #1 catalyst, and
-- can't see untyped catalysts like a capital raise). So the primary "why did
-- it move" reason for BIG moves (|z| >= 1) now comes from a Gemini
-- web-search-grounded call (price_catalyst_service), gated + cached.
--
--   * price_catalyst_cache — 24h serving cache (a move's reason is fresh daily).
--   * price_catalyst_audit — append-only, permanent. Persists the grounded
--     reason + SOURCE CITATIONS for a future report-detail PDF. Citations are
--     stored here, NOT shown in the report view.
--
-- Mirrors 055_moat_intel.sql (cache + audit + RLS + grants).

-- ── Serving cache (24h TTL) ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.price_catalyst_cache (
    ticker        TEXT PRIMARY KEY,
    tag           TEXT,                         -- catalyst label for the badge
    reason        TEXT,                         -- one-sentence grounded reason
    sources       JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{title, uri, publisher}]
    model_version TEXT,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_catalyst_cache_expires
    ON public.price_catalyst_cache(expires_at);

ALTER TABLE public.price_catalyst_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "price_catalyst_cache_service_write" ON public.price_catalyst_cache;
DROP POLICY IF EXISTS "price_catalyst_cache_public_read"   ON public.price_catalyst_cache;

CREATE POLICY "price_catalyst_cache_service_write" ON public.price_catalyst_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY "price_catalyst_cache_public_read" ON public.price_catalyst_cache
    FOR SELECT TO anon, authenticated USING (true);

GRANT SELECT ON public.price_catalyst_cache TO anon, authenticated;
GRANT ALL    ON public.price_catalyst_cache TO service_role;

-- ── Audit log (append-only, permanent — the citation store for the PDF) ─
CREATE TABLE IF NOT EXISTS public.price_catalyst_audit (
    id            BIGSERIAL PRIMARY KEY,
    run_id        UUID NOT NULL,
    ticker        TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN (
        'applied',              -- grounded catalyst + sources written
        'no_catalyst',          -- grounded but no clear company-specific cause
        'gemini_error',         -- grounded call threw / unparseable
        'skipped_kill_switch'   -- PRICE_CATALYST_AI_ENABLED = false
    )),
    change_pct    DOUBLE PRECISION,             -- the move being explained
    window_label  TEXT,
    tag           TEXT,
    reason        TEXT,
    sources       JSONB,                        -- full citation list (for the PDF)
    raw_response  JSONB,                        -- raw grounded text for debugging
    search_queries TEXT[],
    tokens_used   INTEGER,
    model_version TEXT,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_catalyst_audit_ticker
    ON public.price_catalyst_audit(ticker, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_price_catalyst_audit_run_id
    ON public.price_catalyst_audit(run_id);

ALTER TABLE public.price_catalyst_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "price_catalyst_audit_service_all" ON public.price_catalyst_audit;

-- Internal/back-office only (feeds the future PDF) — no anon/authenticated read.
CREATE POLICY "price_catalyst_audit_service_all" ON public.price_catalyst_audit
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT ALL ON public.price_catalyst_audit TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.price_catalyst_audit_id_seq TO service_role;
