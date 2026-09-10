-- 161_chat_starters.sql
--
-- Why: the suggestion chips above "Ask Cay AI…" have been five hardcoded strings since
-- launch — "Should I buy #AAPL?", "#Tech Stocks", "#Crypto" — identical on every open,
-- naming two tickers chosen a year ago. A TestFlight tester asked for questions that
-- change daily and reflect what is actually happening today. Rotating a pool that lives
-- in Swift would mean an App Store release per edit, so the pool lives here instead:
-- editors add, retire or reword a question in Supabase and the change is live after the
-- endpoint's 1-hour cache expires, with no client update.
--
-- The rotation itself is NOT stored. `app/services/daily_rotation.py` picks the day's
-- questions as a pure function of (pool, k, ET date), so there is no schedule table to
-- drift, no cron to miss, and any instance answers identically for the same day.
--
-- Schema: `slug` is the stable edit/upsert key (the seeder derives a uuid5 from it, so
-- re-running updates in place rather than duplicating). `scope` says which surface a
-- question belongs to: 'global' for the main chat, and one per asset detail screen whose
-- rows carry a literal {symbol} placeholder the client interpolates.
--
-- ⚠️ RLS is service-role ONLY, deliberately NOT the public-read `*_cache` template that
-- most content tables here use. `trending_themes` (081) granted anon SELECT and that was
-- reasonable for editorial card copy, but nothing client-side reads this table: iOS gets
-- these questions from GET /api/v1/chat/starters, which composes them with live market
-- data. The Supabase anon key ships inside the iOS binary, so a public grant would widen
-- the attack surface for zero benefit. Same posture as 157/158/159. If someone later
-- "fixes" this back to the template, they are adding an unused public read.
--
-- Deploy order does not matter: the service try/excepts the read and falls back to the
-- vendored backend/data/chat_starters.json, so before this migration is applied the
-- endpoint simply serves the bundled pool.

-- 1. Table -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.chat_starters (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE,            -- stable upsert key, e.g. "global-what-is-a-moat"
    text        text NOT NULL,                   -- the question; detail scopes carry {symbol}
    scope       text NOT NULL DEFAULT 'global',
    is_active   boolean NOT NULL DEFAULT true,   -- soft-retire without losing the row
    sort_order  integer NOT NULL DEFAULT 0,      -- editorial only; the walk sorts canonically
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    -- Closed vocabulary. A typo'd scope would silently create a pool nothing reads,
    -- which reads to an editor as "my question never showed up".
    CONSTRAINT chat_starters_scope_check CHECK (
        scope IN ('global', 'ticker', 'etf', 'crypto', 'commodity', 'index')
    ),
    -- A chip is a pill on one line. 140 is generous for that and still bounds what a
    -- bad paste can do to the row's layout.
    CONSTRAINT chat_starters_text_length CHECK (
        char_length(text) BETWEEN 4 AND 140
    )
);

COMMENT ON TABLE public.chat_starters IS
    'Editorial pool of starter questions for the Ask Cay AI chat. One row per question; '
    'scope says which surface it belongs to (global chat, or an asset detail bar, whose '
    'rows carry a literal {symbol} placeholder the client fills in). The DAY''S selection '
    'is not stored - app/services/daily_rotation.py derives it as a pure function of the '
    'pool and the ET date, so every instance agrees without coordination. Read only by '
    'app/services/chat_starters_service.py and served via GET /api/v1/chat/starters; '
    'seeded from backend/data/chat_starters.json by scripts/seed_chat_starters.py.';

COMMENT ON COLUMN public.chat_starters.scope IS
    'global | ticker | etf | crypto | commodity | index. Non-global rows MUST contain '
    'exactly one {symbol} placeholder; iOS drops any template it cannot fill rather than '
    'rendering a raw brace.';

COMMENT ON COLUMN public.chat_starters.sort_order IS
    'Editorial grouping only. It does NOT drive what the user sees: normalize_pool() '
    'sorts the pool canonically because PostgREST does not guarantee row order without an '
    'ORDER BY, and a position-dependent schedule would let two instances disagree on the '
    'same day.';

-- 2. Indexes -----------------------------------------------------------------
-- The only read this table gets: active rows for one scope (or all scopes at once).
CREATE INDEX IF NOT EXISTS idx_chat_starters_active_scope
    ON public.chat_starters(is_active, scope);

-- 3. RLS + grants (service-role only — see the header) -----------------------
ALTER TABLE public.chat_starters ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "chat_starters_service_all" ON public.chat_starters;
CREATE POLICY "chat_starters_service_all" ON public.chat_starters
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.chat_starters FROM anon, authenticated;
GRANT ALL  ON public.chat_starters TO service_role;
