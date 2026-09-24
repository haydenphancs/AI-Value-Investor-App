-- 175_trillion_club.sql
--
-- Why: the owner asked (2026-09-23/24) for a new Home section, "Trillion-Dollar Club Bets":
-- what the companies worth $1 trillion or more own in other companies. Five members file SEC
-- Form 13F-HR (NVIDIA, Alphabet, Amazon, AMD, Berkshire); the rest do not, so their stakes —
-- private companies (OpenAI, Anthropic, Scale AI), non-US listings, warrants — come from the
-- companies' own 10-K / 10-Q / 20-F / 8-K filings and official releases, hand-kept by the
-- owner with a primary source and a date on every row. Code: backend/app/services/trillion_club/
-- and backend/app/services/trillion_club_service.py.
--
-- Parts:
--   A. trillion_club_companies — the registry. Identity and editorial controls are hand-kept
--      (Studio); membership (`is_member`, the streak counters, the last close) is written by
--      the daily job from FMP's dated market-cap closes. A 13F is ingested only when the owner
--      sets `use_13f` — joining the club never turns on ingestion by itself (JPMorgan's 13F is
--      a 7,720-row book of CLIENT assets, not the bank's own investments). A company whose cap
--      is entered by hand (Aramco, Samsung: the FMP licence has no FX) must carry an explicit
--      `force_in` / `force_out` — a stale hand-entered number never decides membership alone.
--   B. trillion_club_stakes    — hand-kept stakes outside the 13F. `secondary` (news-only)
--      rows may be stored for the record but can never be published (CHECK). Only `material`
--      rows reach the Home card; the rest show on the company's detail screen.
--   C. trillion_club_filings   — one built 13F snapshot per (CIK, quarter). FMP folds a
--      13F-HR/A amendment into the ORIGINAL quarter with its own accession, so `accessions` is
--      an array and `amended_on` is the latest filing date among them. `period_end` is the
--      "holdings as of" date — never call it the filing date.
--   D. notification_job_state rows for the two jobs (their `enabled` = the no-deploy kill
--      switch).
--
-- Every table is service_role only: the iOS app has no Supabase client, and the 13F rows are
-- FMP-licensed data (End-User Display Rights, auth.md §1a) served only to signed-in users by
-- the backend.
--
-- Idempotent: IF NOT EXISTS on tables and indexes; DROP POLICY IF EXISTS before CREATE;
-- REVOKE/GRANT are declarative; the job rows are ON CONFLICT DO NOTHING.
--
-- VERIFY after applying:
--   SELECT grantee, privilege_type FROM information_schema.role_table_grants
--    WHERE table_name IN ('trillion_club_companies','trillion_club_stakes','trillion_club_filings')
--      AND grantee IN ('anon','authenticated');          -- expect 0 rows

BEGIN;

-- A. Registry --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.trillion_club_companies (
    slug                  TEXT PRIMARY KEY CHECK (slug ~ '^[a-z0-9-]{1,40}$'),
    display_name          TEXT NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 60),
    -- Every SEC CIK the company files under (10 digits, zero-padded). Empty for a company
    -- with no SEC filings at all (Aramco). Stored for NON-filers too, so the weekly probe
    -- can notice a company that starts filing 13Fs. The regex alone had two holes:
    -- array_to_string() SKIPS a NULL element and cannot see a comma INSIDE one, so
    -- '{0009999991,NULL}', '{NULL}' and '{"0009999991,0009999993"}' all passed it. "No NULL
    -- element" and "exactly 10 characters per element" close both.
    ciks                  TEXT[] NOT NULL DEFAULT '{}'
                          CHECK (array_to_string(ciks, ',') ~ '^([0-9]{10}(,[0-9]{10})*)?$'
                                 AND array_position(ciks, NULL) IS NULL
                                 AND char_length(array_to_string(ciks, '')) = 10 * cardinality(ciks)),
    card_kind             TEXT NOT NULL
                          CHECK (card_kind IN ('thirteen_f', 'no_thirteen_f', 'non_us', 'whale_link')),
    use_13f               BOOLEAN NOT NULL DEFAULT FALSE,
    cap_symbol            TEXT,               -- FMP symbol for dated market-cap closes (GOOGL, BRK-B, TSM)
    symbol_aliases        TEXT[] NOT NULL DEFAULT '{}',   -- other share classes (GOOG, BRK-A)
    detail_symbol         TEXT,               -- a routable US ticker; NULL for a non-US home listing
    logo_symbol           TEXT,               -- never a pseudo-ticker
    home_country          TEXT NOT NULL DEFAULT 'US' CHECK (home_country ~ '^[A-Z]{2}$'),
    cap_source            TEXT NOT NULL CHECK (cap_source IN ('fmp_us', 'fmp_adr', 'manual')),
    manual_cap_usd        DOUBLE PRECISION CHECK (manual_cap_usd IS NULL OR manual_cap_usd > 0),
    manual_cap_as_of      DATE,
    manual_cap_source_url TEXT CHECK (manual_cap_source_url IS NULL OR manual_cap_source_url ~ '^https://'),
    manual_fx_rate        DOUBLE PRECISION CHECK (manual_fx_rate IS NULL OR manual_fx_rate > 0),
    manual_fx_source      TEXT,
    membership_mode       TEXT NOT NULL DEFAULT 'auto'
                          CHECK (membership_mode IN ('auto', 'force_in', 'force_out')),
    -- Written by the daily job ------------------------------------------------------
    is_member             BOOLEAN NOT NULL DEFAULT FALSE,
    member_since          DATE,
    last_market_cap       DOUBLE PRECISION CHECK (last_market_cap IS NULL OR last_market_cap > 0),
    last_cap_date         DATE,
    closes_at_or_above    INTEGER NOT NULL DEFAULT 0 CHECK (closes_at_or_above >= 0),
    closes_below          INTEGER NOT NULL DEFAULT 0 CHECK (closes_below >= 0),
    membership_checked_at TIMESTAMPTZ,
    -- Editorial -------------------------------------------------------------------------
    link_whale            BOOLEAN NOT NULL DEFAULT FALSE,     -- Berkshire: link to its whale profile
    published             BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_on           DATE,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- A hand-entered cap never decides membership on its own: it must be an explicit call.
    CONSTRAINT trillion_club_manual_cap_is_explicit CHECK (
        cap_source <> 'manual'
        OR (manual_cap_usd IS NOT NULL AND manual_cap_as_of IS NOT NULL
            AND manual_cap_source_url IS NOT NULL
            AND membership_mode IN ('force_in', 'force_out'))),
    CONSTRAINT trillion_club_fmp_cap_has_symbol CHECK (cap_source = 'manual' OR cap_symbol IS NOT NULL),
    CONSTRAINT trillion_club_13f_needs_cik CHECK (NOT use_13f OR (card_kind = 'thirteen_f' AND cardinality(ciks) >= 1)),
    CONSTRAINT trillion_club_whale_link_kind CHECK (link_whale = (card_kind = 'whale_link'))
);

