-- 135_signup_credit_seed_from_plan_credits.sql
--
-- Why: a new account is seeded with THREE credits, not fifty. Both signup seeds hardcode
-- a pricing generation that no longer exists, and `plan_credits` — the table that is
-- supposed to be the single source of truth for allocations — is consulted by neither.
--
-- ── The chain, in the order it actually runs ────────────────────────────────────
--   INSERT INTO auth.users
--     → on_auth_user_created  → handle_new_auth_user()          [SECURITY DEFINER]
--         → INSERT INTO public.users
--             → trg_create_user_credits → create_user_credits() [AFTER INSERT]
--                 → INSERT user_credits (total = CASE tier WHEN 'free' THEN 3
--                                                          WHEN 'pro'  THEN 25
--                                                          WHEN 'premium' THEN 100 END)
--         → INSERT user_credits VALUES (NEW.id, 50, 0) ON CONFLICT DO NOTHING
--                                                        ^^^^^^^^^^^^^^^^^^^^^^
-- The nested trigger has ALREADY created the row by the time the outer statement runs,
-- so the outer `50` conflicts and does nothing, every time. It is not a second opinion —
-- it is unreachable code. The value that actually lands is 3 / 25 / 100, against a live
-- `plan_credits` of 50 / 1200 / 4000. A free account is seeded with 3 credits against a
-- 20-credit report: not one report.
--
-- Migration 100 saw both of these and deliberately deferred them ("DELIBERATELY NOT
-- TOUCHED HERE … the conflicting signup seeds (handle_new_auth_user = 50 vs
-- create_user_credits by-tier 3/25/100)"), because at the time nothing called
-- `ensure_credit_period` and changing them would have altered the live guest-only app.
-- Both preconditions are gone: the credits system is wired up and the app is
-- account-first. This is that deferred cleanup.
--
-- ── Why nobody noticed: ensure_credit_period masks it ───────────────────────────
-- `create_user_credits` inserts with `resets_at` NULL, and `ensure_credit_period` treats
-- `resets_at IS NULL` as due-for-reset — so the first call overwrites `total` to the real
-- plan value. Both `GET /users/me/credits` (users.py:158-163) and `spend_credits()` call
-- it first, so the wrong number is corrected before almost anyone can observe it.
--
-- Three things survive the masking, which is why this is worth fixing rather than
-- documenting:
--
--   (a) users.py explicitly degrades to a RAW read of user_credits when the RPC raises
--       `CreditServiceUnavailable`. A transient Supabase blip on a brand-new account's
--       first credits fetch therefore shows the user "3 credits" — on the screen that
--       decides whether they trust the product.
--   (b) The ledger opens with a `monthly_reset` row on day one. Every new account's first
--       credit_transactions entry claims a monthly reset that never happened, which is
--       wrong in the one table that exists to be auditable. (Live today: exactly one
--       `monthly_reset` row and zero `grant` rows.)
--   (c) `create_user_credits` has NO `ON CONFLICT` clause and no exception handler. If a
--       user_credits row already exists for that id, the unique_violation propagates up
--       into handle_new_auth_user's `EXCEPTION WHEN OTHERS`, which rolls back to the start
--       of its block — discarding the `public.users` INSERT. The result is an auth.users
--       row with no profile row: a signed-in user the app cannot resolve. RAISE WARNING
--       makes that look like a logged non-event.
--
-- ── What this migration changes ─────────────────────────────────────────────────
--   1. create_user_credits() reads plan_credits, sets `resets_at` to the same ET month
--      boundary ensure_credit_period would, logs the initial `grant`, is idempotent
--      (ON CONFLICT DO NOTHING), and can no longer take the parent INSERT down with it.
--   2. handle_new_auth_user()'s hardcoded 50 becomes the same plan_credits lookup. It is
--      unreachable today, but it is one trigger-ordering change away from being the value
--      that ships — and it would hand a Pro subscriber 50 credits instead of 1200.
--
-- After this, `plan_credits` is the only place a credit allocation is written down.
--
-- ── Consequence worth stating: the first `grant` becomes real ──────────────────
-- With `resets_at` set correctly at creation, `ensure_credit_period` finds a valid,
-- not-yet-due row and returns without touching it — so it no longer logs anything on
-- first read. That is why the trigger now writes the `grant` row itself; otherwise the
-- fix would silently delete the ledger's opening entry rather than correct it.
--
-- ── Guest sentinel ─────────────────────────────────────────────────────────────
-- Mirrors ensure_credit_period's guard. The shared guest row carries a deliberately huge
-- fixed balance; if anything ever re-inserts that users row, the trigger must not
-- overwrite it with the free-tier allocation.
--
-- Safe to re-run: CREATE OR REPLACE only, no data rewrite. Existing users are NOT
-- backfilled — ensure_credit_period has already corrected every live row, and re-granting
-- would be a credit giveaway.

BEGIN;

-- ── 1. create_user_credits: allocation from plan_credits, not from a literal ─────
--
-- SECURITY DEFINER + row_security off, matching ensure_credit_period: user_credits is
-- service_role-write-only under RLS, and this fires from whatever context inserted the
-- public.users row. It worked because the live inserters happen to be privileged; that is
-- a property of the callers, not a guarantee of the trigger.
CREATE OR REPLACE FUNCTION public.create_user_credits()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $function$
DECLARE
    _GUEST       CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_alloc      INTEGER;
    v_now_et     TIMESTAMP;
    v_next_reset TIMESTAMPTZ;
BEGIN
    -- Never touch the shared guest bucket's fixed balance.
    IF NEW.id = _GUEST THEN
        RETURN NEW;
    END IF;

    SELECT monthly_credits INTO v_alloc
      FROM public.plan_credits
     WHERE tier = NEW.tier;

    -- An unknown tier seeds 0 rather than NULL. `total` is NOT NULL, so the old CASE (no
    -- ELSE arm) would have raised here and taken the parent INSERT with it.
    IF v_alloc IS NULL THEN
        v_alloc := 0;
    END IF;

    v_now_et     := (now() AT TIME ZONE 'America/New_York');
    v_next_reset := (date_trunc('month', v_now_et) + INTERVAL '1 month')
                        AT TIME ZONE 'America/New_York';

    INSERT INTO public.user_credits (user_id, total, used, resets_at, updated_at)
    VALUES (NEW.id, v_alloc, 0, v_next_reset, NOW())
    ON CONFLICT (user_id) DO NOTHING;

    -- Only log the opening grant if we are the writer that created the row, so a re-insert
    -- or a race cannot double-log. Mirrors ensure_credit_period's own grant row shape.
    IF FOUND THEN
        INSERT INTO public.credit_transactions (user_id, delta, reason, ref_id, balance_after)
        VALUES (NEW.id, v_alloc, 'grant', to_char(v_now_et, 'YYYY-MM'), v_alloc);
    END IF;

    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    -- Seeding credits must never block account creation (the 044 pattern). Without this,
    -- any failure here unwinds handle_new_auth_user's block and leaves an auth.users row
    -- with no public.users mirror. ensure_credit_period creates the row on first read, so
    -- a user who lands here is degraded, not broken.
    RAISE WARNING 'create_user_credits failed for user % (tier %): %', NEW.id, NEW.tier, SQLERRM;
    RETURN NEW;
END;
$function$;

-- ── 2. handle_new_auth_user: same lookup instead of the hardcoded 50 ─────────────
--
-- Unchanged in every other respect, including the EXCEPTION handler that keeps a mirror
-- failure from blocking the auth.users insert.
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $function$
DECLARE
    v_alloc INTEGER;
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

    -- Belt-and-braces only: trg_create_user_credits on public.users has already created
    -- this row, so the ON CONFLICT makes this a no-op on every real signup. It stays as a
    -- safety net for a direct public.users insert that somehow skips the trigger — but it
    -- must read the SAME source, or the net catches the user with the wrong number.
    SELECT monthly_credits INTO v_alloc
      FROM public.plan_credits
     WHERE tier = 'free'::public.user_tier;

    INSERT INTO public.user_credits (user_id, total, used)
    VALUES (NEW.id, COALESCE(v_alloc, 0), 0)
    ON CONFLICT (user_id) DO NOTHING;

    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    -- Mirror failure must never block the auth.users insert. Log it (visible in Postgres
    -- logs) and let the auth row through. A backfill script can sync any users that
    -- landed here.
    RAISE WARNING 'handle_new_auth_user failed for user % (%): %',
        NEW.id, NEW.email, SQLERRM;
    RETURN NEW;
END;
$function$;

COMMENT ON FUNCTION public.create_user_credits() IS
    'AFTER INSERT on public.users: seeds user_credits from plan_credits (never a literal), sets resets_at to the ET month boundary so ensure_credit_period does not treat a fresh row as due, and logs the opening grant. Skips the guest sentinel. Fails soft — see 135.';

COMMIT;
