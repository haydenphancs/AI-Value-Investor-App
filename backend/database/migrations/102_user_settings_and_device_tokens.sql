-- 102_user_settings_and_device_tokens.sql
--
-- Why: the Account/Profile screen is being wired end-to-end. Two per-user needs
-- have no home yet:
--
--   1. user_settings  — the app's preferences (appearance mode + ~13 notification
--                        toggles + ~6 general prefs) live ONLY in iOS @AppStorage
--                        today, so they don't follow the user across devices/reinstalls.
--                        Stored as a single JSONB blob (not typed columns) because the
--                        keys are heterogeneous and churn as the UI evolves; the push
--                        service reads the `notify_*` keys out of it. One row per user.
--
--   2. device_tokens  — APNs registration. Real push needs the app to hand its
--                        device token to the backend so a future push worker can fan
--                        out per user. `token` is UNIQUE so a re-registration upserts
--                        (and re-binds the token to the current user after a
--                        sign-out/sign-in on a shared device).
--
-- Both are user-scoped: RLS lets a user read/write only their own rows; the service
-- role (backend) does everything. Safe to re-run — every statement is idempotent.

BEGIN;

-- ── 1. user_settings: per-user preference blob ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.user_settings (
    user_id     UUID PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "user_settings_select_own" ON public.user_settings;
CREATE POLICY "user_settings_select_own" ON public.user_settings
    FOR SELECT TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_settings_insert_own" ON public.user_settings;
CREATE POLICY "user_settings_insert_own" ON public.user_settings
    FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_settings_update_own" ON public.user_settings;
CREATE POLICY "user_settings_update_own" ON public.user_settings
    FOR UPDATE TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "user_settings_service_all" ON public.user_settings;
CREATE POLICY "user_settings_service_all" ON public.user_settings
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON public.user_settings TO authenticated;
GRANT ALL ON public.user_settings TO service_role;


-- ── 2. device_tokens: APNs push registration ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.device_tokens (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    token       TEXT NOT NULL,
    platform    TEXT NOT NULL DEFAULT 'ios',
    environment TEXT,                                  -- sandbox | production
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT device_tokens_token_key UNIQUE (token)  -- re-register upserts / re-binds user
);

-- Push send fans out by user; look up all of a user's tokens.
CREATE INDEX IF NOT EXISTS idx_device_tokens_user
    ON public.device_tokens (user_id);

ALTER TABLE public.device_tokens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "device_tokens_select_own" ON public.device_tokens;
CREATE POLICY "device_tokens_select_own" ON public.device_tokens
    FOR SELECT TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "device_tokens_insert_own" ON public.device_tokens;
CREATE POLICY "device_tokens_insert_own" ON public.device_tokens
    FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "device_tokens_update_own" ON public.device_tokens;
CREATE POLICY "device_tokens_update_own" ON public.device_tokens
    FOR UPDATE TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "device_tokens_delete_own" ON public.device_tokens;
CREATE POLICY "device_tokens_delete_own" ON public.device_tokens
    FOR DELETE TO authenticated USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "device_tokens_service_all" ON public.device_tokens;
CREATE POLICY "device_tokens_service_all" ON public.device_tokens
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.device_tokens TO authenticated;
GRANT ALL ON public.device_tokens TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.device_tokens_id_seq TO service_role;

COMMIT;
