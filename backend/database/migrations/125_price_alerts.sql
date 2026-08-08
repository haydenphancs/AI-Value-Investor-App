-- 125_price_alerts.sql
--
-- NOTE ON THE NUMBER: this started life as 121 and was renumbered twice — a parallel
-- session's credits migration kept landing on the same number as each of us re-checked
-- "the highest + 1" at the same moment. 125 leaves a deliberate gap so the two lines of
-- work cannot collide again. Gaps are already normal here (033 is one).
--
-- Why: user-set price alerts are the single most-expected notification in a finance
-- app — Robinhood, Webull and Yahoo Finance all ship them — and Caydex has none. The
-- bell already exists in `TickerDetailHeader.swift` and every one of the five detail
-- screens passes it `nil`, so it has never rendered.
--
-- Everything the app sends today is something the SYSTEM decided was interesting. This
-- is the first notification a user asks for by name, which is why it is also the only
-- one that ships `time-sensitive` (it pierces Focus) and skips quiet hours: a price
-- alert that arrives after the move is over is worthless.
--
-- ACCOUNT-ONLY, FK-BOUND — and that is a decision, not an oversight.
-- `device_tokens` is FK-bound to public.users and auth-only, so push can never reach a
-- guest. A guest-owned alert row could never deliver its payload. Making this table
-- guest-writable would cost all four items on database.md's checklist (drop the FK, add
-- to _UNLINKED_USER_TABLES, add to claim-guest-data, service-role-only RLS) to enable a
-- rule that cannot fire. By auth.md §1a's tier test this is "durable cross-device
-- identity" → .signInRequired, the same tier as settings, devices and whale-follow.
-- Consequence: no _UNLINKED_USER_TABLES entry and no claim-guest-data line — the
-- account-deletion test derives its purge list from the FK scan and classifies this as
-- linked automatically.
--
-- ON THE STATE COLUMNS (`armed`, `last_price`) — these are what make the evaluator
-- correct rather than merely functional:
--
--   * `last_price` NULL means "never observed". The engine SEEDS it and does NOT fire.
--     Without that, an alert created at "above $250" on a stock already trading at $260
--     fires on the very first evaluation cycle — a notification about nothing, one
--     second after the user tapped Save.
--   * `armed` is a hysteresis latch. Firing is a CROSSING, not a level, but a stock
--     oscillating 249.99 / 250.01 crosses forty times in an afternoon. On fire the latch
--     drops and only re-arms once price retreats past a band below the threshold.

BEGIN;

CREATE TABLE IF NOT EXISTS price_alerts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    ticker       TEXT        NOT NULL,

    -- Carried so the notification payload can name the asset type. The tap handler
    -- hardcoded `type: .stock`, so a crypto alert opened a stock screen; the route now
    -- reads this instead.
    asset_type   TEXT        NOT NULL DEFAULT 'stock',

    kind         TEXT        NOT NULL,   -- price_above | price_below | percent_move
    threshold    NUMERIC     NOT NULL,   -- dollars for above/below, absolute % for percent_move
    repeat_mode  TEXT        NOT NULL DEFAULT 'once',   -- once | daily
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,

    armed        BOOLEAN     NOT NULL DEFAULT TRUE,
    last_price   NUMERIC,                -- NULL = never observed → SEED, DO NOT FIRE
    last_evaluated_at TIMESTAMPTZ,
    last_triggered_at TIMESTAMPTZ,
    trigger_count INTEGER    NOT NULL DEFAULT 0,

    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT price_alerts_kind_check
        CHECK (kind IN ('price_above', 'price_below', 'percent_move')),
    CONSTRAINT price_alerts_repeat_check
        CHECK (repeat_mode IN ('once', 'daily')),

    -- ⚠️ POSTGRES `NUMERIC` ACCEPTS 'NaN'. It is a valid value of the type, not a parse
    -- error. `threshold = threshold` is FALSE for NaN and TRUE for every real number —
    -- that self-comparison IS the NaN guard. Without it a NaN threshold makes every
    -- comparison in the evaluator false, so the alert never fires and the user sees a
    -- row that simply does nothing, forever, with no error anywhere.
    CONSTRAINT price_alerts_threshold_check
        CHECK (threshold > 0 AND threshold = threshold),

    CONSTRAINT price_alerts_trigger_count_check CHECK (trigger_count >= 0),

    -- One rule per (user, ticker, kind, threshold). A double-tap on Save must not create
    -- two identical alerts that then both fire.
    CONSTRAINT price_alerts_no_dupes UNIQUE (user_id, ticker, kind, threshold)
);

COMMENT ON TABLE price_alerts IS
    'User-set price rules evaluated by the 60s price-alert loop. `armed` is a hysteresis '
    'latch (a stock oscillating around the threshold must fire ONCE); `last_price` NULL '
    'means never-observed, which SEEDS rather than fires.';

-- The evaluator''s universe query: SELECT DISTINCT ticker WHERE is_active.
CREATE INDEX IF NOT EXISTS idx_price_alerts_active_ticker
    ON price_alerts (ticker) WHERE is_active;

-- The per-cycle fetch: every active rule for the tickers just quoted.
CREATE INDEX IF NOT EXISTS idx_price_alerts_eval
    ON price_alerts (ticker, is_active, armed);

-- The user''s list screen, and the per-user count limit check on create.
CREATE INDEX IF NOT EXISTS idx_price_alerts_user
    ON price_alerts (user_id, created_at DESC);


-- ── RLS + grants ─────────────────────────────────────────────────────────────
-- Service-role only. The iOS app never touches Supabase directly — every call goes
-- through APIClient → FastAPI → the service key — and the endpoints scope every read and
-- write with `.eq("user_id", ...)`, which is the effective wall (RLS is defence in
-- depth here, per SYSTEM_DESIGN_GUIDELINES §9).

ALTER TABLE price_alerts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "price_alerts_service_all" ON price_alerts;
CREATE POLICY "price_alerts_service_all" ON price_alerts
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- REVOKE explicitly rather than merely omitting the GRANT: Supabase projects commonly
-- carry `ALTER DEFAULT PRIVILEGES ... GRANT ALL ON TABLES TO anon, authenticated`, so a
-- new public-schema table can arrive already granted (cf. 078/079/094/107/109/119).
REVOKE ALL ON price_alerts FROM anon, authenticated;

GRANT ALL ON price_alerts TO service_role;

COMMIT;
