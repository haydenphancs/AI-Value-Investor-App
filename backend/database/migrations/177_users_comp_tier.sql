-- 177_users_comp_tier.sql
--
-- Why: a COMPLIMENTARY tier floor for accounts that are on a paid tier without paying — the App
-- Review demo account and TestFlight testers.
--
-- Until now those accounts were given a tier by writing `users.tier` directly
-- (`scripts/seed_testflight_testers.py`). But `users.tier` is a MIRROR: `iap_service
-- .reconcile_user_tier` rewrites it from the `subscriptions` rows on every verified purchase,
-- every App Store Server Notification and every expiry sweep, and a hand-set tier has no row
-- behind it. So the moment App Review bought a sandbox subscription on the demo account (which
-- they do, to review the in-app purchases), the account was re-tiered to whatever they bought,
-- and when the accelerated sandbox subscription expired (~1 hour) it dropped to Free — locking
-- the Pro/Max narration that answers the 1.0 (9) Guideline 2.5.4 rejection, mid-review or on
-- the next round. Found by the 2026-09-24 pre-resubmission audit (finding payments-2).
--
-- A promo `subscriptions` row cannot fix it: `subscriptions_user_id_key UNIQUE (user_id)` holds
-- one row per user, and the client-verify path (no signedDate) overwrites that row with the
-- sandbox purchase.
--
-- Schema: one nullable column. NULL (every existing and every new account) means "no floor"
-- and changes nothing. `reconcile_user_tier` now writes max(winning subscription tier,
-- comp_tier) — a floor, never a ceiling: a comp-Pro account that buys Max gets Max.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS and COMMENT are re-runnable.
--
-- Grants and RLS: unchanged. `public.users` is service-role-only
-- (`GRANT ALL ON TABLE public.users TO service_role`), which covers the new column; no client
-- reads it.
--
-- Deploy order: EITHER order is safe. The backend reads the column tolerantly — a missing
-- column (42703 / PGRST204) is logged as a warning and treated as "no floor", so deploying the
-- code first changes nothing until this is applied.
-- ⚠️ But the floor PROTECTS nothing until the backend carrying `effective_tier` /
-- `reconcile_user_tier` is live on Railway: before that deploy, a sandbox purchase or its
-- expiry still re-tiers the account whatever comp_tier says. Apply + deploy, THEN resubmit.
--
-- After applying, set the floor on the demo account (and any testers):
--   UPDATE public.users SET comp_tier = 'premium', tier = 'premium'
--    WHERE email = '<the App Review demo account>';
--   SELECT public.grant_tier_upgrade('<that user id>');   -- lift the monthly allocation to Max
-- or run `scripts/seed_testflight_testers.py`, which now writes comp_tier as well.
--
-- Verify after applying:
--   SELECT column_name, data_type, udt_name, is_nullable
--     FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'comp_tier';
--   -- expect: comp_tier | USER-DEFINED | user_tier | YES

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS comp_tier public.user_tier;

COMMENT ON COLUMN public.users.comp_tier IS
    'Complimentary tier FLOOR (App Review demo account, TestFlight testers). NULL = none. '
    'iap_service.reconcile_user_tier writes users.tier = max(winning subscription tier, '
    'comp_tier), so a sandbox purchase or its expiry can never demote the account below it. '
    'Migration 177.';

-- `users.tier` is no longer "written only after Apple verification": it is max(winning
-- subscription tier, comp_tier), and the tester/demo scripts write it too. Keep the column's
-- own comment true, since the Database Atlas renders it.
COMMENT ON COLUMN public.users.tier IS
    'Effective tier the app gates on. Written by iap_service.reconcile_user_tier as '
    'max(winning subscriptions tier, users.comp_tier); also set by '
    'scripts/seed_testflight_testers.py and scripts/set_comp_tier.py for complimentary '
    'accounts. ensure_credit_period / grant_tier_upgrade read it for the monthly allocation.';
