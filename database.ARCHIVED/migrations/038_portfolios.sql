-- Migration 038: portfolios table
--
-- Named groupings of tickers within a user's master watchlist. The first time
-- a user calls GET /portfolios with no rows on file, the server lazily seeds a
-- portfolio called "Holdings" containing every ticker on their existing
-- watchlist_items rows. After that, the user manages portfolios manually.
--
-- Unique on (user_id, name) so a user can't accidentally end up with two
-- portfolios called "Tech". Renames must respect this; portfolios.py validates
-- before issuing the UPDATE.

CREATE TABLE IF NOT EXISTS portfolios (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_portfolios_user
    ON portfolios(user_id, sort_order);

ALTER TABLE portfolios ENABLE ROW LEVEL SECURITY;

CREATE POLICY portfolios_owner ON portfolios
    USING (user_id = auth.uid());
