-- 088_ai_insight_cache.sql
--
-- Why: the iOS Updates screen shows one AI "Insights" card per scope — a
-- watchlist ticker (AAPL, TSLA, …) or the reserved general-market key
-- '__MARKET__'. Each card is a Gemini roll-up of the ~25 most recent articles
-- already sitting in `ticker_news_cache`, so generating one costs an LLM call.
--
-- A fixed TTL is the wrong primitive here. It pays full price to regenerate a
-- byte-identical card on a quiet ticker, and it is STILL up to a full TTL late
-- on the crash the user actually cares about. Instead a background sweeper
-- (app/services/updates_insight_sweeper.py) re-evaluates every scope on a short
-- cadence during market hours and regenerates only when a deterministic
-- materiality predicate trips: the article-set fingerprint changed, or the
-- price crossed a band (±2% notable / ±5% large-cap / ±10% otherwise, per the
-- SEC LULD Plan tiers), or the once-per-trading-day close-cycle ceiling fired.
--
-- Three tables, deliberately:
--
--   1. ai_insight_cache      — the SERVED card. A row exists ONLY when Gemini
--      returned a fully validated card. No failure path has any reason to write
--      here, which makes the documented cache-poisoning incident
--      (news_cache_service.py: a neutral/empty-bullets fallback persisted with
--      ai_processed=true and poisoned a shared 6h cache for every user, with no
--      retry) architecturally unreachable rather than merely guarded by a Python
--      branch someone can refactor away. The CHECK constraints below are the
--      enforcement: a degraded card cannot be stored even by accident.
--
--   2. updates_insight_state — the sweeper's WORKING state: last fingerprint,
--      last price band, cooldowns, per-scope caps, and the cross-process claim
--      lock. This lives in Postgres and not a Python dict because Railway
--      restarts at will; in-memory cooldowns would evaporate on every deploy and
--      re-open the gate for every scope at once (a self-inflicted stampede).
--      Every skip decision persists its reason, so "why didn't AAPL refresh at
--      14:32?" is answerable with one SELECT instead of a log archaeology dig.
--
--   3. ai_insight_budget     — a durable global daily generation counter. This
--      is the hard ceiling on spend: even a pathological market-wide event
--      cannot exceed gen_count for the day.
--
-- Reads are public (non-sensitive aggregate market commentary, same posture as
-- signals_cache / ticker_news_cache); the two working tables are service_role
-- only. Idempotent: safe to re-apply. Apply manually (Supabase Studio / CLI).
--
-- Wrapped in an explicit transaction. psql autocommits per statement, so a
-- failure partway through would otherwise leave the database half-migrated —
-- tables created, RLS never enabled, grants never applied. There is no
-- CREATE INDEX CONCURRENTLY here, so a single transaction is safe.

BEGIN;


-- ─────────────────────────────────────────────────────────────────────
-- 1. The served card
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_insight_cache (
    id                BIGSERIAL PRIMARY KEY,
    -- 'AAPL' | 'BTCUSD' | '__MARKET__' — matches ticker_news_cache.ticker
    scope             TEXT        NOT NULL,
    headline          TEXT        NOT NULL,
    -- JSON array of 2-5 short strings
    bullets           JSONB       NOT NULL,
    sentiment         TEXT        NOT NULL,
    -- how many articles fed this roll-up (rendered as provenance in the badge)
    article_count     INTEGER     NOT NULL DEFAULT 0,
    -- sha256(sorted external_ids | price_band | prompt_version | model).
    -- Identical inputs cannot produce a different card, so an unchanged
    -- fingerprint is a PROOF that regenerating is wasted spend.
    inputset_id       TEXT        NOT NULL,
    prompt_version    INTEGER     NOT NULL DEFAULT 1,
    ai_model          TEXT        NOT NULL,
    -- which predicate branch fired ('luld_extreme -6.2%', 'new_articles', …).
    -- Persisted for tuning: after two weeks the real distribution tells us
    -- whether the thresholds are right.
    trigger_reason    TEXT,
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- the weekday-18:00-ET close cycle this card was generated in; drives the
    -- once-per-trading-day ceiling (see services/ticker_report_cache.py)
    close_cycle       TIMESTAMPTZ NOT NULL,
    -- soft: card is still served but flagged is_stale in the API response
    soft_expires_at   TIMESTAMPTZ NOT NULL,
    -- hard: stop serving the AI card entirely; fall back to the deterministic
    -- non-LLM card. Prevents a "stale forever" card if the sweeper dies.
    hard_expires_at   TIMESTAMPTZ NOT NULL,
    CONSTRAINT ai_insight_cache_scope_key UNIQUE (scope)
);

