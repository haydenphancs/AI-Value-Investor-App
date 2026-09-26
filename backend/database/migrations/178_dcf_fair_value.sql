-- 178_dcf_fair_value.sql
--
-- Why: the Caydex Fair Value Estimate (a 2-stage FCFE DCF, model `dcf-v1`,
-- documents/research/dcf-methodology-v1.md) replaces the report's dead "Analyst Price Target"
-- block and, later, FMP's own DCF on the Analysis tab. It needs two tables:
--
--   dcf_fair_value_cache    Tier 2 of the usual cache-aside pair (24 h), one row per ticker,
--                           keyed with the model version so a model change is a miss, not a
--                           stale hit.
--   dcf_fair_value_history  APPEND-ONLY: one row per (ticker, ET date, model version) with
--                           every input the value depended on. FMP keeps no history of the
--                           analyst consensus, so this table is the ONLY record of what the
--                           forecast was on a given day, and the evidence of exactly what was
--                           published. Rows are never updated or deleted by the app; the
--                           service upserts with ignore-duplicates, so a second computation on
--                           the same day is a no-op.
--
-- 🔒 Both tables are GLOBAL: one value per (ticker, date, model version) for every caller. No
-- user id, tier or persona column may ever be added — the value must stay impersonal (the US
-- publisher exclusion depends on it; hard rule 1 in the spec).
--
-- ⚠️ SERVICE-ROLE ONLY, like 162/175: the rows hold FMP-derived statements and consensus, and
-- the anon key ships in the iOS binary. iOS reaches the value only through the backend.
--
-- Deploy order does not matter: the service reads and writes these tables only when
-- settings.DCF_ENABLED is true, and wraps every call in try/except.

CREATE TABLE IF NOT EXISTS public.dcf_fair_value_cache (
    ticker        TEXT        PRIMARY KEY,
    model_version TEXT        NOT NULL,
    response_json JSONB       NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.dcf_fair_value_cache IS
    'Tier-2 cache (24 h) of the Caydex Fair Value Estimate (DCF). One row per ticker; a row whose '
    'model_version differs from the running model is ignored. Global and impersonal: no per-user '
    'column may exist. Written and read by app/services/dcf_fair_value_service.py.';

CREATE TABLE IF NOT EXISTS public.dcf_fair_value_history (
    id                BIGSERIAL   PRIMARY KEY,
    ticker            TEXT        NOT NULL,
    as_of_date        DATE        NOT NULL,
    model_version     TEXT        NOT NULL,
    status            TEXT        NOT NULL CHECK (status IN ('ok', 'refused')),
    refusal_code      TEXT,
    fair_value        DOUBLE PRECISION,
    range_low         DOUBLE PRECISION,
    range_high        DOUBLE PRECISION,
    alternative_value DOUBLE PRECISION,
    price             DOUBLE PRECISION,
    inputs            JSONB       NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, as_of_date, model_version),
    CHECK ((status = 'ok') = (fair_value IS NOT NULL)),
    CHECK ((status = 'refused') = (refusal_code IS NOT NULL))
);

COMMENT ON TABLE public.dcf_fair_value_history IS
    'Append-only record of every Caydex Fair Value Estimate computed: one row per (ticker, ET '
    'date, model version) with the inputs it depended on. FMP keeps no consensus history, so this '
    'is the only record of what the forecast was on a given day, and of what was published. '
    'Never updated or deleted by the app. Global and impersonal: no per-user column may exist.';

COMMENT ON COLUMN public.dcf_fair_value_history.as_of_date IS
    'ET calendar date of the computation (one row per ticker per day per model version).';
COMMENT ON COLUMN public.dcf_fair_value_history.price IS
    'Share price when computed, for later analysis only. The value itself does not use it.';
COMMENT ON COLUMN public.dcf_fair_value_history.inputs IS
    'Every input: rates, beta, shares, debt, the 5 fiscal years used, the consensus rows.';

-- Per-ticker time series reads (the replay, the live watch, a "why did it change" answer).
CREATE INDEX IF NOT EXISTS idx_dcf_fair_value_history_ticker_date
    ON public.dcf_fair_value_history (ticker, as_of_date DESC);

ALTER TABLE public.dcf_fair_value_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dcf_fair_value_history ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "dcf_fair_value_cache_service_all" ON public.dcf_fair_value_cache;
CREATE POLICY "dcf_fair_value_cache_service_all" ON public.dcf_fair_value_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "dcf_fair_value_history_service_all" ON public.dcf_fair_value_history;
CREATE POLICY "dcf_fair_value_history_service_all" ON public.dcf_fair_value_history
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.dcf_fair_value_cache FROM anon, authenticated;
REVOKE ALL ON public.dcf_fair_value_history FROM anon, authenticated;
GRANT ALL ON public.dcf_fair_value_cache TO service_role;
GRANT ALL ON public.dcf_fair_value_history TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.dcf_fair_value_history_id_seq TO service_role;
