-- 212_sync_public_user_email.sql
--
-- Why (sign-in audit CC-09, 2026-11-26): `public.users.email` is a COPY of `auth.users.email`,
-- written once by `handle_new_auth_user` (AFTER INSERT) and never again. Nothing synced it on
-- an UPDATE, and the backend reads the copy as the account's address:
--   * /auth/change-password re-checks the current password by signing in with it,
--   * /auth/set-password verifies the emailed code against it,
--   * iOS sends the set-password code request to it, and Profile shows it.
-- So once the auth row's email changed — support editing it in Studio, or the new in-app email
-- change (POST /auth/change-email, completed by Supabase's Secure email change) — every one of
-- those broke: "Your current password is incorrect", codes sent to the old address, Profile
-- showing the old email.
--
-- What this adds:
--   1. `sync_public_user_email()` + an AFTER UPDATE OF email trigger on auth.users that copies
--      the new address into public.users. Only a real change (WHEN OLD IS DISTINCT FROM NEW),
--      never a NULL (public.users.email is NOT NULL; an auth row without an email keeps the last
--      known address rather than failing the auth write).
--   2. A one-time backfill of rows that have already drifted.
--
-- FAILS OPEN (the 044 pattern): an error is a Postgres WARNING and the auth.users update goes
-- through. Blocking GoTrue's email change over a mirror would strand the user mid-change.
-- `email_change` (the PENDING address of an unconfirmed change) is deliberately NOT synced: the
-- account's address changes only when Supabase has both confirmations.
--
-- Idempotent: CREATE OR REPLACE + DROP TRIGGER IF EXISTS; the backfill only touches rows that
-- differ, so a re-run updates nothing.
--
-- ORDER: independent of any deploy. Apply BEFORE the backend that exposes
-- POST /auth/change-email, or a completed change leaves public.users behind until this lands
-- (the backfill then repairs it).
--
-- VERIFY after applying:
--   SELECT tgname FROM pg_trigger WHERE tgrelid = 'auth.users'::regclass
--    AND tgname = 'on_auth_user_email_changed';                                          -- 1 row
--   SELECT count(*) FROM public.users p JOIN auth.users a ON a.id = p.id
--    WHERE a.email IS NOT NULL AND p.email IS DISTINCT FROM a.email;                       -- 0
--
-- AFTER APPLYING: re-run scripts/dump_schema.sh.

CREATE OR REPLACE FUNCTION public.sync_public_user_email()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO 'public', 'pg_temp'
SET row_security TO 'off'
AS $$
BEGIN
    IF NEW.email IS NULL OR btrim(NEW.email) = '' THEN
        RETURN NEW;
    END IF;
    UPDATE public.users
       SET email = NEW.email,
           updated_at = now()
     WHERE id = NEW.id
       AND email IS DISTINCT FROM NEW.email;
    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'sync_public_user_email failed for user %: % (SQLSTATE %)',
        NEW.id, SQLERRM, SQLSTATE;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.sync_public_user_email() IS
    'AFTER UPDATE OF email on auth.users: copies a changed, non-empty address into '
    'public.users.email (read by change-password, set-password and Profile). Fails open. '
    'Migration 212, audit CC-09.';

REVOKE ALL ON FUNCTION public.sync_public_user_email() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.sync_public_user_email() FROM anon;
REVOKE ALL ON FUNCTION public.sync_public_user_email() FROM authenticated;

DROP TRIGGER IF EXISTS on_auth_user_email_changed ON auth.users;
CREATE TRIGGER on_auth_user_email_changed
    AFTER UPDATE OF email ON auth.users
    FOR EACH ROW
    WHEN (OLD.email IS DISTINCT FROM NEW.email)
    EXECUTE FUNCTION public.sync_public_user_email();

-- Backfill: rows that drifted before this trigger existed (e.g. an address edited in Studio).
UPDATE public.users p
   SET email = a.email,
       updated_at = now()
  FROM auth.users a
 WHERE a.id = p.id
   AND a.email IS NOT NULL
   AND btrim(a.email) <> ''
   AND p.email IS DISTINCT FROM a.email;