CREATE INDEX IF NOT EXISTS idx_trillion_club_companies_published
    ON public.trillion_club_companies (published, is_member);

COMMENT ON TABLE public.trillion_club_companies IS
    'Trillion-Dollar Club Bets registry (Home section): hand-kept identity + editorial controls; '
    'membership written daily from dated FMP market-cap closes. 13F ingestion only when use_13f.';

-- B. Hand-kept stakes -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.trillion_club_stakes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_slug        TEXT NOT NULL REFERENCES public.trillion_club_companies (slug)
                        ON DELETE CASCADE ON UPDATE CASCADE,
    kind                TEXT NOT NULL
                        CHECK (kind IN ('private', 'non_us_listed', 'us_listed_off_13f',
                                        'commitment', 'on_13f_note')),
    investee_name       TEXT NOT NULL CHECK (char_length(investee_name) BETWEEN 1 AND 60),
    investee_cusip      TEXT CHECK (investee_cusip IS NULL OR investee_cusip ~ '^[0-9A-Z]{9}$'),
    investee_us_symbol  TEXT,                 -- only when routable to the stock detail screen
    local_listing       TEXT,                 -- display only, e.g. "Taiwan (6770.TW)"
    ownership_pct       DOUBLE PRECISION CHECK (ownership_pct IS NULL OR (ownership_pct > 0 AND ownership_pct <= 100)),
    ownership_basis     TEXT,                 -- e.g. "as-converted", "of Class A"
    disclosed_value_usd DOUBLE PRECISION CHECK (disclosed_value_usd IS NULL OR disclosed_value_usd > 0),
    value_basis         TEXT CHECK (value_basis IS NULL
                                    OR value_basis IN ('carrying_value', 'fair_value', 'invested',
                                                       'committed_up_to')),
    as_of               DATE NOT NULL,
    source_title        TEXT NOT NULL CHECK (char_length(source_title) BETWEEN 1 AND 120),
    source_url          TEXT NOT NULL CHECK (source_url ~ '^https://'),
    source_confidence   TEXT NOT NULL DEFAULT 'primary'
                        CHECK (source_confidence IN ('primary', 'secondary')),
    material            BOOLEAN NOT NULL DEFAULT FALSE,
    tied_to_deal        BOOLEAN NOT NULL DEFAULT FALSE,
    listed_since        DATE,
    background          TEXT CHECK (background IS NULL OR char_length(background) <= 90),
    verified_on         DATE NOT NULL,
    published           BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT trillion_club_stake_value_has_basis CHECK (disclosed_value_usd IS NULL OR value_basis IS NOT NULL),
    CONSTRAINT trillion_club_commitment_basis CHECK (kind <> 'commitment' OR value_basis IS NULL OR value_basis = 'committed_up_to'),
    -- News-only claims may be kept for the record, never shown.
    CONSTRAINT trillion_club_secondary_never_published CHECK (NOT (published AND source_confidence = 'secondary')),
    CONSTRAINT trillion_club_stake_natural_key UNIQUE (company_slug, investee_name, kind)
);