-- `CREATE TABLE IF NOT EXISTS` skips the ENTIRE statement when the table already
-- exists — including every inline CONSTRAINT. On a table that drifted (created
-- by hand, or by an earlier draft of this file) the poisoning guards described
-- above would silently be ABSENT while this migration reported success. So the
-- constraints are (re-)added separately, each in a catch-block, because Postgres
-- has no `ADD CONSTRAINT IF NOT EXISTS`.
--
-- The columns must be back-filled FIRST: `EXCEPTION WHEN duplicate_object`
-- catches 42710 only, so ADDing a constraint on a column a drifted table lacks
-- raises `undefined_column` (42703), which escapes the catch and aborts.
-- Deliberately nullable here (except where a DEFAULT is supplied) — adding a
-- NOT NULL column to a table that already has rows fails outright. A fresh
-- apply still gets NOT NULL from the CREATE TABLE above.
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS headline        TEXT;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS bullets         JSONB;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS sentiment       TEXT;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS article_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS inputset_id     TEXT;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS prompt_version  INTEGER NOT NULL DEFAULT 1;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS ai_model        TEXT;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS trigger_reason  TEXT;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS close_cycle     TIMESTAMPTZ;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS soft_expires_at TIMESTAMPTZ;
ALTER TABLE ai_insight_cache ADD COLUMN IF NOT EXISTS hard_expires_at TIMESTAMPTZ;

-- Promote the back-filled columns to NOT NULL. The ADD COLUMNs above must be
-- nullable to survive a table that already has rows, but leaving them that way
-- would quietly weaken the "a degraded card cannot be stored" guarantee on the
-- drift path only: a SQL CHECK passes on NULL (NULL-in, NULL-out), so an
-- all-NULL card would insert cleanly. A fresh apply already gets NOT NULL from
-- the CREATE TABLE; this makes it unconditional.
-- The inner catch is deliberate: if a legacy row holds a NULL, we want a WARNING
-- naming the column, not a rollback of the whole migration.
DO $$
DECLARE c TEXT;
BEGIN
    FOREACH c IN ARRAY ARRAY['headline','bullets','sentiment','inputset_id',
                             'ai_model','close_cycle','soft_expires_at','hard_expires_at'] LOOP
        BEGIN
            EXECUTE format('ALTER TABLE ai_insight_cache ALTER COLUMN %I SET NOT NULL', c);
        EXCEPTION WHEN others THEN
            RAISE WARNING 'ai_insight_cache.% left NULLable: %', c, SQLERRM;
        END;
    END LOOP;
END $$;

DO $$ BEGIN
    ALTER TABLE ai_insight_cache
        ADD CONSTRAINT ai_insight_cache_scope_key UNIQUE (scope);
EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL; END $$;

