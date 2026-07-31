-- 106_guest_report_budget.sql
--
-- Why: signed-out users could generate UNLIMITED AI ticker reports for free, while a
-- signed-in Free user gets 2/month. Two problems in one:
--
--   1. Denial of wallet (OWASP LLM10). `GET /stocks/{ticker}/report` skips the credit
--      precharge for guests (the credit RPCs early-return on the shared guest
--      sentinel — migration 101 — because debiting it would drain ONE pool for every
--      signed-out user at once). A cache MISS costs ~17 Gemini + ~20 FMP calls, and
--      the cache is keyed (ticker, persona), so a loop over distinct tickers never
--      hits it. There was no ceiling of any kind.
--   2. Inverted funnel. Signing in strictly REDUCED what you could do, so the app
--      gave users a reason never to create an account.
--
-- This adds a DURABLE per-INSTALL monthly report allowance for guests. In-memory
-- counters were rejected: they evaporate on every Railway deploy and don't span
-- replicas, which is exactly the window an abusive caller would use.
--
-- Companion in-process controls (app/config.py), which this complements rather than
-- replaces: REPORT_RATE_LIMIT_PER_MINUTE (burst) and REPORT_GET_MAX_INFLIGHT
-- (concurrency admission → 409 SYSTEM_BUSY).
--
-- Mirrors the 096/097 chat_usage_budget template: atomic claim-and-cap RPC + a
-- release RPC for the failure path, SECURITY DEFINER, service-role only,
-- REVOKE-before-GRANT.
--
-- NOTE ON bucket_key: like chat_usage_budget, this table intentionally has NO foreign
-- key to public.users. The key is the per-INSTALL synthetic id from
-- guest_user_id_for() (UUID5 over an opaque X-Guest-Id), which by design has no users
-- row. This is precisely why guest metering cannot reuse `user_credits` — that table
-- IS FK-bound to public.users (user_credits_user_id_fkey).
--
-- CAVEAT — callers that send NO X-Guest-Id: guest_user_id_for() returns the shared
-- GUEST_USER_ID for them, so they ALL share one row and one monthly allowance
-- between them. The iOS client sets the header unconditionally on every request
-- (APIClient.buildRequest), so in practice this bucket holds only scripted/direct
-- callers — for whom collapsing to the single strictest bucket is the desired
-- outcome, not a regression. It fails safe (too strict, never too permissive), and
-- omitting the header can never be used to BYPASS the cap.
--
-- Schema: (bucket_key, period_month) PK. period_month is the FIRST day of the month
-- (date_trunc'd by the caller) so the allowance rolls monthly without a cron job.

BEGIN;

CREATE TABLE IF NOT EXISTS guest_report_budget (
    bucket_key   UUID    NOT NULL,
    period_month DATE    NOT NULL,
    report_count INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bucket_key, period_month)
);

COMMENT ON TABLE guest_report_budget IS
    'Per-INSTALL monthly AI-report allowance for signed-out users. bucket_key is the '
    'synthetic guest id from guest_user_id_for() and deliberately has NO FK to '
    'public.users. Authenticated users are metered by user_credits instead.';

-- A retention sweep deletes by period_month; the PK already serves point lookups
-- on (bucket_key, period_month).
CREATE INDEX IF NOT EXISTS idx_guest_report_budget_period
    ON guest_report_budget (period_month);


-- Atomic claim-a-report: increment report_count for (bucket, month) and return the
-- new count, or -1 when the cap is already reached (or the limit is a kill-switch
-- <= 0). A SELECT-then-UPDATE from Python races between Railway instances and
-- under-counts, letting spend drift past the ceiling.
CREATE OR REPLACE FUNCTION claim_guest_report(
    p_bucket_key UUID,
    p_period     DATE,
    p_limit      INTEGER
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    -- Guard the INSERT path: the cap only constrains the DO UPDATE branch, so without
    -- this a limit of 0 (intended kill switch) or NULL (missing env var) would still
    -- grant the month's FIRST report — the one case an operator most wants blocked.
    IF p_limit IS NULL OR p_limit <= 0 THEN
        RETURN -1;
    END IF;

    INSERT INTO guest_report_budget (bucket_key, period_month, report_count, updated_at)
    VALUES (p_bucket_key, p_period, 1, NOW())
    ON CONFLICT (bucket_key, period_month) DO UPDATE
        SET report_count = guest_report_budget.report_count + 1,
            updated_at = NOW()
        WHERE guest_report_budget.report_count < p_limit
    RETURNING report_count INTO v_count;

    -- No row returned => the WHERE on DO UPDATE failed => the cap is reached.
    IF v_count IS NULL THEN
        RETURN -1;
    END IF;
    RETURN v_count;
END;
$$;


-- Release (refund) a claim when generation did not deliver. Symmetric with the credit
-- refund an AUTHENTICATED user already gets on the same failure paths — a Gemini
-- outage must not burn a guest's whole monthly allowance on reports they never saw.
-- GREATEST floors at 0 so a stray double-release can never drive the count negative
-- and silently grant extra reports.
CREATE OR REPLACE FUNCTION release_guest_report(
    p_bucket_key UUID,
    p_period     DATE
)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    UPDATE guest_report_budget
       SET report_count = GREATEST(0, report_count - 1),
           updated_at = NOW()
     WHERE bucket_key = p_bucket_key
       AND period_month = p_period
    RETURNING report_count INTO v_count;

    -- No row (never claimed this month) => nothing to release.
    RETURN COALESCE(v_count, 0);
END;
$$;


-- ── RLS + grants ─────────────────────────────────────────────────────────────
-- Backend-internal working table: no anon/authenticated access at all (same posture
-- as chat_usage_budget / ai_insight_budget).

ALTER TABLE guest_report_budget ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "guest_report_budget_service_all" ON guest_report_budget;
CREATE POLICY "guest_report_budget_service_all" ON guest_report_budget
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT ALL ON guest_report_budget TO service_role;

-- SECURITY DEFINER functions are EXECUTE-to-PUBLIC by Postgres default, and Supabase
-- exposes every public function at POST /rest/v1/rpc/<name>. Without these REVOKEs a
-- holder of the shipped anon key could loop release_guest_report to grant itself
-- unlimited reports (or loop claim_guest_report to exhaust a victim's allowance),
-- bypassing RLS by design (row_security = off). REVOKE must precede GRANT; both are
-- idempotent.
REVOKE ALL ON FUNCTION claim_guest_report(UUID, DATE, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_guest_report(UUID, DATE, INTEGER) TO service_role;

REVOKE ALL ON FUNCTION release_guest_report(UUID, DATE) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION release_guest_report(UUID, DATE) TO service_role;

COMMIT;
