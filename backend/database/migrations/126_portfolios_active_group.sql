-- 126_portfolios_active_group.sql
--
-- Why: three screens show the user's tickers and they are silently inconsistent, because
-- only ONE of them knows which group ("portfolio") the user is looking at.
--
--   Home "Your Watchlist"  → reads watchlist_items directly, 12 newest, HARDCODED label
--   Updates chips          → reads watchlist_items directly, 30 newest
--   Tracking → Assets      → the tracking feed INTERSECTED with the active portfolio
--
-- They look identical today only because `_write_through_to_lone_portfolio` mirrors a new
-- ticker into the user's portfolio when they have EXACTLY ONE — which is everyone until
-- they create a second. After that, a newly added ticker belongs to no group and is
-- invisible on the very tab named "Tracking", while still showing on Home and Updates.
--
-- The blocker for fixing that on the server is this: the active group is a device-local
-- `UserDefaults["TrackingView.activePortfolioId"]` string. The backend has never been told
-- which group is active, so Home and Updates cannot follow it, and switching groups on one
-- device does not follow the user to another. This migration moves that fact into the
-- database, which is what lets one list drive all three surfaces.
--
-- NO FOREIGN KEY IS ADDED. `portfolios.user_id` deliberately has none (migration 108) so a
-- signed-out caller can be partitioned per install with a synthetic uuid5 that has no
-- `public.users` row. Adding one here would fail every guest INSERT. See auth.md §1a.
--
-- ON THE PARTIAL UNIQUE INDEX — it is the hard guarantee that "the active group" is
-- singular. Application code can drift; this cannot. It is created AFTER the backfill
-- because the backfill is what makes the existing data satisfy it.
--
-- ⚠️ SHIPS WITH A CODE CHANGE, and the pairing is load-bearing in BOTH directions:
--   * Apply this BEFORE deploying, or `_seed_default_portfolio` / `create_portfolio` /
--     the activate route write a column that does not exist (PostgREST PGRST204).
--   * `POST /users/me/claim-guest-data` MUST clear `is_active` in the same UPDATE that
--     re-points a guest group at the account. Every bucket has an active row — the seed
--     sets one for the guest bucket too — so moving one onto an account that already has
--     one violates the index below. `_claim_portfolios` shares ONE try block, so that
--     23505 would skip the Learn / research / chat claims after it, exactly as the
--     portfolios NAME collision once did. Handled in `users.py`; pinned by
--     `tests/test_watchlist_portfolio_writethrough.py`.
--
-- ON `SET row_security = off` (both functions) — the same construct migrations 118/124 use.
-- It is NOT what bypasses RLS here: these are SECURITY DEFINER and the definer owns
-- `portfolios`, and owners are exempt unless the table is set FORCE ROW LEVEL SECURITY
-- (nothing in this schema is). It is kept deliberately as a tripwire: Postgres raises an
-- error rather than silently filtering if a policy ever WOULD apply — e.g. if someone later
-- recreates these as a non-owner, or adds FORCE. A loud failure beats a function that
-- quietly reads zero rows and reports "the user has no groups".
--
-- Safe to re-run: statement A collapses excess active rows, statement B fills an absence,
-- and neither resets a user's deliberate choice.

BEGIN;

-- ── 1. The column ────────────────────────────────────────────────────────────────────────

ALTER TABLE public.portfolios
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.portfolios.is_active IS
    'At most one row per user is TRUE: the group currently driving Home, Updates and '
    'Tracking. Enforced by idx_portfolios_one_active_per_user. The backend only ever '
    'writes it through set_active_portfolio() / ensure_active_portfolio(); note that is '
    'a convention, NOT a privilege boundary — migration 037 granted table-level UPDATE '
    'on portfolios to authenticated, so a signed-in caller can still clear their own '
    'flag directly via PostgREST. The index makes that unable to produce two active '
    'groups, and GET /portfolios re-heals zero. See migration 126.';


-- ── 2. Backfill: every existing user gets exactly one active group ───────────────────────
-- Two statements — COLLAPSE any excess, then FILL any absence — rather than one clear-then-set
-- pass. A blanket `SET is_active = FALSE` followed by "activate each user's first group" is
-- idempotent in the sense that it converges, but on a RE-RUN it would silently reset every
-- user's deliberately chosen group back to their first one. "Safe to re-run" has to mean
-- "does not change a correct state", not merely "does not error".
--
-- Statement A alone is what makes the unique index creatable. The `NOT EXISTS` guard on its
-- own is the WRONG guard for the one state the index actually rejects: a user who somehow
-- already has TWO active rows (this file applied in pieces outside its transaction, or the
-- index dropped and rows edited since) makes that guard see an active row, skip, leave both
-- TRUE — and CREATE UNIQUE INDEX below then aborts the whole migration.
--
-- The ordering `(sort_order, created_at, id)` is the app's list order with ties broken; all
-- four columns are NOT NULL, so the pick is total and deterministic.

-- A. A user with several active rows keeps exactly the first.
UPDATE public.portfolios p
   SET is_active = FALSE
 WHERE p.is_active
   AND p.id <> (
         SELECT a.id
           FROM public.portfolios a
          WHERE a.user_id = p.user_id AND a.is_active
          ORDER BY a.sort_order, a.created_at, a.id
          LIMIT 1
       );

-- B. A user with no active row gets their first group. Sees statement A's effect, since a
--    later statement in the same transaction reads the earlier one's output.
UPDATE public.portfolios p
   SET is_active = TRUE
  FROM (
        SELECT DISTINCT ON (user_id) id
          FROM public.portfolios
         ORDER BY user_id, sort_order, created_at, id
       ) first_per_user
 WHERE p.id = first_per_user.id
   AND NOT EXISTS (
         SELECT 1 FROM public.portfolios a
          WHERE a.user_id = p.user_id AND a.is_active
       );