-- Case-INSENSITIVE on purpose. The card domain is Capitalized
-- ('Bullish'/'Bearish'/'Neutral', matching the iOS MarketSentiment enum) while
-- the source pipeline this reads from (ticker_news_cache, news_cache_service)
-- normalizes to lowercase. A strict-casing CHECK would turn that mismatch into
-- a hard write failure — i.e. no card at all — which is a worse outcome than
-- storing the value; the API layer and the iOS decoder both fold case anyway.
DO $$ BEGIN
    ALTER TABLE ai_insight_cache ADD CONSTRAINT ai_insight_sentiment_check
        CHECK (lower(sentiment) IN ('bullish', 'bearish', 'neutral'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- A degraded card (empty or over-long bullet list) cannot be persisted.
DO $$ BEGIN
    ALTER TABLE ai_insight_cache ADD CONSTRAINT ai_insight_bullets_check
        CHECK (jsonb_typeof(bullets) = 'array'
               AND jsonb_array_length(bullets) BETWEEN 2 AND 5);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- 240, not 160: the service already clips the headline, and a length overrun is
-- verbosity, not a degraded card. Making it a hard write failure would throw
-- away an otherwise-good card and leave the scope with none at all.
DO $$ BEGIN
    ALTER TABLE ai_insight_cache ADD CONSTRAINT ai_insight_headline_check
        CHECK (char_length(headline) BETWEEN 1 AND 240);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE ai_insight_cache ADD CONSTRAINT ai_insight_scope_check
        CHECK (char_length(scope) BETWEEN 1 AND 32);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE ai_insight_cache ADD CONSTRAINT ai_insight_article_count_check
        CHECK (article_count >= 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- soft must not outlive hard, or a card would be served un-flagged past the
-- point where it should have stopped being served at all.
DO $$ BEGIN
    ALTER TABLE ai_insight_cache ADD CONSTRAINT ai_insight_expiry_order_check
        CHECK (soft_expires_at <= hard_expires_at);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Lookup by scope is already served by the UNIQUE index. What is NOT served is
-- the expiry sweep (`WHERE hard_expires_at < now()`), which cannot use a
-- scope-leading index.
DROP INDEX IF EXISTS idx_ai_insight_cache_lookup;
CREATE INDEX IF NOT EXISTS idx_ai_insight_cache_expiry
    ON ai_insight_cache(hard_expires_at);


-- ─────────────────────────────────────────────────────────────────────
-- 2. Sweeper working state
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS updates_insight_state (
    id                    BIGSERIAL PRIMARY KEY,
    scope                 TEXT        NOT NULL,
    -- how many users watch this ticker; used to prioritise admission when the
    -- global daily budget binds
    watch_count           INTEGER     NOT NULL DEFAULT 0,

    -- ── gate memory ──
    last_inputset_id      TEXT,
    last_price_band       TEXT,
    last_trigger_reason   TEXT,
    last_skip_reason      TEXT,
    last_evaluated_at     TIMESTAMPTZ,
    last_generated_at     TIMESTAMPTZ,
    close_cycle           TIMESTAMPTZ,

    -- ── debounce / budget (durable across Railway restarts) ──
    regen_day             DATE,
    -- SUCCESSFUL generations today. Only incremented on a validated card, so a
    -- run of transient Gemini 429s can never pin a scope for the rest of the day.
    regen_count_today     INTEGER     NOT NULL DEFAULT 0,
    -- ATTEMPTS today (successes + failures). Failures burn this instead.
    attempts_today        INTEGER     NOT NULL DEFAULT 0,
    last_failure_at       TIMESTAMPTZ,
    last_error            TEXT,

    -- ── cross-process singleflight ──
    -- Set immediately before the Gemini call; a stale claim self-heals after
    -- 120s (same two-threshold pattern as research_reconciliation_service's
    -- processing_started_at). Two Railway instances therefore cannot both pay
    -- for the same card.
    claim_at              TIMESTAMPTZ,

    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT updates_insight_state_scope_key UNIQUE (scope)
);

-- Same drifted-table back-fill as ai_insight_cache above.
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS watch_count          INTEGER NOT NULL DEFAULT 0;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS last_inputset_id     TEXT;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS last_price_band      TEXT;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS last_trigger_reason  TEXT;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS last_skip_reason     TEXT;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS last_evaluated_at    TIMESTAMPTZ;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS last_generated_at    TIMESTAMPTZ;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS close_cycle          TIMESTAMPTZ;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS regen_day            DATE;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS regen_count_today    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS attempts_today       INTEGER NOT NULL DEFAULT 0;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS last_failure_at      TIMESTAMPTZ;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS last_error           TEXT;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS claim_at             TIMESTAMPTZ;
ALTER TABLE updates_insight_state ADD COLUMN IF NOT EXISTS updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW();

DO $$ BEGIN
    ALTER TABLE updates_insight_state
        ADD CONSTRAINT updates_insight_state_scope_key UNIQUE (scope);
EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE updates_insight_state
        ADD CONSTRAINT updates_insight_state_scope_check
        CHECK (char_length(scope) BETWEEN 1 AND 32);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Scope lookups are served by the UNIQUE index; a (scope, claim_at) index would
-- be redundant. What the sweeper actually scans is "unclaimed work, oldest
-- first", which is scope-agnostic:
DROP INDEX IF EXISTS idx_updates_insight_state_claim;
CREATE INDEX IF NOT EXISTS idx_updates_insight_state_due
    ON updates_insight_state(last_evaluated_at)
    WHERE claim_at IS NULL;

-- Priority ordering when the global budget binds.
CREATE INDEX IF NOT EXISTS idx_updates_insight_state_watch
    ON updates_insight_state(watch_count DESC);

-- Atomic claim. This MUST be a single statement in the database.
--
-- The obvious client-side version — read the row, then write back
-- `attempts_today = <read value> + 1` — has an ABA bug that silently defeats the
-- daily cap: instance A can complete a whole claim→generate→release cycle while
-- B is calling Gemini, and because `claim_at` returns to NULL, B's conditional
-- write still matches and stamps its STALE counters over A's increment. Nor can
-- PostgREST express a column-relative update (`x = x + 1`), so there is no
-- pure-SDK fix. Everything — the day roll, both caps, the stale-claim steal, and
-- the increment — happens here under one row lock.
--
-- `claim_at <= p_now - interval` also handles clock skew correctly: a
-- FUTURE-dated claim (another instance's clock running ahead) fails the
-- predicate and is left alone, instead of being stolen mid-generation and
-- billed twice.
CREATE OR REPLACE FUNCTION claim_updates_insight_scope(
    p_scope TEXT,
    p_now TIMESTAMPTZ,
    p_stale_seconds INTEGER,
    p_attempt_cap INTEGER,
    p_daily_cap INTEGER
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_ok BOOLEAN;
    v_today DATE := (p_now AT TIME ZONE 'UTC')::date;
BEGIN
    INSERT INTO updates_insight_state (scope, regen_day, updated_at)
    VALUES (p_scope, v_today, p_now)
    ON CONFLICT (scope) DO NOTHING;

    UPDATE updates_insight_state s
       SET regen_day = v_today,
           attempts_today    = CASE WHEN s.regen_day IS DISTINCT FROM v_today
                                    THEN 1 ELSE s.attempts_today + 1 END,
           regen_count_today = CASE WHEN s.regen_day IS DISTINCT FROM v_today
                                    THEN 0 ELSE s.regen_count_today END,
           claim_at = p_now,
           updated_at = p_now
     WHERE s.scope = p_scope
       AND (s.claim_at IS NULL
            OR s.claim_at <= p_now - make_interval(secs => p_stale_seconds))
       AND (s.regen_day IS DISTINCT FROM v_today
            OR (s.attempts_today < p_attempt_cap
                AND s.regen_count_today < p_daily_cap))
    RETURNING TRUE INTO v_ok;

    RETURN COALESCE(v_ok, FALSE);
END;
$$;

-- Atomic success counter. Incrementing from a value read BEFORE the (multi-
-- second) Gemini call is a lost update: two instances would both write N+1.
CREATE OR REPLACE FUNCTION increment_updates_insight_success(p_scope TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    UPDATE updates_insight_state
       SET regen_count_today = regen_count_today + 1,
           updated_at = NOW()
     WHERE scope = p_scope
    RETURNING regen_count_today INTO v_count;
    RETURN COALESCE(v_count, 0);
END;
$$;


-- ─────────────────────────────────────────────────────────────────────
-- 3. Durable global daily budget (single row per day)
-- ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ai_insight_budget (
    budget_day  DATE    PRIMARY KEY,
    gen_count   INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Atomic increment + read-back. Doing this as a SELECT-then-UPDATE from Python
-- races between Railway instances and silently under-counts, which would let
-- spend drift past the ceiling. SECURITY DEFINER so the service role can call it
-- regardless of RLS on the table.
CREATE OR REPLACE FUNCTION increment_ai_insight_budget(p_day DATE, p_limit INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    -- Guard the INSERT path. The cap below only constrains the DO UPDATE branch,
    -- so without this a limit of 0 (an intended kill switch) or NULL (a missing
    -- env var) would still grant the day's FIRST generation — the one case where
    -- the operator most wants zero.
    IF p_limit IS NULL OR p_limit <= 0 THEN
        RETURN -1;
    END IF;

    INSERT INTO ai_insight_budget (budget_day, gen_count, updated_at)
    VALUES (p_day, 1, NOW())
    ON CONFLICT (budget_day) DO UPDATE
        SET gen_count = ai_insight_budget.gen_count + 1,
            updated_at = NOW()
        WHERE ai_insight_budget.gen_count < p_limit
    RETURNING gen_count INTO v_count;

    -- No row returned => the WHERE on DO UPDATE failed => the cap is reached.
    -- Return -1 so the caller can distinguish "budget exhausted" from "count=N".
    IF v_count IS NULL THEN
        RETURN -1;
    END IF;
    RETURN v_count;
END;
$$;


-- ─────────────────────────────────────────────────────────────────────
-- RLS + grants
-- ─────────────────────────────────────────────────────────────────────

ALTER TABLE ai_insight_cache      ENABLE ROW LEVEL SECURITY;
ALTER TABLE updates_insight_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_insight_budget     ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "ai_insight_cache_public_read" ON ai_insight_cache;
CREATE POLICY "ai_insight_cache_public_read" ON ai_insight_cache
    FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "ai_insight_cache_service_write" ON ai_insight_cache;
CREATE POLICY "ai_insight_cache_service_write" ON ai_insight_cache
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Working state and budget are backend-internal: no anon/authenticated access.
DROP POLICY IF EXISTS "updates_insight_state_service_all" ON updates_insight_state;
CREATE POLICY "updates_insight_state_service_all" ON updates_insight_state
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "ai_insight_budget_service_all" ON ai_insight_budget;
CREATE POLICY "ai_insight_budget_service_all" ON ai_insight_budget
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON ai_insight_cache TO anon, authenticated;
GRANT ALL ON ai_insight_cache      TO service_role;
GRANT ALL ON updates_insight_state TO service_role;
GRANT ALL ON ai_insight_budget     TO service_role;
GRANT USAGE, SELECT ON SEQUENCE ai_insight_cache_id_seq      TO service_role;
GRANT USAGE, SELECT ON SEQUENCE updates_insight_state_id_seq TO service_role;

-- SECURITY DEFINER functions are granted EXECUTE to PUBLIC by Postgres default,
-- and Supabase exposes every public-schema function at POST /rest/v1/rpc/<name>.
-- Without these REVOKEs, anyone holding the shipped anon key could loop
-- `increment_ai_insight_budget` to exhaust the daily cap and switch the Insights
-- feature off for every user — bypassing RLS by design (row_security = off).
-- REVOKE must come BEFORE the GRANT, and both are idempotent.
REVOKE ALL ON FUNCTION increment_ai_insight_budget(DATE, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION increment_ai_insight_budget(DATE, INTEGER) TO service_role;

REVOKE ALL ON FUNCTION increment_updates_insight_success(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION increment_updates_insight_success(TEXT) TO service_role;

REVOKE ALL ON FUNCTION claim_updates_insight_scope(TEXT, TIMESTAMPTZ, INTEGER, INTEGER, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_updates_insight_scope(TEXT, TIMESTAMPTZ, INTEGER, INTEGER, INTEGER) TO service_role;

COMMIT;
