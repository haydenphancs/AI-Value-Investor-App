-- 144_guest_bucket_probe.sql
--
-- Why: `POST /users/me/claim-guest-data` runs on EVERY cold launch of EVERY signed-in user,
-- and its steady-state answer is "there is nothing to claim".
--
-- The client cannot avoid the call. Its transition key (`AppState.lastAuthenticatedUserId`)
-- is a plain in-memory property on an AppState rebuilt each process, so a launch always looks
-- like a first-ever identity — and that is deliberate: `AppState` documents why a persisted
-- latch is wrong (it would permanently skip the claim for anyone who signed in on a build
-- shipped before the endpoint existed — precisely the users who still need it).
--
-- So the endpoint stays, and the SERVER gets cheap instead. Discovering "nothing to claim"
-- previously cost six sequential PostgREST round trips (one SELECT per table, each
-- short-circuiting on an empty result) on top of the auth lookup. Six EXISTS inside one
-- function answers the same question in ONE round trip. The full six-step claim still runs
-- whenever the probe says there IS something.
--
-- Cost: every predicate below is already index-covered — idx_watchlist_user,
-- idx_portfolios_user_sort, idx_user_learn_progress_lookup (user_id, content_type),
-- idx_reports_user, idx_chat_sessions_user, and user_investor_profile.user_id is the PK — so
-- no new index is needed and each EXISTS stops at the first matching row.
--
-- Safety: SECURITY DEFINER with a pinned search_path, service_role only. It reveals one
-- boolean about a caller-supplied uuid and mutates nothing, but it reads six user tables, so
-- it must not be reachable with the shipped anon key.
--
-- Note (migration 142's lesson): CREATE OR REPLACE with an UNCHANGED signature preserves
-- existing grants; a DROP would discard them and re-own the function. The GRANT is re-issued
-- below anyway so a fresh database ends up in the same state.

CREATE OR REPLACE FUNCTION public.guest_bucket_has_data(p_bucket uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT EXISTS (SELECT 1 FROM watchlist_items       WHERE user_id = p_bucket)
        OR EXISTS (SELECT 1 FROM portfolios            WHERE user_id = p_bucket)
        OR EXISTS (SELECT 1 FROM user_learn_progress   WHERE user_id = p_bucket)
        OR EXISTS (SELECT 1 FROM research_reports      WHERE user_id = p_bucket)
        OR EXISTS (SELECT 1 FROM chat_sessions         WHERE user_id = p_bucket)
        OR EXISTS (SELECT 1 FROM user_investor_profile WHERE user_id = p_bucket);
$$;

COMMENT ON FUNCTION public.guest_bucket_has_data(uuid) IS
    'True when the per-install guest bucket holds anything claimable. One round trip instead '
    'of six, for the launch-time no-op case of POST /users/me/claim-guest-data.';

REVOKE ALL ON FUNCTION public.guest_bucket_has_data(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.guest_bucket_has_data(uuid) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.guest_bucket_has_data(uuid) TO service_role;
