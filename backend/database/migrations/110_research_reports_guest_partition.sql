-- 110_research_reports_guest_partition.sql
--
-- Why: EVERY SIGNED-OUT USER CURRENTLY SHARES ONE SET OF RESEARCH REPORTS.
--
-- `/research/*` resolved identity through `get_current_user_or_guest`, which returns the shared
-- `GUEST_USER_ID` sentinel for every guest, and `GET /research/reports` filters
-- `.eq("user_id", user["id"])`. So guest A generates a deep-research report on TSLA, and guest B
-- — a different person, a different device — opens the Reports tab and sees it: ticker,
-- executive summary, overall score, fair-value estimate. B can open it, rate it, and DELETE it.
-- The ticker someone researches is itself a disclosure of intent.
--
-- Same defect migration 108 fixed for `watchlist_items` / `portfolios`, and 066/067 for Learn
-- progress. It was never extended to research because `research_reports.user_id` carries a
-- foreign key to `public.users(id)` and a synthetic per-install uuid5 has no `public.users`
-- row — so the partitioning could not be switched on without dropping that FK first.
--
-- 🔴 SHIPS WITH THE CODE IN THE SAME RELEASE. Both halves are in this changeset:
--    * `app/dependencies.py`        — `get_research_identity` (per-install guest id)
--    * `app/api/v1/endpoints/research.py` — every route now uses it
--    * `app/api/v1/endpoints/users.py`    — `research_reports` added to `_UNLINKED_USER_TABLES`
--      and to the guest-data claim, because the cascade this migration drops was what deleted a
--      user's reports on account deletion.
--    Applying WITHOUT that code leaves account deletion silently orphaning reports.
--    Deploying that code WITHOUT this leaves every guest report INSERT failing the FK check.
--
-- Consequences to expect rather than debug:
--   * Existing GUEST reports become unreachable on deploy. They were written under the shared
--     sentinel, which no per-install identity resolves to — and that shared list was other
--     people's reports in the first place. Reports owned by real accounts are untouched.
--   * Not practically reversible once guest rows carry synthetic ids: re-adding the FK would
--     fail validation against them.
--   * `POST /users/me/claim-guest-data` now moves this install's guest reports onto the account
--     at sign-up, so the guest→account upgrade does not lose them.
--   * NO REAPER, same accepted trade-off migration 108 recorded: an abandoned install's rows
--     (and its `research-pdfs/reports/<synthetic-id>/` prefix) are never revisited, because
--     there is no account to delete. Bounded in practice by GUEST_REPORT_MONTHLY_LIMIT = 1.

BEGIN;

-- ── 1. Drop the FK that blocks a synthetic per-install user_id ────────────────
--
-- DESTRUCTIVE (constraint only — no rows are deleted): this removes the ON DELETE CASCADE that
-- account deletion relied on. `_UNLINKED_USER_TABLES` in app/api/v1/endpoints/users.py takes
-- over that responsibility in the same release. NOTE: the generic guard in
-- tests/test_account_deletion_completeness.py cannot cover this table — it classifies tables by
-- regexing FOREIGN KEY (user_id) out of schema_snapshot.sql, and the snapshot still predates
-- this migration, so research_reports reads as FK-linked and is skipped. The EXPLICIT assertion
-- lives in tests/test_research_guest_partition.py
-- (`test_research_reports_is_purged_explicitly_on_account_deletion`), which also documents why
-- the generic guard cannot cover it. Re-run backend/scripts/dump_schema.sh after applying this
-- and the generic guard will pick it up too.
ALTER TABLE public.research_reports
    DROP CONSTRAINT IF EXISTS research_reports_user_id_fkey;

COMMENT ON COLUMN public.research_reports.user_id IS
    'Owner. Deliberately NOT a foreign key (migration 110): holds either a real public.users id '
    'or a synthetic per-install uuid5 for signed-out guests, mirroring watchlist_items and '
    'portfolios (migration 108). Account deletion therefore purges this table EXPLICITLY via '
    '_UNLINKED_USER_TABLES in app/api/v1/endpoints/users.py — there is no cascade.';

-- ── 2. Indexes ───────────────────────────────────────────────────────────────
--
-- None needed: `idx_reports_user (user_id, created_at DESC)` already exists and is exactly the
-- lookup `GET /research/reports` performs. Deliberately NOT adding a second copy under a new
-- name — `IF NOT EXISTS` matches on name, not definition, so that would silently duplicate the
-- index and its write amplification (the cleanup precedent is 047_data_integrity_hardening).

COMMIT;