-- ── 3. The singleton guarantee ───────────────────────────────────────────────────────────

CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolios_one_active_per_user
    ON public.portfolios (user_id)
    WHERE is_active;


-- ── 4. set_active_portfolio — switch the active group ────────────────────────────────────

CREATE OR REPLACE FUNCTION public.set_active_portfolio(
    p_user_id      UUID,
    p_portfolio_id UUID
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_owned BOOLEAN;
BEGIN
    -- Serialise on the USER, not on the target row. A row lock on `id = p_portfolio_id` is
    -- what an earlier draft used, and it does not serialise anything: two concurrent
    -- switches for the same user lock DIFFERENT rows, so both proceed. Worked interleaving,
    -- with the user's active row currently C:
    --
    --   T1 locks A, clears C, sets A = TRUE  (uncommitted)
    --   T2 locks B (no conflict), its clear blocks on T1's lock of C
    --   T1 commits; T2 re-checks C, now FALSE, so T2's clear matches NOTHING —
    --     A's new TRUE version postdates T2's clear snapshot
    --   T2 sets B = TRUE  →  duplicate key on idx_portfolios_one_active_per_user
    --
    -- And when the user has ZERO active rows — a state this schema explicitly expects and
    -- heals — neither clear touches anything, so there is no incidental lock contention to
    -- narrow the window at all and the collision window is the whole transaction.
    --
    -- An advisory lock keyed on the user is the only thing that covers both. It is
    -- transaction-scoped, so it releases on COMMIT/ROLLBACK with no unlock path to forget.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

    SELECT TRUE INTO v_owned
      FROM public.portfolios
     WHERE user_id = p_user_id AND id = p_portfolio_id;

    IF NOT FOUND THEN
        RETURN FALSE;   -- not this user's portfolio (or gone) — caller answers 404
    END IF;

    -- TWO statements, deliberately. The obvious one-statement form
    --     UPDATE portfolios SET is_active = (id = p_portfolio_id) WHERE user_id = p_user_id
    -- transiently holds two TRUE rows for the same user while the executor walks them, and a
    -- plain unique INDEX is checked per row and cannot be deferred. That form therefore fails
    -- with a duplicate-key error whenever the new row happens to be visited before the old one
    -- is cleared — i.e. intermittently, depending on physical row order. Clear, then set.
    UPDATE public.portfolios
       SET is_active = FALSE, updated_at = NOW()
     WHERE user_id = p_user_id AND is_active AND id <> p_portfolio_id;

    -- `AND NOT is_active` keeps re-activating the current group a genuine no-op instead of a
    -- spurious updated_at bump.
    UPDATE public.portfolios
       SET is_active = TRUE, updated_at = NOW()
     WHERE user_id = p_user_id AND id = p_portfolio_id AND NOT is_active;

    RETURN TRUE;
END;
$$;

REVOKE ALL ON FUNCTION public.set_active_portfolio(UUID, UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.set_active_portfolio(UUID, UUID) TO service_role;


-- ── 5. ensure_active_portfolio — heal a user who has none ────────────────────────────────
-- Called after deleting the active group, from the seed-race backfill, and after the
-- guest→account claim. One place, so "the user always has an active group" is an invariant
-- rather than three similar code paths that can each forget.

CREATE OR REPLACE FUNCTION public.ensure_active_portfolio(p_user_id UUID)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_id UUID;
BEGIN
    -- Same per-user advisory lock as set_active_portfolio, and it has to be the SAME key or
    -- the two functions do not exclude each other. A `NOT EXISTS (an active row)` re-check
    -- is NOT sufficient on its own: it reads committed state, so an in-flight uncommitted
    -- activation is invisible to it, this UPDATE proceeds, and the index insert then blocks
    -- and raises 23505 when the other transaction commits.
    PERFORM pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

    SELECT id INTO v_id
      FROM public.portfolios
     WHERE user_id = p_user_id AND is_active
     LIMIT 1;

    IF FOUND THEN
        RETURN v_id;   -- already healthy; the common path is one indexed read
    END IF;

    -- Promote the user's first group, ordered the way the app lists them.
    --
    -- One statement with the pick as a sub-select, NOT `SELECT ... LIMIT 1 FOR UPDATE` into
    -- a variable: LIMIT is applied BEFORE the lock, so if the chosen row is deleted
    -- concurrently, EPQ drops it and the SELECT returns nothing rather than falling through
    -- to the next candidate — reporting "this user owns no groups" for a user who owns
    -- several. The documented caller is literally "after deleting the active group", so
    -- delete-versus-heal is the expected pair, not an exotic one.
    UPDATE public.portfolios
       SET is_active = TRUE, updated_at = NOW()
     WHERE id = (
             SELECT id
               FROM public.portfolios
              WHERE user_id = p_user_id
              ORDER BY sort_order, created_at, id
              LIMIT 1
           )
    RETURNING id INTO v_id;

    -- NULL here means the user genuinely owns no groups. `GET /portfolios` seeds one
    -- (`_seed_default_portfolio`, which inserts is_active = TRUE); every other caller
    -- treats NULL as "nothing to heal" and the surfaces fall back honestly.
    RETURN v_id;
END;
$$;

REVOKE ALL ON FUNCTION public.ensure_active_portfolio(UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ensure_active_portfolio(UUID) TO service_role;

COMMIT;
