-- 046_seed_guest_user.sql
--
-- TEMP: until the iOS login screen is built, the research and credits
-- endpoints fall back to GUEST_USER_ID (dependencies.py:88) for any
-- request without a valid JWT. Charge/refund need a real
-- public.users + user_credits row at that UUID, otherwise
-- charge_user_credits() returns NULL (no row matches) and the iOS
-- app sees a permanent INSUFFICIENT_CREDITS.
--
-- public.users.id has a FK to auth.users.id. We can't satisfy that
-- without creating an auth user, which we don't want for a guest. So
-- we drop the FK on this single guest row by routing through a
-- pre-existing auth user OR (preferred here) just create the auth
-- user once via Supabase admin API (out-of-band). For local dev the
-- simplest path is to skip the FK by creating the guest auth user via
-- the Supabase admin endpoint at deploy time, then this migration
-- ensures the public.users + user_credits rows exist.
--
-- This migration tolerates BOTH paths: it inserts only if the auth
-- user already exists. If it doesn't, the trigger from migration 044
-- will create the public.users + user_credits rows once the auth user
-- is created. Either way, no error.

DO $$
DECLARE
    guest_uuid UUID := '00000000-0000-0000-0000-000000000000';
    auth_exists BOOLEAN;
BEGIN
    SELECT EXISTS (SELECT 1 FROM auth.users WHERE id = guest_uuid) INTO auth_exists;

    IF auth_exists THEN
        INSERT INTO public.users (id, email, display_name, tier)
        VALUES (guest_uuid, 'guest@local', 'Guest', 'free')
        ON CONFLICT (id) DO NOTHING;

        INSERT INTO public.user_credits (user_id, total, used)
        VALUES (guest_uuid, 100000, 0)
        ON CONFLICT (user_id) DO UPDATE
            SET total = GREATEST(public.user_credits.total, 100000);

        RAISE NOTICE 'Guest user seeded: % credits remaining',
            (SELECT total - used FROM public.user_credits WHERE user_id = guest_uuid);
    ELSE
        RAISE NOTICE 'Guest auth user not found — create it via the admin API '
            'first (see migration comment), then re-run this migration.';
    END IF;
END $$;
