-- 159_corporate_action_cache.sql
--
-- Why: FMP's `/splits` is in the "Market Calendar" package, which is NOT on the signed
-- Order Form, and answers 402 since enforcement (2026-09-03). Splits are now DERIVED by
-- `app/services/corporate_actions_service.py` from two entitled price series
-- (`historical-price-eod/full` vs `/non-split-adjusted`). That costs two calls of roughly
-- 24 KB each instead of one small `/splits` call, and the 13F hydration jobs run it across
-- the whole universe — so it needs a durable tier, not just the in-process dict.
--
-- Why a cache table is legitimate here, when `price_service` refuses one:
--   `price_service` must NEVER persist a value derived from a live price
--   (`project_etf_index_cache_decomposition`). This table does not. A split factor for a
--   CLOSED quarter is immutable. FMP restates its adjusted series after every corporate
--   action, so the factor `f[d]` for an old date DOES change when a new split happens —
--   but the RATIO between two consecutive days inside a closed window does not, because
--   any later rescaling multiplies both sides equally. That invariance is the whole reason
--   this is cacheable, and it is why the service writes ONLY windows that end in the past.
--
-- Schema: (symbol, kind, from_date, to_date) unique; JSONB event list; no expiry column —
-- a closed window never goes stale. `kind` distinguishes the split derivation from the
-- ex-dividend-date derivation, which reads a different second series.
--
-- RLS: service-role only, deliberately NOT the public-read `*_cache` template. This is
-- FMP-derived vendor data and the Supabase anon key ships inside the iOS binary; public
-- read would be redistribution under FMP ToS 2.6.1. Same posture as 157/158.
--
-- Deploy order does not matter: the service try/excepts both the read and the write, so
-- before this migration is applied it simply runs on the in-memory tier alone.

CREATE TABLE IF NOT EXISTS public.corporate_action_cache (
    symbol      TEXT        NOT NULL,
    kind        TEXT        NOT NULL,
    from_date   DATE        NOT NULL,
    to_date     DATE        NOT NULL,
    events      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, kind, from_date, to_date)
);

COMMENT ON TABLE public.corporate_action_cache IS
    'Stock splits and ex-dividend dates derived from entitled FMP price series, because '
    '/splits and /dividends are outside the signed Order Form and answer 402. Rows cover a '
    'CLOSED date window only: the adjustment-factor ratio inside a finished window is '
    'invariant under FMP''s later restatements, so such a row never needs invalidating. '
    'Written by app/services/corporate_actions_service.py; read by whale_service, '
    'holders_service and the two 13F hydration scripts.';

COMMENT ON COLUMN public.corporate_action_cache.kind IS
    '"split" (derived vs /non-split-adjusted) or "dividend" (vs /dividend-adjusted).';
COMMENT ON COLUMN public.corporate_action_cache.events IS
    'JSONB array of {date, observed, numerator, denominator}. numerator/denominator are '
    'NULL for an adjustment that is not a nameable split ratio — a spin-off, or a reverse '
    'split outside the classifier range. An empty array is a MEANINGFUL result ("no split '
    'in this window") and is the answer to the large majority of queries.';

CREATE INDEX IF NOT EXISTS idx_corporate_action_cache_symbol
    ON public.corporate_action_cache (symbol, kind);

ALTER TABLE public.corporate_action_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "corporate_action_cache_service_all" ON public.corporate_action_cache;
CREATE POLICY "corporate_action_cache_service_all" ON public.corporate_action_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.corporate_action_cache FROM anon, authenticated;
GRANT ALL ON public.corporate_action_cache TO service_role;
