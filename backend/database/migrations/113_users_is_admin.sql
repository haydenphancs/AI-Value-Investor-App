-- 113_users_is_admin.sql
--
-- Why: ADMIN ACCESS IS CURRENTLY GRANTED BY EMAIL CLAIM, AND THE EMAIL IS UNCLAIMED.
--
-- `app/api/v1/endpoints/admin.py` authorized on a hardcoded allowlist:
--
--     _ADMIN_EMAILS: set[str] = {"haiphan@caydexinvest.com", "admin@caydexinvest.com"}
--     ...
--     if user and user.get("email") in _ADMIN_EMAILS:
--         return
--
-- The backend's own registration gate is correct — it refuses to mint tokens unless
-- `email_confirmed_at` is set (`auth.py:291`). But Supabase POPULATES that field
-- automatically when the project's "Confirm email" setting is OFF, which is its current
-- state (LAUNCH_CHECKLIST §5b(b), unticked). `auth.py:300` says so in as many words:
-- "Confirmation disabled project-side: a real session exists, so behave as before."
--
-- So today: register `admin@caydexinvest.com` — an address nobody holds, since the mailboxes
-- in §2 of the checklist are also not set up — receive a real session, and every admin route
-- opens. The benchmark recompute endpoints are expensive (full FMP universe sweeps), and the
-- status routes disclose internal counts.
--
-- Turning on "Confirm email" closes it, and that must happen regardless. But an authorization
-- control whose only guard is a dashboard checkbox in another product is not a control. This
-- migration moves the decision into the database, where registering an address cannot grant
-- it.
--
-- Ships with `admin.py` switching from the email allowlist to this flag. Order does not
-- matter for safety: applying this migration alone changes nothing (nothing reads the column
-- yet), and deploying the code alone fails CLOSED — every admin route starts answering 403
-- until the flag is set, which is a locked door, not an open one.
--
-- AFTER APPLYING, VERIFY THE RIGHT ROW WAS FLAGGED:
--
--     select id, email, is_admin, created_at from public.users where is_admin;
--
-- The seeding statement below matches on the historical allowlist. If it flags a row you do
-- not recognise, that address was already registered by someone else — clear it immediately
-- (`update public.users set is_admin = false where id = '<row>';`), and treat it as evidence
-- the escalation above was exercised.

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN public.users.is_admin IS
    'Grants access to /api/v1/admin/*. Set manually — never by registration, signup trigger, '
    'or any application code path. See migration 113.';

-- Partial index: the only query is "is this user an admin", and admins are a handful of rows.
CREATE INDEX IF NOT EXISTS idx_users_is_admin
    ON public.users (id) WHERE is_admin;

-- Seed from the allowlist admin.py used to hardcode, so existing access is not interrupted.
-- Scoped to rows that already exist; this creates nothing.
UPDATE public.users
   SET is_admin = TRUE
 WHERE email IN ('haiphan@caydexinvest.com', 'admin@caydexinvest.com')
   AND is_admin IS DISTINCT FROM TRUE;

-- `is_admin` must never be client-writable. `users` already has RLS with per-user policies,
-- and a self-UPDATE policy would let a signed-in user set their own flag. Revoke the column
-- privilege explicitly so that stays true even if a broad UPDATE policy is added later.
REVOKE UPDATE (is_admin) ON public.users FROM anon, authenticated;
