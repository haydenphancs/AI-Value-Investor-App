-- 162_chat_starter_answers.sql
--
-- Why: the daily suggestion chips (161) are the questions people are most likely to tap,
-- and they are known in advance — so their answers can be computed BEFORE anyone asks and
-- served instantly. A background pass generates each of the day's global chips once and
-- stores the answer here; tapping one then replays a stored row instead of paying a full
-- Gemini turn plus, for a big mover, a grounded web search.
--
-- ⚠️ THE KEY IS THE QUESTION, NOT THE SLOT — and that is the whole design.
-- `chat_starters_service` rebuilds its set every `_RESPONSE_TTL_SECONDS` (900s), and the
-- hot-ticker / hot-sector slots track the live tape, so the day's questions DRIFT intraday:
-- "Why is NAVN down 22% today?" at 10:00 can be a different chip by 14:00. Keying on a slot
-- index (or on "today's chip set") would serve a stale answer under a fresh question, which
-- is worse than not pre-warming at all. Keying on the question text makes drift
-- self-correcting: a newly promoted chip simply misses and is warmed on the next pass, and
-- a chip that rotates out leaves a row nobody reads until the daily sweep removes it.
--
-- `question_hash` is a SHA-256 over the NFKC-normalised, case-folded question, computed in
-- `app/services/chat_starter_warm_service.py`. Hash rather than the raw text as the key so
-- the primary key is fixed-width regardless of question length; the text is stored beside
-- it for debugging and to make an accidental collision visible.
--
-- Schema: (question_hash, answer_date) is the primary key. `answer_date` is the ET trading
-- day, matching the rotation's own day boundary (`trading_date_et`) — NOT UTC, or the chips
-- and their answers would roll over at different moments and every evening would serve
-- yesterday's answers under today's questions.
--
-- RETENTION: one day. The sweep in `main.py` deletes `answer_date < today`, alongside the
-- existing `chat_usage_budget` sweep. Yesterday's answer is not merely stale, it is WRONG —
-- "Why is X down 22% today?" answered with yesterday's catalyst is exactly the class of
-- error `daily_move_attribution` exists to prevent.
--
-- ⚠️ RLS is service-role ONLY, deliberately NOT the public-read `*_cache` template.
-- These rows contain FMP-derived market data and Gemini-written prose about it, and the
-- Supabase anon key ships inside the iOS binary — a public grant would be redistribution
-- under FMP ToS 2.6.1 and would also serve market data to a caller with no account, which
-- End-User Display Rights forbid. iOS never reads this table: it reaches these answers only
-- through the account-gated chat endpoint. Same posture as 157/158/159/161.
--
-- 🔒 THE ROWS ARE GLOBALLY SHARED, SO NOTHING PER-USER MAY ENTER THEM. One row serves every
-- caller, exactly as `chat_starters_service`'s own header requires of the questions. The
-- warm job therefore runs with no user id, no personalisation and no memory facts — see
-- that service. `redact_signals()` is per-request, so a Pro-gated signal reaching this table
-- would be served to Free users with no filter left to catch it.
--
-- Deploy order does not matter: the warm service try/excepts both the read and the write,
-- so before this migration is applied chat simply answers every question live, as it does
-- today.

CREATE TABLE IF NOT EXISTS public.chat_starter_answers (
    question_hash TEXT        NOT NULL,
    answer_date   DATE        NOT NULL,
    question      TEXT        NOT NULL,
    answer        TEXT        NOT NULL,
    -- The inline card (stock chart / market overview) the live turn would have rendered,
    -- so a replayed answer is visually identical to a generated one.
    widget        JSONB,
    suggestions   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    tokens_used   INTEGER,
    model         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (question_hash, answer_date)
);

COMMENT ON TABLE public.chat_starter_answers IS
    'Pre-computed answers to the day''s Ask Cay AI suggestion chips, so tapping one replays '
    'a stored answer instead of paying a Gemini turn (and possibly a grounded web search). '
    'Keyed on the QUESTION, not the chip slot, because the chip set drifts intraday as the '
    'hot-ticker and hot-sector slots track the tape. One ET day of retention: yesterday''s '
    'answer to a "today" question is wrong, not merely stale. Written by '
    'app/services/chat_starter_warm_service.py; read by the chat streaming endpoint.';

COMMENT ON COLUMN public.chat_starter_answers.question_hash IS
    'SHA-256 of the NFKC-normalised, case-folded question text. Fixed-width key; the raw '
    'question is kept alongside so a collision would be visible rather than silent.';
COMMENT ON COLUMN public.chat_starter_answers.answer_date IS
    'ET trading day, matching the rotation''s own boundary. UTC here would roll the answers '
    'and the questions over at different moments.';

-- Supports the daily retention sweep, which is the only query that does not use the PK.
CREATE INDEX IF NOT EXISTS idx_chat_starter_answers_day
    ON public.chat_starter_answers (answer_date);

ALTER TABLE public.chat_starter_answers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "chat_starter_answers_service_all" ON public.chat_starter_answers;
CREATE POLICY "chat_starter_answers_service_all" ON public.chat_starter_answers
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.chat_starter_answers FROM anon, authenticated;
GRANT ALL ON public.chat_starter_answers TO service_role;
