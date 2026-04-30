-- 044_auth_trigger_bypass_rls.sql
--
-- Supabase signup currently 500s with "Database error saving new user".
-- Root cause: RLS on public.users has policy `users_insert_own` with
-- WITH CHECK (auth.uid() = id), but the AFTER INSERT trigger on
-- auth.users runs while the session is still anonymous (auth.uid() is
-- NULL). The RLS check fails → trigger throws → auth.users insert rolls
-- back → 500. No user is ever created.
--
-- Fix: recreate handle_new_auth_user() with SECURITY DEFINER + row_security
-- off so the function-scoped INSERT bypasses RLS. Also seed user_credits
-- in the same trigger (50 starting credits) and wrap in EXCEPTION so a
-- mirror failure can never lock out signups again.

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
BEGIN
    INSERT INTO public.users (id, email, display_name, avatar_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(
            NEW.raw_user_meta_data->>'display_name',
            NEW.raw_user_meta_data->>'full_name',
            split_part(NEW.email, '@', 1)
        ),
        NEW.raw_user_meta_data->>'avatar_url'
    )
    ON CONFLICT (id) DO NOTHING;

    -- Seed credits so the first /research/generate has a row to charge.
    -- 50 credits = 10 deep-research runs at 5 credits each.
    INSERT INTO public.user_credits (user_id, total, used)
    VALUES (NEW.id, 50, 0)
    ON CONFLICT (user_id) DO NOTHING;

    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    -- Mirror failure must never block the auth.users insert. Log it
    -- (visible in Postgres logs) and let the auth row through. A
    -- backfill script can sync any users that landed here.
    RAISE WARNING 'handle_new_auth_user failed for user % (%): %',
        NEW.id, NEW.email, SQLERRM;
    RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_auth_user();
