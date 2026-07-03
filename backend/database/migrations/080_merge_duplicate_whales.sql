-- 080_merge_duplicate_whales.sql
--
-- Why: the whale registry double-listed 6 person↔fund pairs on ONE SEC CIK each
-- (Bill Ackman ↔ Pershing Square Capital, Ray Dalio ↔ Bridgewater Associates,
-- Cathie Wood ↔ ARK Invest, David Tepper ↔ Appaloosa Management,
-- George Soros ↔ Soros Fund Management, Seth Klarman ↔ Baupost Group).
-- A 13F filing belongs to exactly one filer (one CIK), so this produced two
-- unlinked profiles per portfolio, double hydration (2x FMP calls/day for the
-- same data), and let users follow "both". Survey of 11 tracker products
-- (GuruFocus, Dataroma, Stockcircle, Quiver, TipRanks, WhaleWisdom, 13f.info,
-- Unusual Whales, HedgeFollow, Fiscal.ai, Autopilot): every one keeps ONE
-- profile per filer, person-fronted on retail products with the firm shown as
-- a subtitle. This migration merges each pair into the PERSON row and adds
-- `firm_name` so the firm always renders next to the person's name.
--
-- Pairs with the registry (backend/data/whale_registry.json) and the sync
-- script (backend/scripts/sync_whale_registry.py), which now write firm_name.
-- Apply BEFORE running the updated sync (sync selects the new column).

-- 1. The firm a person-fronted whale runs, displayed with their name on every
--    surface (list rows, search results, profile header, signal drill-downs).
--    NULL for firm-branded institutions (name IS the firm) and politicians.
ALTER TABLE public.whales ADD COLUMN IF NOT EXISTS firm_name TEXT;

COMMENT ON COLUMN public.whales.firm_name IS
    'Firm a person-fronted whale runs (e.g. "Bridgewater Associates" for Ray Dalio). NULL for institutions/politicians.';

-- 2. Move follows from each firm row to its person row BEFORE deleting, so no
--    user silently loses a follow. UNIQUE(user_id, whale_id) absorbs users who
--    followed both; the AFTER INSERT followers_count trigger fires only for
--    rows actually inserted, and step 5 renormalizes counts anyway.
INSERT INTO public.whale_follows (user_id, whale_id, followed_at)
SELECT f.user_id, p.id, f.followed_at
FROM (VALUES
    ('Pershing Square Capital', '0001336528', 'Bill Ackman'),
    ('Bridgewater Associates',  '0001350694', 'Ray Dalio'),
    ('ARK Invest',              '0001697748', 'Cathie Wood'),
    ('Appaloosa Management',    '0001656456', 'David Tepper'),
    ('Soros Fund Management',   '0001029160', 'George Soros'),
    ('Baupost Group',           '0001061768', 'Seth Klarman')
) AS m(firm, cik, person)
JOIN public.whales fw ON fw.name = m.firm   AND fw.cik = m.cik AND fw.category = 'institutions'
JOIN public.whales p  ON p.name  = m.person AND p.cik  = m.cik AND p.category  = 'investors'
JOIN public.whale_follows f ON f.whale_id = fw.id
ON CONFLICT (user_id, whale_id) DO NOTHING;

-- 3. Seed firm_name for the 6 person rows here (not just via sync) so the
--    merge is atomic — no window where the firm row is gone but the person row
--    has no firm to display. The updated sync covers all other investors.
UPDATE public.whales p
SET firm_name = m.firm
FROM (VALUES
    ('Pershing Square Capital', '0001336528', 'Bill Ackman'),
    ('Bridgewater Associates',  '0001350694', 'Ray Dalio'),
    ('ARK Invest',              '0001697748', 'Cathie Wood'),
    ('Appaloosa Management',    '0001656456', 'David Tepper'),
    ('Soros Fund Management',   '0001029160', 'George Soros'),
    ('Baupost Group',           '0001061768', 'Seth Klarman')
) AS m(firm, cik, person)
WHERE p.name = m.person AND p.cik = m.cik AND p.category = 'investors';

-- DESTRUCTIVE: deletes the 6 firm-named whale rows. Their whale_holdings /
-- whale_trades / whale_trade_groups / caches are EXACT duplicates of the paired
-- person rows (hydration fetches by the shared CIK and writes per whale_id), so
-- no data is lost and all of it is regenerable via scripts/hydrate_whales.py.
-- Follows were moved in step 2. All 8 child tables FK with ON DELETE CASCADE;
-- the whale_follows decrement trigger's UPDATE matches 0 rows post-delete (no-op).
-- 4. Gated on the paired PERSON row existing — if a person row is missing or
--    renamed in the live DB, that pair's firm row is kept (steps 2/3 would have
--    skipped it too), never deleting a profile without its replacement.
DELETE FROM public.whales fw
USING (VALUES
    ('Pershing Square Capital', '0001336528', 'Bill Ackman'),
    ('Bridgewater Associates',  '0001350694', 'Ray Dalio'),
    ('ARK Invest',              '0001697748', 'Cathie Wood'),
    ('Appaloosa Management',    '0001656456', 'David Tepper'),
    ('Soros Fund Management',   '0001029160', 'George Soros'),
    ('Baupost Group',           '0001061768', 'Seth Klarman')
) AS m(firm, cik, person)
WHERE fw.name = m.firm AND fw.cik = m.cik AND fw.category = 'institutions'
  AND EXISTS (
      SELECT 1 FROM public.whales p
      WHERE p.name = m.person AND p.cik = m.cik AND p.category = 'investors'
  );

-- 5. Renormalize followers_count from ground truth. Safe: the column is purely
--    trigger-derived (DEFAULT 0 + symmetric insert/delete triggers), no seeded
--    synthetic values. Also repairs any historical drift.
UPDATE public.whales w
SET followers_count = sub.cnt
FROM (
    SELECT w2.id, COUNT(f.id) AS cnt
    FROM public.whales w2
    LEFT JOIN public.whale_follows f ON f.whale_id = w2.id
    GROUP BY w2.id
) AS sub
WHERE w.id = sub.id AND w.followers_count IS DISTINCT FROM sub.cnt;

-- 6. Hardening: one whale per CIK from now on. The name-keyed, additive-only
--    sync script would silently re-insert a duplicate-CIK row if an OLD
--    checkout's registry were ever synced again — this makes that a loud
--    failure instead. Partial index excludes politicians (cik IS NULL).
--    NOTE: creation fails if any OTHER duplicate-CIK rows exist beyond the 6
--    deleted above — that failure is a signal to investigate, not to skip.
CREATE UNIQUE INDEX IF NOT EXISTS uq_whales_cik
    ON public.whales (cik)
    WHERE cik IS NOT NULL;

-- The old non-unique index has the same key + predicate — fully redundant now.
DROP INDEX IF EXISTS public.idx_whales_cik;
