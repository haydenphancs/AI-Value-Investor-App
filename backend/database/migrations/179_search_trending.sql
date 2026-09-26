-- 179_search_trending.sql
--
-- Why: every ticker-search screen gets two chip rows above the keyboard — "Trending searches"
-- and "Most added", both over a rolling 7 days (product decision 2026-09-26). Neither signal
-- existed: the app stored NO search activity at all (App Privacy: "Search History — not
-- collected"), and analytics_events (107) is guest-writable with a client-chosen identity, so
-- a list built from it could be pushed around by anyone rotating a header.
--
--   A. public.search_pick_daily — ANONYMOUS daily counters. One row per (ET day, ticker,
--      asset type) holding a de-duplicated pick count. A "pick" is a tap on a search RESULT
--      row by a signed-in account — never a keystroke, never a tap on a trending chip (that
--      would feed the list its own output). There is NO user, device, IP or session column,
--      and one must never be added: the user chose counters over per-user rows precisely so
--      that nothing here is linked to anyone, nothing needs deleting on account deletion, and
--      App Privacy can say "Search History — not linked to you".
--
--      De-duplication happens BEFORE the write, twice: on the device (one pick per ticker per
--      7 ET days, remembered in UserDefaults) and in server memory (a keyed digest per
--      account + ticker + CLASS, never persisted — `search_pick_service`). The class is
--      crypto vs everything else, the same split C sums over: keyed on the declared type,
--      one account could send AAPL as stock, etf and fund and reach the floor alone. So
--      `picks` is "de-duplicated picks", NOT a proven count of people: a server restart plus
--      a second device or a reinstall can count one person twice. That gap is stated, not
--      hidden (see the column COMMENT and SYSTEM_DESIGN_GUIDELINES §10).
--
--      NO TIMESTAMP COLUMN, on purpose. `day` is the only time dimension. A precise
--      `updated_at` on a counter that usually reads 1 matches exactly one request in the
--      access logs — which carry the client IP — and so would re-link a person to a ticker.
--
--   B. public.increment_search_pick(date, text, text) — the atomic upsert-increment (173's
--      increment_marketing_link_hits shape). SECURITY INVOKER: its only caller is
--      service_role, which holds the table grant, so it needs no owner rights and is never an
--      escalation path. It refuses a day more than one day away from ET today, so a bug (or a
--      replay) cannot backfill history.
--
--   C. public.get_search_trending(date, integer, integer) — the 7-day ranking. The PRIVACY
--      FLOOR LIVES HERE, not only in Python: `GREATEST(p_min_picks, 3)`, so no caller can ask
--      for a ticker only one or two people picked. stock/etf/fund rows for one symbol are ONE
--      security that some client declared differently, so they are summed and labelled by the
--      majority type; a coin is always its own row (BTC the coin is not BTC the ETF).
--
--   D. public.get_most_added_tickers(timestamptz, integer, integer, integer) — the 7-day
--      "Most added" ranking over watchlist_items, which already records every add
--      (`added_at`). It returns NO name: watchlist_items.company_name is client-writable
--      (POST /tracking/holdings), so the latest adder could otherwise rename a chip for
--      every user. The backend names items from the active-listing directory instead. Like
--      C, a symbol's stock and etf rows are ONE security (writers disagree on the class) —
--      counted together, labelled by the most common kind. Portfolio holdings are always a subset of the watchlist (every add is
--      mirrored into the active group) and portfolio_items.added_at is reset by every
--      PUT /portfolios/{id}/tickers, so the watchlist is the one honest source. Exact
--      distinct accounts: COUNT(DISTINCT user_id), floor GREATEST(p_min_users, 3). It:
--        * JOINs public.users, which drops the legacy guest rows (their user_id is a uuid5
--          with no users row — migration 108);
--        * drops adds made in an account's first 24 h — onboarding's suggested chips would
--          otherwise BE the list;
--        * drops admin accounts (testing);
--        * lowercases asset_type (the column default is 'Stock') and keeps stock/etf/crypto;
--        * turns a stored coin pair back into the search spelling (BTCUSD → BTC, migration
--          160) — only a trailing USD is stripped, so the coin "USD" itself survives.
--
--   E. idx_watchlist_items_added_at — D scans a global added_at range; the existing
--      (user_id, added_at) index leads with user_id and cannot serve it.
--
--   F. RLS + grants: service_role only (the 162/173 template). iOS never reads any of this
--      directly; the backend serves the lists through GET /api/v1/search/trending.
--
-- Deploy order: apply BEFORE the backend deploy that calls these functions. The code FAILS
-- OPEN if it ships first: the lists fall back to the curated "Popular" set and picks are
-- dropped (logged once), and search itself never touches this migration.
--
-- Idempotent: IF NOT EXISTS on the table and index, CREATE OR REPLACE on the functions,
-- DROP POLICY IF EXISTS before CREATE, and REVOKE/GRANT are declarative. Safe to re-run.
--
-- VERIFY (run after applying):
--   SELECT has_function_privilege('anon',
--          'public.increment_search_pick(date, text, text)', 'EXECUTE');            -- expect f
--   SELECT has_function_privilege('authenticated',
--          'public.get_search_trending(date, integer, integer)', 'EXECUTE');       -- expect f
--   SELECT has_function_privilege('anon',
--          'public.get_most_added_tickers(timestamptz, integer, integer, integer)',
--          'EXECUTE');                                                              -- expect f
--   SELECT relname, relrowsecurity FROM pg_class WHERE relname = 'search_pick_daily';
--                                                                                   -- expect t
--   SELECT * FROM public.get_search_trending(CURRENT_DATE - 6, 1, 5);
--                                          -- the floor still applies: nothing under 3 comes back

BEGIN;

-- ── A. Anonymous daily counters ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.search_pick_daily (
    -- ET calendar day of the pick.
    day        DATE        NOT NULL,
    -- The symbol as search spells it (BRK-B, BTC). Same shape the stock routes accept.
    ticker     TEXT        NOT NULL CHECK (ticker ~ '^[A-Z0-9][A-Z0-9.-]{0,9}$'),
    -- Must equal app.schemas.search_trending.PICK_TYPES (test-pinned).
    asset_type TEXT        NOT NULL CHECK (asset_type IN ('stock', 'etf', 'fund', 'crypto')),
    picks      INTEGER     NOT NULL DEFAULT 0 CHECK (picks >= 0),
    -- `day` leads, so the 7-day window read is a range scan on the primary key.
    PRIMARY KEY (day, ticker, asset_type)
);

COMMENT ON TABLE public.search_pick_daily IS
    'Anonymous daily counters behind the "Trending searches" chips: one row per (ET day, '
    'ticker, asset type) with a de-duplicated count of search-result taps by signed-in '
    'accounts. No user, device, IP or session column exists and none may ever be added — '
    'nothing here is linked to a person — and no timestamp beyond the day, which could be '
    'matched against request logs. Written by app/services/search_pick_service.py '
    '(increment_search_pick), read by app/services/search_trending_service.py '
    '(get_search_trending); rows older than 14 days are swept.';

COMMENT ON COLUMN public.search_pick_daily.picks IS
    'De-duplicated picks (one per account per ticker per 7 ET days, enforced on the device '
    'and in server memory) — NOT a proven count of distinct people: a server restart plus a '
    'second device or a reinstall can count one person twice.';

-- ── B. The atomic increment ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.increment_search_pick(
    p_day        DATE,
    p_ticker     TEXT,
    p_asset_type TEXT
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_today DATE := (now() AT TIME ZONE 'America/New_York')::date;
    v_picks INTEGER;
BEGIN
    IF p_day IS NULL OR p_day < v_today - 1 OR p_day > v_today + 1 THEN
        RAISE EXCEPTION 'increment_search_pick: p_day % is not within one day of ET today %',
            p_day, v_today
            USING ERRCODE = '22023';  -- invalid_parameter_value
    END IF;

    INSERT INTO public.search_pick_daily (day, ticker, asset_type, picks)
    VALUES (p_day, p_ticker, p_asset_type, 1)
    ON CONFLICT (day, ticker, asset_type) DO UPDATE
        SET picks = search_pick_daily.picks + 1
    RETURNING picks INTO v_picks;

    RETURN v_picks;
END;
$$;

COMMENT ON FUNCTION public.increment_search_pick(DATE, TEXT, TEXT) IS
    'Atomic upsert-increment of one anonymous (day, ticker, asset_type) pick counter; returns '
    'the new count. INVOKER: its only caller is service_role. Raises 22023 for a day more '
    'than one day from ET today.';

REVOKE ALL ON FUNCTION public.increment_search_pick(DATE, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.increment_search_pick(DATE, TEXT, TEXT) TO service_role;

-- ── C. "Trending searches" — 7-day ranking with the privacy floor ────────────────
CREATE OR REPLACE FUNCTION public.get_search_trending(
    p_since     DATE,
    p_min_picks INTEGER,
    p_limit     INTEGER
)
RETURNS TABLE (ticker TEXT, asset_type TEXT, picks BIGINT)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
    -- ⚠️ Every column is alias-qualified: the RETURNS TABLE names are OUT parameters, and an
    -- unqualified `ticker` would be ambiguous.
    WITH per_type AS (
        SELECT s.ticker, s.asset_type, SUM(s.picks)::BIGINT AS picks
          FROM public.search_pick_daily s
         WHERE s.day >= p_since
         GROUP BY s.ticker, s.asset_type
    ),
    per_asset AS (
        SELECT p.ticker,
               -- One security, several declared types: label it by the majority.
               (array_agg(p.asset_type
                          ORDER BY p.picks DESC, (p.asset_type = 'stock') DESC, p.asset_type))[1]
                   AS asset_type,
               SUM(p.picks)::BIGINT AS picks
          FROM per_type p
         GROUP BY p.ticker, (p.asset_type = 'crypto')
    )
    SELECT a.ticker, a.asset_type, a.picks
      FROM per_asset a
     WHERE a.picks >= GREATEST(COALESCE(p_min_picks, 3), 3)
     ORDER BY a.picks DESC, a.ticker ASC
     LIMIT LEAST(GREATEST(COALESCE(p_limit, 20), 1), 100);
$$;

COMMENT ON FUNCTION public.get_search_trending(DATE, INTEGER, INTEGER) IS
    'Tickers ranked by de-duplicated search picks since p_since. Never returns a ticker below '
    '3 picks, whatever p_min_picks says. Capped at 100 rows.';

REVOKE ALL ON FUNCTION public.get_search_trending(DATE, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_search_trending(DATE, INTEGER, INTEGER) TO service_role;

-- ── D. "Most added" — 7-day distinct accounts from the watchlist ─────────────────
CREATE OR REPLACE FUNCTION public.get_most_added_tickers(
    p_since                  TIMESTAMPTZ,
    p_min_users              INTEGER,
    p_limit                  INTEGER,
    p_min_account_age_hours  INTEGER DEFAULT 24
)
RETURNS TABLE (ticker TEXT, asset_type TEXT, adders BIGINT)
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
    WITH adds AS (
        SELECT wi.user_id,
               lower(coalesce(wi.asset_type, 'stock')) AS kind,
               upper(wi.ticker) AS raw_ticker
          FROM public.watchlist_items wi
          JOIN public.users u ON u.id = wi.user_id   -- drops legacy guest rows
         WHERE wi.added_at >= p_since
           AND wi.added_at >= u.created_at
                              + make_interval(hours => GREATEST(COALESCE(p_min_account_age_hours, 24), 0))
           AND NOT u.is_admin
           AND lower(coalesce(wi.asset_type, 'stock')) IN ('stock', 'etf', 'crypto')
    ),
    normalized AS (
        SELECT d.user_id,
               d.kind,
               -- Coins are stored as the pair (BTCUSD, migration 160); search spells BTC.
               CASE WHEN d.kind = 'crypto' AND length(d.raw_ticker) > 3
                         AND right(d.raw_ticker, 3) = 'USD'
                    THEN left(d.raw_ticker, length(d.raw_ticker) - 3)
                    ELSE d.raw_ticker
               END AS symbol
          FROM adds d
    )
    SELECT n.symbol AS ticker,
           -- One security, rows declared differently by different writers: count them
           -- together and label by the most common kind (ties → 'etf' before 'stock').
           mode() WITHIN GROUP (ORDER BY n.kind) AS asset_type,
           COUNT(DISTINCT n.user_id)::BIGINT AS adders
      FROM normalized n
     GROUP BY n.symbol, (n.kind = 'crypto')
    HAVING COUNT(DISTINCT n.user_id) >= GREATEST(COALESCE(p_min_users, 3), 3)
     ORDER BY COUNT(DISTINCT n.user_id) DESC, n.symbol ASC
     LIMIT LEAST(GREATEST(COALESCE(p_limit, 20), 1), 100);
$$;

COMMENT ON FUNCTION public.get_most_added_tickers(TIMESTAMPTZ, INTEGER, INTEGER, INTEGER) IS
    'Tickers added to watchlists since p_since by at least 3 distinct real accounts (never '
    'fewer, whatever p_min_users says), excluding each account''s first p_min_account_age_hours '
    '(onboarding) and admin accounts. A symbol''s stock and etf rows count as one security; a '
    'coin is its own row, returned in search spelling (BTCUSD → BTC). Returns no name: '
    'company_name is client-writable. Capped at 100 rows.';

REVOKE ALL ON FUNCTION public.get_most_added_tickers(TIMESTAMPTZ, INTEGER, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_most_added_tickers(TIMESTAMPTZ, INTEGER, INTEGER, INTEGER)
    TO service_role;

-- ── E. Index for the global added_at range ───────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_watchlist_items_added_at
    ON public.watchlist_items (added_at DESC);

-- ── F. RLS + grants: service_role only ───────────────────────────────────────────
ALTER TABLE public.search_pick_daily ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "search_pick_daily_service_all" ON public.search_pick_daily;
CREATE POLICY "search_pick_daily_service_all" ON public.search_pick_daily
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.search_pick_daily FROM anon, authenticated;
-- A table with no GRANT is owner-only, not open (169's lesson). No sequence: composite key.
GRANT ALL ON public.search_pick_daily TO service_role;

COMMIT;
