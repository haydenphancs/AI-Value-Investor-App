-- 201_subscription_transactions_purge_with_account.sql
--
-- Why (IAP audit F17, 2026-10-17; owner decision 2026-10-21: "Purge them at deletion"): 198's
-- transaction register promises its rows "go with the account" (owner decision D3, "Delete with
-- account", and privacy.html's deletion section), but its only deletion path is the FK
-- `user_id REFERENCES public.users ON DELETE CASCADE`, and a NULL owner never matches it. Rows of
-- a deleted account's lineages that were recorded with no owner therefore survived the deletion:
--   (A) a REFUND of an unbound lineage whose appAccountToken names an account that already holds
--       an entitling row (iap_service refuses to bind it) — since the phase-2 backend this is
--       recorded under the token's account instead, so the FK cascade deletes it;
--   (B) a token-less refund recorded before the account bound that lineage.
-- No money or access effect (the rows only block replays of refunded transactions and hold no
-- Caydex user id), but they outlived an account deletion the user was promised.
--
-- The cost, accepted with the decision (it extends D3's trade-off for owned rows): a purged
-- ownerless refund row was the only thing making Rule A refuse a replay of that refunded
-- transaction's saved pre-refund JWS. After the deletion, the same Apple ID signed in to a NEW
-- Caydex account can replay it, and since the deleted account's credit periods cascade away
-- (subscription_credit_periods' key is global), that period's credits can be granted again.
--
-- What this migration does: a BEFORE DELETE row trigger on public.users deletes the ownerless
-- register rows of the account's lineages — every original transaction its subscriptions rows,
-- its credit periods or its own register rows name — BEFORE the cascade removes the evidence of
-- which lineages were its. A lineage another account still references (a subscriptions row, a
-- credit period or an owned register row of a different user) is left alone: those rows feed that
-- account's replay refusal (Rule A) and ensure_credit_period's "never reinstate a refunded period"
-- guard. A trigger rather than users.py: it covers every deletion path (DELETE /users/me's auth
-- admin delete, Studio, scripts), it is atomic with the cascade, and it needs no deploy.
--
-- What it leaves (documented, SDG §9b.12):
--   * rows recorded AFTER the deletion — an in-flight delivery whose owner 198's
--     record_subscription_transaction nulls because the users row is gone, or Apple's later refund
--     of the deleted account's lineage. They hold no account id; a token-less one still usefully
--     blocks a replay of that refunded transaction into a new account;
--   * case (A) rows recorded ownerless BEFORE the phase-2 backend deploy: the lineage links to the
--     account only through the token, which is not stored, so none of the three sources names it;
--   * no backfill: rows left by deletions before this migration cannot be told from never-bound
--     lineages.
--
-- ⚠️ The function MUST `RETURN OLD` on every path. A NULL return from a BEFORE DELETE row trigger
-- on the child of auth.users' ON DELETE CASCADE does not raise: it silently SKIPS the public.users
-- delete, so the auth user is gone while public.users and every row cascading from it survive,
-- orphaned — and DELETE /users/me reports {"deleted": true} (an App Store 5.1.1(v) break nobody
-- would see). Any ERROR, on the other hand, fails GoTrue's delete and every account deletion with
-- ACCOUNT_DELETE_INCOMPLETE — so the purge is wrapped in an EXCEPTION handler that logs a WARNING
-- (Postgres log) and still returns OLD: deletion fails OPEN, back to the pre-201 behaviour where
-- these rows survive. Pinned by tests/test_account_deletion_completeness.py.
--
-- Deploy order: none — pure SQL, independent of any backend deploy. Needs 198. Apply in Supabase
-- Studio as `postgres` (the SECURITY DEFINER owner must be able to delete register rows with
-- row_security off).
-- Idempotent: CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS + CREATE TRIGGER, COMMENTs.

BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF to_regclass('public.subscription_transactions') IS NULL THEN
        RAISE EXCEPTION '201 needs migration 198 (subscription_transactions) — apply 198 first';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.purge_ownerless_subscription_transactions()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
BEGIN
    DELETE FROM public.subscription_transactions t
     WHERE t.user_id IS NULL
       AND t.original_transaction_id IN (
             SELECT s.original_transaction_id FROM public.subscriptions s
              WHERE s.user_id = OLD.id AND s.original_transaction_id IS NOT NULL
             UNION
             SELECT p.original_transaction_id FROM public.subscription_credit_periods p
              WHERE p.user_id = OLD.id
             UNION
             SELECT o.original_transaction_id FROM public.subscription_transactions o
              WHERE o.user_id = OLD.id)
       AND NOT EXISTS (SELECT 1 FROM public.subscriptions s2
                        WHERE s2.original_transaction_id = t.original_transaction_id
                          AND s2.user_id <> OLD.id)
       AND NOT EXISTS (SELECT 1 FROM public.subscription_credit_periods p2
                        WHERE p2.original_transaction_id = t.original_transaction_id
                          AND p2.user_id <> OLD.id)
       AND NOT EXISTS (SELECT 1 FROM public.subscription_transactions o2
                        WHERE o2.original_transaction_id = t.original_transaction_id
                          AND o2.user_id IS NOT NULL
                          AND o2.user_id <> OLD.id);
    RETURN OLD;
EXCEPTION WHEN OTHERS THEN
    -- Never block an account deletion over register hygiene: the rows stay, as before 201.
    RAISE WARNING 'purge_ownerless_subscription_transactions: could not purge the ownerless register rows of user % (%: %) — the account is still deleted; the rows stay',
        OLD.id, SQLSTATE, SQLERRM;
    RETURN OLD;
END;
$$;

-- A trigger function is never called directly, and EXECUTE is not checked when a trigger fires.
-- anon / authenticated are named because Supabase's default privileges grant them explicitly
-- (PUBLIC alone is not enough); service_role keeps its default grant, unused.
REVOKE ALL ON FUNCTION public.purge_ownerless_subscription_transactions()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS users_purge_ownerless_subscription_transactions ON public.users;
CREATE TRIGGER users_purge_ownerless_subscription_transactions
    BEFORE DELETE ON public.users
    FOR EACH ROW EXECUTE FUNCTION public.purge_ownerless_subscription_transactions();

COMMENT ON FUNCTION public.purge_ownerless_subscription_transactions() IS
    'BEFORE DELETE trigger on public.users (201, IAP audit F17): deletes the ownerless '
    'subscription_transactions rows of the account''s lineages (named by its subscriptions rows, '
    'credit periods or owned register rows) before the cascade removes them, unless another '
    'account still references that lineage. Owned rows go by the FK cascade. RETURN OLD on '
    'every path is mandatory: NULL would silently skip the public.users delete under the '
    'auth.users cascade (orphaned data, deletion reported as done); an error is caught and logged '
    'as a WARNING so a deletion never fails over this purge.';

COMMENT ON TABLE public.subscription_transactions IS
    'One row per Apple subscription transaction iap_service has seen, keyed (environment, '
    'transaction_id): applied_at = first written to a lineage row; revoked_signed_at / '
    'reversed_signed_at = the latest Apple signedDate of a refund of it and of a reversal of that '
    'refund (both only move forward, so arrival order never matters). REFUNDED while '
    'revoked_signed_at is set and later than reversed_signed_at. Refuses replays of refunded '
    'transactions (even of lineages never bound to an account), lets a reversal through in either '
    'arrival order, tells an older period from an unrecorded upgrade, and keeps '
    'ensure_credit_period from reinstating a refunded period. Written only by '
    'record_subscription_transaction (and purged by the 201 trigger below). user_id = the bound '
    'account, or (recorded by the backend deployed with 201) the account an unbound refund''s '
    'appAccountToken names, never binding the lineage; NULL = no known owner. Rows go '
    'with the account: owned ones by ON DELETE CASCADE, ownerless ones of its lineages by the '
    'users trigger users_purge_ownerless_subscription_transactions (201); rows recorded after a '
    'deletion stay, holding no account id. SERVICE-ROLE ONLY. 198, 201.';

COMMIT;

-- VERIFY (run after applying):
--   1. One row: 'O', t.
--   SELECT tgenabled, pg_get_triggerdef(oid) LIKE '%BEFORE DELETE ON public.users FOR EACH ROW%' AS before_delete
--     FROM pg_trigger
--    WHERE tgrelid = 'public.users'::regclass AND tgname = 'users_purge_ownerless_subscription_transactions';
--
--   2. One row: t, t, f, f, postgres, {"search_path=public, pg_temp",row_security=off}.
--   SELECT p.prosecdef,
--          pg_get_functiondef(p.oid) LIKE '%RETURN OLD%' AS returns_old,
--          has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_exec,
--          has_function_privilege('authenticated', p.oid, 'EXECUTE') AS auth_exec,
--          pg_get_userbyid(p.proowner) AS owner,
--          p.proconfig
--     FROM pg_proc p WHERE p.oid = 'public.purge_ownerless_subscription_transactions()'::regprocedure;
--
--   2b. Three rows, each owner postgres and force_rls f (FORCE ROW LEVEL SECURITY, or another
--       owner, would make the purge raise — caught as a WARNING, so the rows would stay):
--   SELECT relname, pg_get_userbyid(relowner) AS owner, relforcerowsecurity AS force_rls
--     FROM pg_class WHERE oid IN ('public.subscription_transactions'::regclass,
--                                 'public.subscriptions'::regclass,
--                                 'public.subscription_credit_periods'::regclass);
--
--   3. Informational: how many register rows have no owner (rows left by deletions before 201,
--      never-bound lineages, and rows recorded after a deletion).
--   SELECT count(*) FILTER (WHERE user_id IS NULL) AS ownerless, count(*) AS total
--     FROM public.subscription_transactions;
