-- 143_whale_trades_dedupe.sql
--
-- Why: `whale_trades` has NO unique key of any kind, and `whale_trade_groups` rows are
-- inserted with a check-then-act guard:
--
--     existing = select id from whale_trade_groups where whale_id=? and date=?
--     if existing: continue          -- skip the trades too
--     insert into whale_trade_groups ...
--     insert into whale_trades ...   -- one statement per trade
--
-- Two things go wrong with that, and `hydrate_whales.py` documents both as a KNOWN GAP:
--
--   (a) TOCTOU across processes. The hourly hydration job and a live profile build (or
--       two Railway replicas) can both read "no row", both insert, and one loses to
--       `uq_whale_trade_groups_whale_date` (migration 077) — AFTER its trades have
--       already been written. Those trades keep the losing group's id and are then
--       invisible to the surviving group.
--   (b) Partial replay. If the process dies between the group insert and the last trade
--       insert, the next run sees the group EXISTS and skips it forever, so the group is
--       permanently short of its trades. And if the skip guard is loosened without a
--       unique key, a replay simply DUPLICATES every trade instead.
--
-- The fix has two halves. This migration is the schema half; the code half (converting
-- the insert to `upsert(on_conflict="whale_id,date")` and deriving the skip from a
-- `whale_trades` COUNT rather than from group existence) ships alongside it and is
-- written to tolerate the pre-migration schema, so ORDER OF APPLICATION DOES NOT MATTER.
--
-- Schema: dedupe existing rows, then a unique key on the natural identity of a trade.
--
-- The key is (trade_group_id, ticker, action, date). A filer can legitimately report the
-- same ticker twice in one filing with DIFFERENT actions (a partial sale and a purchase),
-- so `action` is part of the identity; `date` is included because congressional trades
-- carry their own transaction date within a single disclosure group.
--
-- Idempotent: safe to run more than once.

-- ── 1. Remove existing duplicates, keeping the OLDEST row per identity ────────
--
-- DESTRUCTIVE: deletes duplicate `whale_trades` rows. Only rows that are byte-identical
-- on (trade_group_id, ticker, action, date) are touched, and exactly one of each group
-- survives — no distinct trade is lost. `created_at` ASC keeps the first-written row so
-- any downstream reference to it stays valid; `id` breaks ties deterministically.
--
-- These rows are fully regenerable from `whale_filing_snapshots` via
-- `hydrate_whales.py --force`, so there is nothing here that a re-run cannot rebuild.

DELETE FROM public.whale_trades t
USING (
    SELECT id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY trade_group_id, ticker, action, date
                ORDER BY created_at ASC, id ASC
            ) AS rn
        FROM public.whale_trades
        WHERE trade_group_id IS NOT NULL
    ) ranked
    WHERE ranked.rn > 1
) dupes
WHERE t.id = dupes.id;

-- ── 2. The unique key ─────────────────────────────────────────────────────────
--
-- PARTIAL, on `trade_group_id IS NOT NULL`. The FK is `ON DELETE SET NULL`
-- (schema_snapshot.sql), so deleting a group deliberately ORPHANS its trades rather than
-- cascading them away. Orphans all share a NULL group id and would collide under a
-- non-partial key — the constraint must not make deleting a trade group fail.

CREATE UNIQUE INDEX IF NOT EXISTS uq_whale_trades_group_ticker_action_date
    ON public.whale_trades (trade_group_id, ticker, action, date)
    WHERE trade_group_id IS NOT NULL;

COMMENT ON INDEX public.uq_whale_trades_group_ticker_action_date IS
    'Natural identity of a trade within its filing group. Lets the ingester UPSERT '
    'instead of check-then-act, so a partial replay is repaired rather than either '
    'duplicated or skipped forever. Partial because trade_group_id is nullable by '
    'design (FK is ON DELETE SET NULL).';

-- ── 3. Refresh processed_at on snapshot upsert ────────────────────────────────
--
-- Why: `whale_filing_snapshots.processed_at` defaults to now() on INSERT only, but the
-- ingester UPSERTs on (whale_id, filing_period) — so re-processing an existing period
-- never advanced the column. `_process_congressional` orders by `processed_at DESC` to
-- pick the previous snapshot, and was therefore ordering by first-seen rather than
-- last-updated.

CREATE OR REPLACE FUNCTION public.touch_whale_snapshot_processed_at()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    NEW.processed_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_whale_snapshot_touch ON public.whale_filing_snapshots;
CREATE TRIGGER trg_whale_snapshot_touch
    BEFORE UPDATE ON public.whale_filing_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION public.touch_whale_snapshot_processed_at();
