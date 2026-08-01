-- 108_watchlist_guest_partition.sql
--
-- Why: EVERY signed-out user currently shares ONE watchlist (and one set of portfolios). `watchlist_items` is
-- written via `get_current_user_or_guest`, so all guests resolve to the single shared
-- `GUEST_USER_ID` sentinel. One guest adding NVDA puts it on every other guest's
-- Tracking tab, and either of them can delete the other's tickers.
--
-- This is the same defect `guest_user_id_for()` was introduced to fix for Learn
-- progress (migrations 066/067) — one install's completed lessons were being merged
-- into every other install's app. That fix was never extended to the watchlist.
--
-- It matters more now than it did: the app is guest-first by design, so at launch most
-- users ARE guests; and the watchlist is about to drive first-run onboarding and a
-- personalized Home strip, which would put one guest's tickers on every other guest's
-- most-visited screen.
--
-- The blocker was this FK: a per-INSTALL id is a synthetic UUID5 with no `public.users`
-- row, so it cannot satisfy `watchlist_items_user_id_fkey`. Same constraint that keeps
-- guests off `user_credits` (see migration 106). Dropping it matches the deliberate
-- no-FK design of `user_learn_progress` / `user_book_progress` / `chat_usage_budget`.
--
-- ⚠️ CONSEQUENCE — ACCOUNT DELETION. The dropped constraint was ON DELETE CASCADE, and
-- `DELETE /users/me` RELIED on that cascade to remove a user's watchlist; the table is
-- not in `_UNLINKED_USER_TABLES`. Without a matching code change, deleting an account
-- would silently leave the watchlist behind — the exact incomplete-deletion bug fixed
-- earlier in the launch prep, and a privacy-policy violation. `watchlist_items` is
-- added to `_UNLINKED_USER_TABLES` in `app/api/v1/endpoints/users.py` in the SAME
-- change. Do not apply this migration without that code deployed.
--
-- ⚠️ EXISTING GUEST ROWS BECOME INVISIBLE ON DEPLOY. An earlier draft of this comment
-- claimed headerless clients keep seeing them "so nothing breaks" — that is WRONG for
-- the real client: iOS sends `X-Guest-Id` on EVERY request (APIClient.buildRequest), so
-- after deploy every existing guest install resolves to its own new partition and its
-- watchlist renders EMPTY. The legacy `GUEST_USER_ID` rows survive but become
-- unreachable by any client (RLS leaves them service-role-only).
--
-- Accepted deliberately: there are 4 such rows, all from pre-launch testing, and that
-- shared list was wrong data to begin with. There is no backfill and no one-time claim.
-- Existing rows are left in place (decision recorded 2026-08-01); clear them in Studio
-- if you want the bucket empty.
--
-- ⚠️ NOT PRACTICALLY REVERSIBLE. Once guest rows exist, re-adding either FK fails
-- validation until every row whose user_id has no `public.users` match is deleted.
--
-- KNOWN, ACCEPTED: (a) no reaper — unlike chat_usage_budget (096) and
-- guest_report_budget (106), these rows are user-VISIBLE data, and deleting someone's
-- watchlist because they didn't open the app for N days is worse than the storage;
-- (b) signing in does NOT migrate a guest's watchlist to the new account (no claim/
-- merge exists) — pre-existing for Learn, but it now applies to Tracking too;
-- (c) `get_top_watchlist_tickers` becomes guest-WEIGHTED (one row per install instead
-- of one per ticker across all guests), so the pre-warm/sweeper universe will reshuffle
-- on deploy. Arguably more correct; expect it rather than debug it.
--
-- Also adds the ticker-leading index the reverse lookup needs ("which users watch
-- AAPL?"). All three existing indexes are user_id-leading, so that query is a seq scan
-- today. Bundled here because the watchlist-driven push work needs it next and it is
-- cheap to create alongside.

BEGIN;

-- Idempotent: IF EXISTS so a re-run after a partial apply is a no-op.
ALTER TABLE watchlist_items
    DROP CONSTRAINT IF EXISTS watchlist_items_user_id_fkey;

-- `portfolios` has to move WITH the watchlist, not after it. A portfolio is a
-- user-named subset of the master watchlist, and `POST /portfolios/{id}/items`
-- restricts additions to tickers already on that user's watchlist
-- (portfolios.py "Restrict to tickers already on the master watchlist"). If the
-- watchlist became per-install while portfolios stayed on the shared sentinel, a
-- guest's own tickers would never match and they could add NOTHING to a portfolio.
-- Guest portfolios are also shared between all guests today, for the same reason the
-- watchlist was — this fixes both together.
--
-- Same ON DELETE CASCADE consequence: `portfolios` joins _UNLINKED_USER_TABLES.
-- `portfolio_items` is unaffected — it FKs to portfolios(id), not to users, so it
-- still cascades when a portfolio row is deleted.
ALTER TABLE portfolios
    DROP CONSTRAINT IF EXISTS portfolios_user_id_fkey;

COMMENT ON COLUMN portfolios.user_id IS
    'A real public.users id, OR a per-INSTALL synthetic uuid from guest_user_id_for() '
    'for signed-out users. NO foreign key by design (migration 108), same as '
    'watchlist_items. Account deletion clears this table explicitly.';

COMMENT ON COLUMN watchlist_items.user_id IS
    'A real public.users id, OR a per-INSTALL synthetic uuid from guest_user_id_for() '
    'for signed-out users. NO foreign key by design (migration 108) — the synthetic id '
    'has no users row. Account deletion therefore clears this table EXPLICITLY via '
    '_UNLINKED_USER_TABLES, not via a cascade.';

-- "Which users watch this ticker?" — the reverse of every existing index.
CREATE INDEX IF NOT EXISTS idx_watchlist_items_ticker
    ON watchlist_items (ticker);

COMMIT;
