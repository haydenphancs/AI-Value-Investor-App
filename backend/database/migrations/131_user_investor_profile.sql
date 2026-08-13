-- 131_user_investor_profile.sql
--
-- Why: the personalized-agent feature needs somewhere to keep what a reader tells us
-- about HOW THEY LIKE TO LEARN, so Cay AI can choose what to cover first and at what
-- reading level. Phase 3 only CAPTURES it — nothing reads this table yet.
--
-- ⚠️ WHAT THIS TABLE DELIBERATELY DOES NOT COLLECT, and why it is load-bearing:
-- no finances, no risk tolerance, no time horizon, no tax situation, no goals. Those
-- five are named verbatim in `persona_config.ADVICE_BOUNDARY` ("you do not know this
-- user's … and you must not assume or ask for them") and in the live Terms §2
-- ("not adapted to your portfolio, holdings, financial situation, risk tolerance, or
-- objectives"). Collecting them would turn a content-preference layer into a
-- SUITABILITY profile, which is the line between an educational publication and
-- personalized investment advice. Every column here is a *content* preference.
-- Do not add a risk-tolerance column to this table.
--
-- Every field is a CLOSED ENUM (or an array over a closed vocabulary), enforced here
-- by CHECK and again by Pydantic `Literal` at the edge and `sanitize_profile()` in
-- between. That is a security property, not tidiness: the rendered preference block is
-- injected into the chat SYSTEM instruction UNFENCED so it can actually steer tone and
-- ordering, and that is only safe while no user-authored byte can reach it. A free-text
-- column here would become a prompt-injection surface with steering power. If free text
-- is ever wanted it must live in a fenced, untrusted span instead — never in this table.
--
-- ── GUEST-WRITABLE: NO FOREIGN KEY (deliberate) ────────────────────────────────
-- The profile is captured during first-run onboarding, BEFORE an account exists, so
-- `user_id` holds either a real `public.users.id` or the synthetic per-install uuid5
-- from `dependencies.guest_user_id_for()`. A uuid5 has no `public.users` row, so an FK
-- would reject every guest INSERT. Same deliberate design as `user_learn_progress`,
-- `chat_usage_budget`, and the FKs dropped by migrations 108 / 110 / 111.
--
-- Per .claude/rules/auth.md §1a, a guest-writable table needs all four of these, and
-- the other three ship in the SAME change as this migration:
--   1. no FK to public.users                                        (here)
--   2. `_UNLINKED_USER_TABLES` entry in app/api/v1/endpoints/users.py
--      — the dropped cascade WAS the account-deletion path; without it a deleted
--        account's profile survives, which the privacy policy says it does not
--   3. a claim step in POST /users/me/claim-guest-data
--      — otherwise answering the onboarding questions and THEN signing up loses them
--   4. REVOKE from anon/authenticated + a service_role-only policy      (here)
--
-- ── Access model ───────────────────────────────────────────────────────────────
-- service-role ONLY, like `push_send_log` (109) and `analytics_events` (107). A guest
-- row's `user_id` is a synthetic uuid5 that `auth.uid()` can never equal, so the usual
-- `auth.uid() = user_id` policies would be dead on arrival for exactly the callers this
-- table exists to serve — and widening `USING` to `true` to "make guests work" would
-- expose every user's row to any holder of the shipped anon key. The backend reaches it
-- with the service-role key (app/database.py), so the API layer is the access control.
--
-- REVOKE explicitly rather than merely omitting the GRANT: Supabase projects commonly
-- carry `ALTER DEFAULT PRIVILEGES … GRANT ALL ON TABLES TO anon, authenticated`, so a
-- new public-schema table can arrive already granted (cf. 078/079/094/107/109).
--
-- No index beyond the PK: every access is `WHERE user_id = ?`, which the primary key
-- already serves. `user_id` is the PK, so one profile per identity and upsert is trivial.
--
-- Idempotent: safe to apply multiple times.
--
-- ROLLOUT: apply BEFORE deploying the code, because the code WRITES this table (a guest
-- finishing onboarding INSERTs immediately). That is the opposite of migration 130,
-- which was read-mostly and guarded; here a deploy that races ahead fails every profile
-- save. The endpoints degrade rather than 500, but the data is simply lost until applied.

BEGIN;

CREATE TABLE IF NOT EXISTS public.user_investor_profile (
    -- NO FK: holds a real users.id OR a synthetic per-install uuid5 (see header).
    user_id           UUID PRIMARY KEY,

    experience_level  TEXT NOT NULL DEFAULT 'learning'
        CHECK (experience_level IN ('new', 'learning', 'experienced')),

    explanation_style TEXT NOT NULL DEFAULT 'balanced'
        CHECK (explanation_style IN ('plain_language', 'balanced', 'technical')),

    answer_depth      TEXT NOT NULL DEFAULT 'brief'
        CHECK (answer_depth IN ('brief', 'standard', 'deep')),

    -- `<@` is "contained by": every element must be in the vocabulary. It is also TRUE
    -- for the empty array, which is the intended "skipped this question" value.
    topics            TEXT[] NOT NULL DEFAULT '{}'
        CHECK (topics <@ ARRAY[
            'value', 'growth', 'dividends', 'technology', 'energy',
            'healthcare', 'crypto', 'etf_index', 'macro', 'small_cap'
        ]::TEXT[]),

    learning_goals    TEXT[] NOT NULL DEFAULT '{}'
        CHECK (learning_goals <@ ARRAY[
            'read_financials', 'value_a_business', 'understand_charts',
            'understand_macro', 'follow_smart_money'
        ]::TEXT[]),

    follow_signals    TEXT[] NOT NULL DEFAULT '{}'
        CHECK (follow_signals <@ ARRAY[
            'whales', 'congress', 'insiders', 'earnings'
        ]::TEXT[]),

    -- Bumped when the QUESTION SET changes, so a stored answer can be re-asked rather
    -- than silently reinterpreted under a new meaning.
    profile_version   SMALLINT NOT NULL DEFAULT 1,

    -- When this user accepted the amended Terms covering preference-based
    -- personalization. NULL until they do; the feature must not apply a profile
    -- captured before the disclosure existed.
    consented_at      TIMESTAMPTZ,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.user_investor_profile ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_investor_profile_service_all" ON public.user_investor_profile;
CREATE POLICY "user_investor_profile_service_all" ON public.user_investor_profile
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON public.user_investor_profile FROM anon, authenticated;
GRANT ALL ON public.user_investor_profile TO service_role;

COMMENT ON TABLE public.user_investor_profile IS
    'How a reader prefers to LEARN (experience level, explanation style, answer depth, topics of interest). Content preferences only — deliberately no finances, risk tolerance, time horizon, tax situation or goals, so output stays impersonal. Closed enums only: the rendered block is injected UNFENCED into the chat system instruction, which is safe only while no user-authored text can reach it. Guest-writable, so user_id has NO FK and may be a synthetic per-install uuid5.';

COMMIT;
