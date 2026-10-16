-- 197_subscriptions_ordering_comments.sql
--
-- Why: the comment on public.subscriptions.last_event_at (written by migration 114) no longer
-- describes what iap_service.apply_transaction stores, and the Database Atlas shows it verbatim
-- under the column. It says the column holds the signedDate of the most recently applied App
-- Store Server Notification and is NULL for rows last written by the client verify path.
-- Neither is true any more:
--
--   * Since 2026-09-25 a REVOCATION seen on the client path (POST /billing/verify) stores the
--     transaction's own signedDate, so a saved pre-refund JWS re-POSTed later is refused even
--     when the REFUND notification never arrives (SYSTEM_DESIGN_GUIDELINES §9b.3).
--   * A client payload that is not a revocation stores no key, but it does not clear one either.
--     NULL means no notification carrying a signedDate and no client revocation has been applied
--     to the row yet, not "last written by the client".
--   * Since 2026-10-13 the key never moves backwards. A revocation skips the stale checks, so a
--     late refund of an OLDER period used to rewind it and let a payload signed in between
--     re-entitle a refunded row. A signedDate is stored only when it is later than the stored
--     key, or no readable key is stored. The column holds the LATEST signedDate applied, not
--     the last delivery's.
--
-- And updated_at, which carried no comment, became load-bearing on 2026-10-13: apply_transaction
-- fences its write on the updated_at it read (UPDATE ... WHERE id = $1 AND updated_at = $2), so
-- a delivery that read the row before another one wrote it reads again instead of writing over
-- the newer state. That only holds while EVERY writer sets updated_at — no trigger sets it
-- (198's subscriptions_max_period_end, added later, maintains only max_period_end) — which the
-- comment now says, for the next hand-written UPDATE.
--
-- Comment only. No column, type, constraint, index, grant, policy, function or row changes, and
-- no code reads the comments. Apply after the backend deploy that carries the 2026-10-13 IAP
-- changes (applying earlier is harmless: the text then describes code not yet live).
--
-- Idempotent: COMMENT ON replaces the text on every run. Requires 114 (last_event_at).

BEGIN;

SET LOCAL lock_timeout = '5s';

COMMENT ON COLUMN public.subscriptions.last_event_at IS
    'Ordering key for App Store deliveries: Apple''s signedDate (when Apple asserted the state), '
    'NOT expiresDate (which period a transaction is about). Holds the LATEST signedDate applied '
    'to the row and never moves backwards: an App Store Server Notification or a revocation seen '
    'on the client verify path stores its signedDate only when it is later than this value, or '
    'this value is NULL. A client payload that is not a revocation stores none. NULL = no such '
    'event applied yet. Written only by iap_service.apply_transaction. See migrations 114 and '
    '197.';

COMMENT ON COLUMN public.subscriptions.updated_at IS
    'When this row was last written. iap_service.apply_transaction FENCES its update on the '
    'value it read (WHERE id = ... AND updated_at = ...), so every writer of this table MUST set '
    'it (no trigger sets it): a write that leaves it unchanged is invisible to a concurrent '
    'delivery, which would then overwrite it. Also the client ordering fallback for a revoked '
    'row with no last_event_at. See migration 197.';

COMMIT;

-- VERIFY (expect one row: t, t):
--   SELECT
--     col_description('public.subscriptions'::regclass,
--       (SELECT attnum FROM pg_attribute WHERE attrelid = 'public.subscriptions'::regclass
--          AND attname = 'last_event_at')) LIKE '%never moves backwards%' AS key_comment,
--     col_description('public.subscriptions'::regclass,
--       (SELECT attnum FROM pg_attribute WHERE attrelid = 'public.subscriptions'::regclass
--          AND attname = 'updated_at')) LIKE '%FENCES its update%' AS fence_comment;
