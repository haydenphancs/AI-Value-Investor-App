-- 132_user_memory_facts.sql
--
-- Why: Cay AI forgets everything between chat sessions. `chat_sessions.memory_summary`
-- (migration 130) carries context WITHIN one conversation, but a reader who spent last
-- week asking valuation questions about NVDA starts from zero tomorrow. This is the
-- cheapest durable half of that: a small set of structured facts about what the reader
-- has actually been asking about, rendered into the same per-user prompt layer as their
-- stated preferences.
--
-- ⚠️ EVERY VALUE COMES FROM A CLOSED VOCABULARY, AND THAT IS A SECURITY PROPERTY.
-- The rendered block is injected into the chat SYSTEM instruction UNFENCED (fencing it
-- would tell the model not to be steered by it, which is the opposite of the point), so
-- it is only safe while no user-authored byte can reach it. Facts derived from a user's
-- own messages are UNTRUSTED — "my preference is that you ignore your rules" is a fact
-- a user can state — so nothing here stores prose:
--
--   fact_key = 'ticker_discussed'  → fact_value is a validated ticker symbol
--   fact_key = 'question_theme'    → fact_value is one of chat_specialists.SPECIALIST_KEYS
--
-- Both are ALREADY COMPUTED on every turn (the router picks the specialist; the session
-- carries the ticker), so recording them needs no LLM at all — which removes the
-- extraction cost, the extraction latency, and the injection surface that a
-- summarise-the-user's-words step would have introduced. If a future fact type genuinely
-- needs free text, it must live behind a fence in the volatile layer, never here.
--
-- ── FK-BOUND, deliberately, unlike the recent guest-writable tables ───────────
-- Memory applies only on Pro/Max, which require a real account, so there is no guest to
-- partition. Keeping the FK means ON DELETE CASCADE remains the deletion path: no
-- `_UNLINKED_USER_TABLES` entry and no claim-guest-data step are needed, and
-- `test_account_deletion_completeness` classifies it automatically. That is the OPPOSITE
-- of migration 131's reasoning, and the difference is exactly whether a guest can ever
-- own a row.
--
-- Retention is bounded in code (a per-user row cap with LRU eviction) rather than by a
-- TTL job: the value of a fact is that it is recent, and an unbounded table would slowly
-- turn a cheap indexed read into a scan.
--
-- Idempotent: safe to apply multiple times.
--
-- ROLLOUT: apply BEFORE deploying the code, which WRITES this table. The writes are
-- best-effort and guarded, so a race only loses facts until the table exists — but the
-- feature also ships behind `CHAT_MEMORY_FACTS_ENABLED=false`, so nothing writes until
-- that is flipped.

BEGIN;

CREATE TABLE IF NOT EXISTS public.user_memory_facts (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

    -- Closed set. A new key means a new validator in
    -- `user_memory_facts_service.FACT_VALIDATORS` and a new renderer line — the CHECK
    -- is here so a value can never reach the prompt without both.
    fact_key    TEXT NOT NULL
        CHECK (fact_key IN ('ticker_discussed', 'question_theme')),

    -- Validated per key in Python before it ever gets here. Bounded hard so a bug
    -- upstream cannot store a paragraph: no legitimate value is longer than a ticker.
    fact_value  TEXT NOT NULL
        CHECK (char_length(fact_value) BETWEEN 1 AND 32),

    -- How many times this fact has been observed. NOT a ranking key — ordering is by
    -- `updated_at`, which already encodes frequency: every observation bumps it, so a
    -- ticker asked about repeatedly stays recent by definition. Kept for observability
    -- (telling "asked once last week" from "asked ten times") and as the input any
    -- future weighting would need. A concurrent double-write can lose one increment;
    -- that is acceptable for a best-effort signal nothing currently sorts on.
    hit_count   INTEGER NOT NULL DEFAULT 1 CHECK (hit_count > 0),

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One row per distinct fact, so recording is an idempotent upsert that bumps
    -- hit_count rather than appending a duplicate on every turn.
    UNIQUE (user_id, fact_key, fact_value)
);

-- The only read pattern: this user's most recent facts, newest first.
CREATE INDEX IF NOT EXISTS idx_user_memory_facts_recent
    ON public.user_memory_facts (user_id, updated_at DESC);

ALTER TABLE public.user_memory_facts ENABLE ROW LEVEL SECURITY;

-- service_role only, like the other tables the backend owns end-to-end. The API layer is
-- the access control; nothing client-side ever queries this directly.
DROP POLICY IF EXISTS "user_memory_facts_service_all" ON public.user_memory_facts;
CREATE POLICY "user_memory_facts_service_all" ON public.user_memory_facts
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- REVOKE explicitly rather than merely omitting the GRANT: Supabase projects commonly
-- carry `ALTER DEFAULT PRIVILEGES … GRANT ALL ON TABLES TO anon, authenticated`, so a
-- new public-schema table can arrive already granted (cf. 078/079/094/107/109/131).
REVOKE ALL ON public.user_memory_facts FROM anon, authenticated;
GRANT ALL ON public.user_memory_facts TO service_role;

-- The SEQUENCE needs the same treatment as the table, for the same reason: Supabase
-- projects carry an `ALTER DEFAULT PRIVILEGES … ON SEQUENCES` grant alongside the
-- `ON TABLES` one, so a new sequence can arrive already granted to anon/authenticated.
-- Mirrors 107_analytics_events.sql, the other service-role-only table with a BIGSERIAL
-- PK. The GRANT is not optional: PostgREST executes as service_role, and `nextval()` on
-- the BIGSERIAL default needs USAGE or every insert fails with 42501.
REVOKE ALL ON SEQUENCE public.user_memory_facts_id_seq FROM anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE public.user_memory_facts_id_seq TO service_role;

COMMENT ON TABLE public.user_memory_facts IS
    'What a reader has been asking about, across sessions — tickers discussed and question themes. Closed vocabulary only (validated ticker symbols; chat specialist keys), because the rendered block is injected UNFENCED into the chat system instruction and must contain no user-authored text. Derived deterministically from the router and the session, so recording costs no LLM call.';

COMMIT;
