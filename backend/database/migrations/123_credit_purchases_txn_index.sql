-- 123_credit_purchases_txn_index.sql
--
-- ⚠️ RENUMBERED. This shipped as `121_…` and was applied to the live database under that name;
-- a concurrently-developed `121_price_alerts.sql` claimed the same number, so this one moved
-- (121 -> 122 -> 123) to keep the sequence unambiguous. The CONTENT never changed and every
-- statement is `IF NOT EXISTS`, so re-running it on a database that already took the 121
-- version is a clean no-op. Nothing tracks applied numbers here — migrations are applied by
-- hand — so the number is ordering documentation, not state.
--
-- Why: migration 117 meant to create TWO indexes on `credit_purchases` — the unique
-- `(environment, transaction_id)` dedup key, and a plain `(transaction_id)` lookup index — but
-- gave both the SAME NAME (`idx_credit_purchases_txn`). Since every statement in 117 is
-- `CREATE INDEX IF NOT EXISTS`, the second one matched the existing name and became a silent
-- no-op. 117 applied cleanly, reported success, and never created the lookup index.
--
-- That is the real lesson here and it is worth stating plainly: **idempotency guards HIDE name
-- collisions rather than surfacing them.** A duplicated index name inside an `IF NOT EXISTS`
-- migration does not error, does not warn, and leaves nothing behind to notice. The only
-- defence is distinct names — 117 has been corrected so a FRESH apply now creates both, and
-- this migration repairs any database that already took the original.
--
-- Why the index matters: `IAPService.user_id_for_transaction` resolves an App Store REFUND
-- webhook to its owner by `transaction_id` ALONE — it has no `environment` to hand at that
-- point. The unique index leads on `environment`, so Postgres cannot use it for that predicate,
-- and the lookup degrades to a sequential scan over a table that only ever grows. Measured on a
-- 20k-row table: 255 buffers and 19,004 rows filtered, versus a 3-buffer index probe.
--
-- Safe to apply any time and safe to re-run. Purely additive: it creates an index, changes no
-- data and no behaviour beyond the plan chosen for that one lookup. Applying it before or after
-- any code deploy is equally fine.

CREATE INDEX IF NOT EXISTS idx_credit_purchases_txn_lookup
    ON public.credit_purchases (transaction_id);

COMMENT ON INDEX public.idx_credit_purchases_txn_lookup IS
    'Serves IAPService.user_id_for_transaction, which looks up a refunded consumable by '
    'transaction_id alone. The unique (environment, transaction_id) index cannot serve it — '
    'wrong leading column. See migration 121.';