CREATE INDEX IF NOT EXISTS idx_trillion_club_stakes_company
    ON public.trillion_club_stakes (company_slug, published, sort_order);

COMMENT ON TABLE public.trillion_club_stakes IS
    'Trillion-Dollar Club Bets: hand-kept stakes outside the 13F (private, non-US, warrants, '
    'commitments), each with a primary source, as-of date and verified_on. Secondary rows never published.';

-- C. Built 13F snapshots ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.trillion_club_filings (
    cik            TEXT NOT NULL CHECK (cik ~ '^[0-9]{10}$'),
    period         TEXT NOT NULL CHECK (period ~ '^[0-9]{4}-Q[1-4]$'),
    period_end     DATE NOT NULL,
    filed_on       DATE,                    -- earliest filingDate among the accessions
    amended_on     DATE,                    -- latest filingDate when there is more than one accession
    accessions     TEXT[] NOT NULL DEFAULT '{}',
    total_value    DOUBLE PRECISION CHECK (total_value IS NULL OR total_value >= 0),
    position_count INTEGER NOT NULL DEFAULT 0 CHECK (position_count >= 0),
    holdings       JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(holdings) = 'array'),
    changes        JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(changes) = 'object'),
    excluded_rows  INTEGER NOT NULL DEFAULT 0 CHECK (excluded_rows >= 0),
    -- {cusip: first_seen ISO date} for rows that never resolved to a symbol; the builder stops
    -- retrying one after 7 days (a terminal state, not a daily degraded rebuild).
    unresolved     JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(unresolved) = 'object'),
    raw_hash       TEXT NOT NULL,
    build_status   TEXT NOT NULL CHECK (build_status IN ('complete', 'degraded')),
    source         TEXT NOT NULL DEFAULT 'fmp' CHECK (source IN ('fmp', 'edgar')),
    built_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cik, period)
);

CREATE INDEX IF NOT EXISTS idx_trillion_club_filings_recent
    ON public.trillion_club_filings (cik, period DESC);

COMMENT ON TABLE public.trillion_club_filings IS
    'Trillion-Dollar Club Bets: one built 13F snapshot per (CIK, quarter) — holdings + '
    'quarter-over-quarter share changes. accessions[] because FMP folds 13F-HR/A into the original quarter.';

-- RLS + grants: service_role only --------------------------------------------------------
ALTER TABLE public.trillion_club_companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trillion_club_stakes    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trillion_club_filings   ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "trillion_club_companies_service_all" ON public.trillion_club_companies;
CREATE POLICY "trillion_club_companies_service_all" ON public.trillion_club_companies
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "trillion_club_stakes_service_all" ON public.trillion_club_stakes;
CREATE POLICY "trillion_club_stakes_service_all" ON public.trillion_club_stakes
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "trillion_club_filings_service_all" ON public.trillion_club_filings;
CREATE POLICY "trillion_club_filings_service_all" ON public.trillion_club_filings
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.trillion_club_companies FROM anon, authenticated;
REVOKE ALL ON public.trillion_club_stakes    FROM anon, authenticated;
REVOKE ALL ON public.trillion_club_filings   FROM anon, authenticated;

-- A table with no GRANT is owner-only, not open (169's lesson): grant service_role explicitly.
GRANT ALL ON public.trillion_club_companies TO service_role;
GRANT ALL ON public.trillion_club_stakes    TO service_role;
GRANT ALL ON public.trillion_club_filings   TO service_role;

-- D. Job ledger rows (enabled = the no-deploy kill switch) ----------------------------------
INSERT INTO public.notification_job_state (job, enabled, updated_at)
VALUES ('trillion_club_daily',  TRUE, NOW()),
       ('trillion_club_weekly', TRUE, NOW())
ON CONFLICT (job) DO NOTHING;

COMMIT;
