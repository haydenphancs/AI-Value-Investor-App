-- 205_trillion_club_stake_finder.sql
--
-- Why: the owner asked (2026-10-27) for Trillion-Dollar Club Bets to find new stakes on its own
-- — "so next time, it can automatically find and add. i don't want to add them like a hard
-- code." — and chose the go-live on 2026-10-28 ("Tiered"): a member's own plain-share Schedule
-- 13D/13G auto-publishes with a Withdraw / Keep note in Telegram; every other SEC find (8-K
-- closings, signed agreements, warrant / preferred / convertible positions, unlisted issuers,
-- changes to published rows) arrives as a pre-filled one-tap Publish / Dismiss proposal; an
-- indication of interest or a newer filing for a pair that already has a row is one line in a
-- daily digest and never a row. Until now every stake came from the owner-run seed
-- (backend/data/trillion_club_seed.json → backend/scripts/seed_trillion_club.py) and nothing
-- found new deals. The plan is ~/.claude/plans/eventual-prancing-pebble.md (v3); the code is
-- backend/app/services/trillion_club/stake_*.py, whose shared vocabulary
-- (backend/app/services/trillion_club/stake_contracts.py: ORIGINS, FINDING_SOURCES,
-- FINDING_OUTCOMES, FINDING_TIERS, FINDING_STATUSES, OWNER_DECISIONS, REASON_CODES) every CHECK
-- list below must equal, value for value and in order — pinned by
-- backend/tests/test_trillion_club_stake_migration.py.
--
-- Parts:
--   A. trillion_club_stakes — who wrote each row, and what a machine-written row may claim:
--      * `origin`: 'seed' (the owner's seed; the DEFAULT backfills every existing row), 'auto'
--        (published by the finder with no tap) or 'proposal' (published by the owner's Publish
--        tap). The seed script lists machine rows apart and never treats one as an orphan or
--        as drift.
--      * `finding_id` (the ledger row that made it — set exactly when origin is auto/proposal),
--        `investee_cik` (the issuer's SEC CIK, the strongest identity key), `owner_kept_at`
--        (the owner tapped Keep on an auto row) and `withdrawn_at` (the owner tapped Withdraw, or
--        the proposal was dismissed or expired: a tombstone — such a row is never published again).
--      * A machine row cites an SEC Archives URL, is primary, carries no background line and
--        is never tied to a deal. An auto row the owner has not kept is the phase-1 auto scope
--        only: not material, kind on_13f_note / us_listed_off_13f, no disclosed value. The 8-K
--        closing tier (an owner-approved follow-up after a reviewed trial) relaxes that in its
--        own migration.
--      The five rules are named constraints (DROP CONSTRAINT IF EXISTS + ADD), and every rule
--      holds on every seed row (a seed row is 'seed', so only the finding_id, withdrawn_at and
--      owner_kept_at rules bind it).
--   B. trillion_club_stake_findings — the finder's ledger, one row per (member, SEC accession).
--      UNIQUE (company_slug, accession) is the exactly-once claim: a filing is classified once,
--      and a (slug, accession) whose finding is terminal is never classified again. It holds the
--      classification (tier, outcome, reason), the evidence (verified spans), the proposed stake
--      row and the review state (the Telegram message, the owner's decision). `stake_id` is ON
--      DELETE SET NULL and NO CHECK requires it on any status: the owner deleting a stake row in
--      Studio must always succeed (the next reconcile records that pair as withdrawn,
--      deleted_in_studio — a tombstone — plus one heads-up).
--   C. notification_job_state row `trillion_club_stake_finder` (its `enabled` = the no-deploy
--      stop for new finds).
--
-- service_role only (the 162 template): the iOS app has no Supabase client; only the backend
-- reads the stakes and only the finder writes the ledger.
--
-- ⚠️ Apply BEFORE deploying the backend that reads `origin`: the marketing stakes adapter selects
-- and filters on it, and against a pre-205 table that read answers 42703 / PGRST204, so the
-- company_stakes series stops (the seed script alone falls back to its 175 select with a
-- WARNING). Apply it too before the next manual-cap seed refresh run with the new seed script.
-- Nothing writes the new columns or the ledger while TRILLION_CLUB_STAKE_FINDER_ENABLED is False
-- (the default).
--
-- Valves that need no deploy:
--   UPDATE public.notification_job_state SET enabled = false WHERE job = 'trillion_club_stake_finder';
--     — stops new finds;
--   UPDATE public.trillion_club_stakes SET published = false, withdrawn_at = now()
--    WHERE origin IN ('auto', 'proposal');
--     — hides every machine-written row from both readers (use it before any code rollback).
--
-- Idempotent: IF NOT EXISTS on the table, the columns and the indexes (an inline CHECK rides its
-- ADD COLUMN, so a replay is a no-op); every named constraint is DROP CONSTRAINT IF EXISTS + ADD;
-- DROP POLICY IF EXISTS before CREATE; REVOKE / GRANT are declarative; the job row is ON CONFLICT
-- DO NOTHING, so a replay never re-enables a job the owner stopped. Safe to re-run. One
-- transaction: a refused constraint leaves nothing half-applied.

BEGIN;

-- ── A. trillion_club_stakes ──────────────────────────────────────────────────────────────
-- The inline CHECKs carry the names Postgres would generate, so a later migration can DROP them
-- by a name that is greppable here.
ALTER TABLE public.trillion_club_stakes
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'seed'
        CONSTRAINT trillion_club_stakes_origin_check
        CHECK (origin IN ('seed', 'auto', 'proposal')),
    ADD COLUMN IF NOT EXISTS owner_kept_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMPTZ,
    -- SEC's 10-digit, zero-padded CIK (app/integrations/sec_edgar.normalize_cik: never zero).
    ADD COLUMN IF NOT EXISTS investee_cik TEXT
        CONSTRAINT trillion_club_stakes_investee_cik_check
        CHECK (investee_cik ~ '^[0-9]{10}$' AND investee_cik <> '0000000000'),
    -- Soft pointer to trillion_club_stake_findings.id (no FK: the ledger's stake_id FK is the
    -- enforced edge, and a circular FK would make re-applying this migration non-idempotent).
    ADD COLUMN IF NOT EXISTS finding_id UUID;

-- A row has a finding exactly when the finder wrote it.
ALTER TABLE public.trillion_club_stakes
    DROP CONSTRAINT IF EXISTS trillion_club_stake_finding_iff_machine;
ALTER TABLE public.trillion_club_stakes
    ADD CONSTRAINT trillion_club_stake_finding_iff_machine
    CHECK ((finding_id IS NOT NULL) = (origin IN ('auto', 'proposal')));

-- Withdrawn is a tombstone: it can never be live (any origin — Withdraw itself only ever
-- touches machine rows, and the seed script clears withdrawn_at when it adopts one).
ALTER TABLE public.trillion_club_stakes
    DROP CONSTRAINT IF EXISTS trillion_club_stake_withdrawn_never_published;
ALTER TABLE public.trillion_club_stakes
    ADD CONSTRAINT trillion_club_stake_withdrawn_never_published
    CHECK (NOT (published AND withdrawn_at IS NOT NULL));

-- What any machine row may claim: an SEC filing as its source, no background line (that is
-- the owner's prose), primary, never tied to a deal.
ALTER TABLE public.trillion_club_stakes
    DROP CONSTRAINT IF EXISTS trillion_club_stake_machine_shape;
ALTER TABLE public.trillion_club_stakes
    ADD CONSTRAINT trillion_club_stake_machine_shape
    CHECK (origin NOT IN ('auto', 'proposal')
           OR (background IS NULL
               AND NOT tied_to_deal
               AND source_confidence = 'primary'
               AND source_url ~ '^https://www\.sec\.gov/Archives/edgar/data/'));

-- The phase-1 auto scope (a member's own plain-share 13D/13G): while the owner has not kept
-- it, an auto row is not material, is a 13F note or an off-13F U.S. listing, and states no value.
ALTER TABLE public.trillion_club_stakes
    DROP CONSTRAINT IF EXISTS trillion_club_stake_auto_scope;
ALTER TABLE public.trillion_club_stakes
    ADD CONSTRAINT trillion_club_stake_auto_scope
    CHECK (origin <> 'auto'
           OR owner_kept_at IS NOT NULL
           OR (NOT material
               AND kind IN ('on_13f_note', 'us_listed_off_13f')
               AND disclosed_value_usd IS NULL
               AND value_basis IS NULL));

-- Keep is an answer to an auto row's note; no other row can carry it (an auto row the finder
-- turns into a proposal must clear it in the same write).
ALTER TABLE public.trillion_club_stakes
    DROP CONSTRAINT IF EXISTS trillion_club_stake_kept_only_auto;
ALTER TABLE public.trillion_club_stakes
    ADD CONSTRAINT trillion_club_stake_kept_only_auto
    CHECK (owner_kept_at IS NULL OR origin = 'auto');

COMMENT ON TABLE public.trillion_club_stakes IS
    'Trillion-Dollar Club Bets: stakes outside the 13F (private, non-US, warrants, commitments), '
    'hand-kept, or found automatically from SEC filings (migration 205: origin seed | auto | '
    'proposal), each with a primary source, as-of date and verified_on. Secondary rows never '
    'published; a withdrawn row (withdrawn_at) never again.';

COMMENT ON COLUMN public.trillion_club_stakes.origin IS
    'seed = hand-kept (written by scripts/seed_trillion_club.py; every pre-205 row); auto = '
    'published by the stake finder with no owner tap (a member''s own plain-share 13D/13G); '
    'proposal = built by the finder and published by the owner''s Publish tap. Migration 205.';
COMMENT ON COLUMN public.trillion_club_stakes.finding_id IS
    'The trillion_club_stake_findings row that wrote this stake: set exactly when origin is auto '
    'or proposal (CHECK). A soft pointer — the ledger''s stake_id FK is the enforced edge.';
COMMENT ON COLUMN public.trillion_club_stakes.investee_cik IS
    'The investee''s SEC CIK (10 digits, never zero) when known — the first identity key the '
    'finder''s dedupe matches on, before the CUSIP-6 and the symbol.';
COMMENT ON COLUMN public.trillion_club_stakes.owner_kept_at IS
    'When the owner tapped Keep on an auto row''s note (auto rows only, CHECK). A kept row is no '
    'longer held to the phase-1 auto scope (it may be made material or carry a value); the '
    'machine-row rules still apply.';
COMMENT ON COLUMN public.trillion_club_stakes.withdrawn_at IS
    'When the owner tapped Withdraw, or the proposal was dismissed or expired: a tombstone. A '
    'withdrawn row is never published (CHECK) and the finder never adds the pair again.';

-- ── B. trillion_club_stake_findings ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.trillion_club_stake_findings (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The member whose stake this is.
    company_slug         TEXT        NOT NULL REFERENCES public.trillion_club_companies (slug)
                                     ON DELETE CASCADE ON UPDATE CASCADE,
    -- sch13 = the member's own Schedule 13D/13G (from its submissions); efts = another
    -- company's filing found through EDGAR full-text search.
    source               TEXT        NOT NULL,
    -- The filing, as SEC numbers it (sec_edgar.normalize_accession's dashed form).
    accession            TEXT        NOT NULL,
    filer_cik            TEXT        NOT NULL,
    investee_cik         TEXT,
    form                 TEXT        NOT NULL,
    filed_on             DATE        NOT NULL,
    document             TEXT        NOT NULL,
    source_url           TEXT        NOT NULL,
    -- The classification.
    tier                 TEXT        NOT NULL,
    outcome              TEXT        NOT NULL,
    reason               TEXT        NOT NULL,
    -- The evidence and the row it supports.
    proposed_row         JSONB,
    verified_spans       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    fact_sha             TEXT,
    stake_id             UUID        REFERENCES public.trillion_club_stakes (id) ON DELETE SET NULL,
    -- The lifecycle and the review.
    status               TEXT        NOT NULL DEFAULT 'new',
    telegram_message_id  BIGINT,
    notified_at          TIMESTAMPTZ,
    -- When the row went live (every move to 'published'); the auto-publish caps count by it.
    published_at         TIMESTAMPTZ,
    owner_decision       TEXT,
    decided_at           TIMESTAMPTZ,
    attempts             INTEGER     NOT NULL DEFAULT 0,
    last_error           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- The exactly-once claim: one finding per (member, filing).
    CONSTRAINT trillion_club_stake_findings_company_slug_accession_key
        UNIQUE (company_slug, accession),
    CONSTRAINT trillion_club_stake_findings_source_check
        CHECK (source IN ('sch13', 'efts')),
    CONSTRAINT trillion_club_stake_findings_tier_check
        CHECK (tier IN ('13dg', 'sec_closing', 'sec_agreement', 'ioi', 'directed_issuance',
                        'instrument', 'update', 'other')),
    CONSTRAINT trillion_club_stake_findings_outcome_check
        CHECK (outcome IN ('auto', 'proposal', 'heads_up', 'skipped')),
    CONSTRAINT trillion_club_stake_findings_reason_check
        CHECK (reason IN ('plain_13dg', 'about_member', 'legacy_schedule', 'schedule_unparseable',
                          'member_line_missing', 'member_lines_disagree', 'reporting_type',
                          'group_member', 'shared_power', 'no_dispositive_power',
                          'ownership_disclaimed',
                          'derivative_position', 'class_unmapped',
                          'percent_above_50', 'exit_filing', 'cusip_invalid', 'no_us_listing',
                          'ambiguous_listing', 't1_closing', 't2_agreement', 't3_indication',
                          't4_directed', 'instrument', 'member_filed', 'multi_cik',
                          'exhibit_skipped', 'commercial_only', 'co_investor_only', 'no_template',
                          'too_large', 's2_name_mismatch', 'name_unusable', 'copy_rule',
                          'person_name', 'dates_invalid', 'too_old', 'existing_row',
                          'possible_match', 'tombstone', 'open_proposal', 'validator',
                          'volume_cap', 'breaker', 'tier_off', 'before_auto_since', 'trial',
                          'not_enabled', 'fetch_failed', 'update', 'exit', 'deleted_in_studio',
                          'recheck_negative')),
    -- Never a rule tying stake_id to a status: SET NULL (a Studio delete) must always succeed.
    CONSTRAINT trillion_club_stake_findings_status_check
        CHECK (status IN ('new', 'staged', 'noted', 'published', 'kept', 'withdrawn', 'proposed',
                          'publishing', 'dismissed', 'expired', 'superseded', 'closed',
                          'reported', 'skipped', 'error')),
    CONSTRAINT trillion_club_stake_findings_decision_check
        CHECK (owner_decision IS NULL
               OR owner_decision IN ('kept', 'withdrawn', 'published', 'dismissed',
                                     'deleted_in_studio')),
    -- The breaker counts the owner's Withdraws BY decided_at: a decision always carries its time.
    CONSTRAINT trillion_club_stake_findings_decision_dated
        CHECK ((owner_decision IS NULL) = (decided_at IS NULL)),
    CONSTRAINT trillion_club_stake_findings_accession_check
        CHECK (accession ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
    CONSTRAINT trillion_club_stake_findings_cik_check
        CHECK (filer_cik ~ '^[0-9]{10}$' AND filer_cik <> '0000000000'
               AND (investee_cik IS NULL
                    OR (investee_cik ~ '^[0-9]{10}$' AND investee_cik <> '0000000000'))),
    -- One constraint, CASE-guarded: jsonb_array_length raises on a non-array, so the length may
    -- only be read once the type is known (AND does not fix the evaluation order).
    CONSTRAINT trillion_club_stake_findings_json_shapes
        CHECK ((proposed_row IS NULL
                OR (jsonb_typeof(proposed_row) = 'object'
                    AND octet_length(proposed_row::text) <= 16384))
               AND CASE WHEN jsonb_typeof(verified_spans) = 'array'
                        THEN jsonb_array_length(verified_spans) <= 100
                             AND octet_length(verified_spans::text) <= 65536
                        ELSE FALSE
                   END),
    CONSTRAINT trillion_club_stake_findings_sha_check
        CHECK (fact_sha IS NULL OR fact_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT trillion_club_stake_findings_text_caps
        CHECK (char_length(form) BETWEEN 1 AND 40
               AND char_length(document) BETWEEN 1 AND 255
               AND source_url ~ '^https://' AND char_length(source_url) <= 500
               AND (last_error IS NULL OR char_length(last_error) <= 2000)),
    CONSTRAINT trillion_club_stake_findings_ranges
        CHECK (attempts >= 0 AND (telegram_message_id IS NULL OR telegram_message_id > 0))
);

-- The every-run sweeps (reconcile, the digest, proposal expiry) read by status, oldest first.
CREATE INDEX IF NOT EXISTS idx_trillion_club_stake_findings_status_notified
    ON public.trillion_club_stake_findings (status, notified_at);
-- The identity dedupe and the tombstone check read a member's findings by investee.
CREATE INDEX IF NOT EXISTS idx_trillion_club_stake_findings_company_investee
    ON public.trillion_club_stake_findings (company_slug, investee_cik);

COMMENT ON TABLE public.trillion_club_stake_findings IS
    'Trillion Club stake finder (migration 205): one row per (member, SEC accession) — the '
    'exactly-once claim on a filing (UNIQUE), its classification (tier, outcome auto | proposal | '
    'heads_up | skipped, reason), the verified evidence and the proposed stake row, and the '
    'Telegram review state (message, owner decision). A terminal finding is never classified '
    'again; a withdrawn, dismissed or expired one is a tombstone for its pair. stake_id is ON '
    'DELETE SET NULL and no CHECK requires it, so a Studio delete of a stake always succeeds. '
    'Written only by the stake finder (app/services/trillion_club/stake_*.py); service_role only.';

COMMENT ON COLUMN public.trillion_club_stake_findings.status IS
    'auto: new → staged → noted → published (→ kept | withdrawn | closed); proposal: new → '
    'proposed → publishing → published (→ withdrawn), or → dismissed | expired; heads-up: new → '
    'reported; also skipped, superseded and error (read again once per ET day while attempts < 3, '
    'then a fetch_failed heads-up). '
    'Terminal statuses are stake_contracts.TERMINAL_STATUSES. Every change is a compare-and-swap '
    'on the observed status.';
COMMENT ON COLUMN public.trillion_club_stake_findings.reason IS
    'Why it got its outcome — a stake_contracts.REASONS code (closed set, CHECK); the digest text '
    'for each code lives there too.';
COMMENT ON COLUMN public.trillion_club_stake_findings.proposed_row IS
    'The pre-filled trillion_club_stakes row (a JSON object of its columns) for an auto or '
    'proposal outcome. A heads-up or skipped finding holds only underscore-prefixed digest keys '
    '(_investee, _detail, _t3, _old) so its digest line survives a failed send — and an exit filing '
    '(the member reporting 5% or less) its event date (_event), against which an older filing of '
    'the pair is refused; those are never read as a stake row. At most 16 KiB.';
COMMENT ON COLUMN public.trillion_club_stake_findings.verified_spans IS
    'The filing sentences the facts were read from, each verified against the full document text '
    '— a JSON array of at most 100 entries and 64 KiB.';
COMMENT ON COLUMN public.trillion_club_stake_findings.fact_sha IS
    'sha256 hex of the proposed facts. The Publish tap''s compare-and-swap fences on it and on the '
    'message id. The facts of an open proposal (proposed / publishing) never change in place '
    '(stake_store refuses the write), so a Publish publishes exactly the row its message showed.';
COMMENT ON COLUMN public.trillion_club_stake_findings.stake_id IS
    'The stake row this finding wrote or updated. ON DELETE SET NULL: a Studio delete always '
    'succeeds, and the next reconcile records the pair as withdrawn (deleted_in_studio).';
COMMENT ON COLUMN public.trillion_club_stake_findings.telegram_message_id IS
    'The owner''s Telegram message carrying this finding''s buttons (a note or a proposal). Taps '
    'that change a row are fenced on it.';
COMMENT ON COLUMN public.trillion_club_stake_findings.published_at IS
    'When this finding''s row went live: stamped on every move to published (the auto flip, a '
    'reconcile catch-up, the owner''s Publish of a proposal or an update) and never cleared. The '
    'auto-publish volume caps (3 a day ET, 10 in 7 days) count auto rows by it — plus every auto '
    'finding still new / staged / noted — never by created_at. NULL until then.';
COMMENT ON COLUMN public.trillion_club_stake_findings.owner_decision IS
    'kept | withdrawn | published | dismissed | deleted_in_studio, with decided_at (both or '
    'neither, CHECK). The auto-publish breaker counts withdrawn decisions by decided_at.';
COMMENT ON COLUMN public.trillion_club_stake_findings.attempts IS
    'Processing attempts. An error finding is read again at most once per ET day while '
    'attempts < 3 (stake_contracts.MAX_ATTEMPTS), then becomes a fetch_failed heads-up (a digest '
    'line with its EDGAR link).';

-- ── RLS + grants: service_role only ──────────────────────────────────────────────────────
ALTER TABLE public.trillion_club_stake_findings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "trillion_club_stake_findings_service_all" ON public.trillion_club_stake_findings;
CREATE POLICY "trillion_club_stake_findings_service_all" ON public.trillion_club_stake_findings
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.trillion_club_stake_findings FROM anon, authenticated;

-- A table with no GRANT is owner-only, not open (169's lesson): grant service_role explicitly.
-- UUID primary key: no sequence to grant.
GRANT ALL ON public.trillion_club_stake_findings TO service_role;

-- ── C. Job ledger row (enabled = the no-deploy stop for new finds) ───────────────────────
INSERT INTO public.notification_job_state (job, enabled, updated_at)
VALUES ('trillion_club_stake_finder', TRUE, NOW())
ON CONFLICT (job) DO NOTHING;

COMMIT;

-- VERIFY (read-only) right after applying → one row: t | t | t | t | t | t | t | t
-- SELECT
--   to_regclass('public.trillion_club_stake_findings') IS NOT NULL,
--   (SELECT count(*) FROM information_schema.columns
--     WHERE table_schema = 'public' AND table_name = 'trillion_club_stakes'
--       AND column_name IN ('origin', 'owner_kept_at', 'withdrawn_at', 'investee_cik',
--                           'finding_id')) = 5,
--   -- every pre-existing row is the owner's (true until the finder is switched on)
--   NOT EXISTS (SELECT 1 FROM public.trillion_club_stakes WHERE origin <> 'seed'),
--   (SELECT count(*) FROM pg_constraint
--     WHERE conrelid = to_regclass('public.trillion_club_stakes')
--       AND conname IN ('trillion_club_stakes_origin_check', 'trillion_club_stakes_investee_cik_check',
--                       'trillion_club_stake_finding_iff_machine',
--                       'trillion_club_stake_withdrawn_never_published',
--                       'trillion_club_stake_machine_shape', 'trillion_club_stake_auto_scope',
--                       'trillion_club_stake_kept_only_auto')) = 7,
--   (SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass('public.trillion_club_stake_findings')),
--   NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants
--                WHERE table_schema = 'public' AND table_name = 'trillion_club_stake_findings'
--                  AND grantee IN ('anon', 'authenticated')),
--   (SELECT count(DISTINCT privilege_type) FROM information_schema.role_table_grants
--     WHERE table_schema = 'public' AND table_name = 'trillion_club_stake_findings'
--       AND grantee = 'service_role'
--       AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')) = 4,
--   EXISTS (SELECT 1 FROM public.notification_job_state WHERE job = 'trillion_club_stake_finder');
