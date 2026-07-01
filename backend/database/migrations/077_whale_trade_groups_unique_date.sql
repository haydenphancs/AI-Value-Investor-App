-- 077_whale_trade_groups_unique_date.sql
--
-- NOTE: this index already appears in the live DB (it is present in the
-- regenerated schema_snapshot.sql). If you applied it out-of-band, this file is
-- documentary — CREATE UNIQUE INDEX IF NOT EXISTS + the rn>1 cleanup DELETEs are
-- all no-ops on an already-deduped, already-indexed database.
--
-- Why: whale trade groups are de-duplicated in application code by a
-- SELECT-then-INSERT on (whale_id, date) in BOTH _sync_to_whale_tables
-- (app/services/whale_service.py) and _persist (scripts/hydrate_whales.py).
-- That check-then-act is a TOCTOU race across processes: the daily/6-hourly
-- hydration job and a live profile-build can both SELECT "no row" and then both
-- INSERT the same (whale_id, date) group — silently re-creating the duplicate
-- rows that migration 076 was meant to eliminate. There is currently no DB-level
-- guarantee (only PK(id) + a non-unique (whale_id, created_at) index), so app
-- code alone cannot prevent it. Add the UNIQUE constraint that makes the
-- (whale_id, date) dedup authoritative; the code additionally catches the
-- unique-violation and skips the duplicate insert.
--
-- Cleanup first: collapse any pre-existing duplicate (whale_id, date) groups
-- (keep the newest) and their orphaned child trades, so the UNIQUE index can be
-- created. Regenerable from FMP via hydration — no user data lost.
--
-- Idempotent: safe to re-apply (no dupes -> deletes nothing; index IF NOT EXISTS).
-- Apply manually. Apply AFTER 076 + `hydrate_whales --force` so the dataset is
-- already de-duped in the common case.

-- ── 1. Delete child trades of duplicate groups (keep newest per whale_id,date)
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY whale_id, date
               ORDER BY created_at DESC, id
           ) AS rn
    FROM public.whale_trade_groups
)
DELETE FROM public.whale_trades
WHERE trade_group_id IN (SELECT id FROM ranked WHERE rn > 1);

-- ── 2. Delete the duplicate group rows themselves
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY whale_id, date
               ORDER BY created_at DESC, id
           ) AS rn
    FROM public.whale_trade_groups
)
DELETE FROM public.whale_trade_groups
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- ── 3. Authoritative uniqueness for the app-level dedup key
CREATE UNIQUE INDEX IF NOT EXISTS uq_whale_trade_groups_whale_date
    ON public.whale_trade_groups (whale_id, date);
