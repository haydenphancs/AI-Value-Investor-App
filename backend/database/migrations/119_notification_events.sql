-- 119_notification_events.sql
--
-- Why: `push_send_log` (migration 109) is a pure dedup ledger — (user_id, dedup_key,
-- sent_at) and nothing else. That was exactly right for one sender. It cannot express
-- four things the notification overhaul needs:
--
--   1. PER-CATEGORY daily caps. `PushDispatchService.alerts_sent_today` counts EVERY
--      row, so the moment earnings / smart-money / price alerts exist they land in the
--      same 3-per-day bucket as a price move and one noisy category silently starves
--      the rest. Counting by parsing `dedup_key` would couple the cap to a key shape
--      the CALLERS own and are free to change (109's own header says so).
--   2. An in-app INBOX. A push that arrives while the phone is face-down is gone
--      forever; there is no record of what fired. The ledger already writes one row per
--      notification — it needs the copy stored beside the claim.
--   3. QUIET HOURS that DEFER rather than drop. A deferred notification has to live
--      somewhere durable, with a wake time, and survive a Railway redeploy.
--   4. OBSERVABILITY. Today there is no device-free way to prove a sender fired, for
--      whom, or why it didn't. Every suppression reason is a log line at best. With
--      this table "did it fire and why not" is one SQL query — which is what makes the
--      new senders verifiable without a physical iPhone.
--
-- ONE table, not three beside push_send_log. Two tables would mean two writes per
-- notification and a divergent ledger the moment the second write fails; the quiet-hours
-- deferral needs a durable stateful row regardless; and the cap query needs `category`
-- on the ledger either way.
--
-- ON THE FOREIGN KEY — this table DIVERGES from push_send_log deliberately.
-- 109 omitted the FK to match chat_usage_budget/guest_report_budget, which hold
-- per-install GUEST uuid5s. This table cannot: `device_tokens` is FK-bound and
-- auth-only, so a push can never reach a guest, and every row here is addressed to a
-- real `public.users` id. The FK is therefore both correct and free, and ON DELETE
-- CASCADE is the right deletion story. It also keeps this table OUT of
-- `_UNLINKED_USER_TABLES` — tests/test_account_deletion_completeness.py derives that
-- list by scanning for `user_id ... REFERENCES public.users` and will classify this
-- table as linked automatically. Adding it to the purge list would fail that test.
--
-- push_send_log is NOT dropped here. It stays in place, unread by the new code, and is
-- removed in a later migration once this path has soaked — which keeps the entire
-- rollback code-only. Deploy the cutover OUTSIDE market hours: the only live sender is
-- gated on is_market_active(), so a closed market means zero chance of a same-day
-- duplicate across the ledger switch.

BEGIN;

CREATE TABLE IF NOT EXISTS notification_events (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,

    -- THE CLAIM. UNIQUE(user_id, dedup_key) is the lock, exactly as push_send_log's
    -- composite PK was: INSERT before the send, treat a 23505 as "already handled".
    -- Composed by the caller; encodes what "the same alert" means for that kind.
    dedup_key     TEXT        NOT NULL,

    -- Registry identity. `kind` is the NotificationKind key ("earnings_upcoming") and
    -- is also what ships as data["kind"] for the iOS router; `category` is its cap
    -- bucket ("earnings"). Denormalised on purpose: the per-recipient cap probe must
    -- not join, and editing the registry must not retroactively re-bucket history a
    -- cap has already counted.
    kind          TEXT        NOT NULL,
    category      TEXT        NOT NULL,

    -- Inbox payload — what APNs was asked to render.
    title         TEXT        NOT NULL,
    body          TEXT        NOT NULL,

    -- The tap target, shipped verbatim as the APNs `data` dict. FLAT SCALARS ONLY:
    -- the iOS AnyCodable decoder handles String/Int/Double/Bool and silently yields ""
    -- for anything nested, so a nested dict arrives as garbage (.claude/rules/auth.md §3).
    route         JSONB       NOT NULL DEFAULT '{}'::jsonb,

    -- Delivery state machine.
    --   pending    claimed, not yet attempted (transient; a crash leaves a row here)
    --   deferred   inside the user's quiet hours; wake at deliver_after
    --   sent       APNs accepted at least one device
    --   no_device  user has no registered token — the INBOX row is still valid
    --   dry_run    PUSH_DRY_RUN was on; inbox row written, no APNs POST
    --   failed     APNs rejected every device, or the row went stale in deferral
    push_state    TEXT        NOT NULL DEFAULT 'pending',
    deliver_after TIMESTAMPTZ,
    attempts      INTEGER     NOT NULL DEFAULT 0,
    last_error    TEXT,

    claimed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Stamped ONLY on a real delivery. The per-category cap counts rows with
    -- sent_at IS NOT NULL, so a deferred, capped or dry-run row never consumes a
    -- user's daily budget. This is why the cap and the claim can stay separate.
    sent_at       TIMESTAMPTZ,
    read_at       TIMESTAMPTZ,

    CONSTRAINT notification_events_dedup_unique UNIQUE (user_id, dedup_key),
    CONSTRAINT notification_events_state_check CHECK (
        push_state IN ('pending', 'deferred', 'sent', 'no_device', 'dry_run', 'failed')
    ),
    CONSTRAINT notification_events_attempts_check CHECK (attempts >= 0)
);

COMMENT ON TABLE notification_events IS
    'One row per notification DECIDED (not merely delivered). UNIQUE(user_id, dedup_key) '
    'is the dedup claim: insert BEFORE sending, treat a conflict as "already handled". '
    'Doubles as the in-app inbox (title/body/route/read_at), the per-category cap ledger '
    '(category + sent_at), and the quiet-hours deferral queue (push_state/deliver_after). '
    'Swept on a retention window.';

-- Inbox list: newest first, per user.
CREATE INDEX IF NOT EXISTS idx_notification_events_inbox
    ON notification_events (user_id, claimed_at DESC);

-- Unread badge count. Partial — unread is a small minority of rows.
CREATE INDEX IF NOT EXISTS idx_notification_events_unread
    ON notification_events (user_id) WHERE read_at IS NULL;

-- Per-CATEGORY daily cap probe. THE hot path: one lookup per recipient per candidate.
CREATE INDEX IF NOT EXISTS idx_notification_events_category_sent
    ON notification_events (user_id, category, sent_at) WHERE sent_at IS NOT NULL;

-- Quiet-hours flush loop: "which deferred rows are due?" across ALL users.
CREATE INDEX IF NOT EXISTS idx_notification_events_due
    ON notification_events (deliver_after) WHERE push_state = 'deferred';

-- Retention sweep.
CREATE INDEX IF NOT EXISTS idx_notification_events_claimed_at
    ON notification_events (claimed_at);


-- ── Deferral flush claim ─────────────────────────────────────────────────────
-- Cross-instance safe hand-out of due deferred rows.
--
-- FOR UPDATE SKIP LOCKED is what makes this correct with more than one Railway
-- instance: two dispatch loops firing in the same second never grab the same row.
-- A bare PostgREST `.update().eq("push_state","deferred")` would also serialize under
-- READ COMMITTED, but it has no LIMIT and would hand ONE instance the entire backlog
-- while the other idles — and PostgREST cannot express `attempts = attempts + 1`.

CREATE OR REPLACE FUNCTION claim_due_notifications(
    p_now   TIMESTAMPTZ,
    p_limit INTEGER
)
RETURNS SETOF notification_events
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $$
BEGIN
    RETURN QUERY
    UPDATE notification_events e
       SET push_state = 'pending',
           attempts   = e.attempts + 1
     WHERE e.id IN (
        SELECT s.id
          FROM notification_events s
         WHERE s.push_state = 'deferred'
           AND s.deliver_after IS NOT NULL
           AND s.deliver_after <= p_now
         ORDER BY s.deliver_after
         FOR UPDATE SKIP LOCKED
         LIMIT GREATEST(p_limit, 0)
     )
    RETURNING e.*;
END;
$$;

-- Same posture as 089's REVOKE/GRANT pair, and for the same reason: on a database
-- where this function is ABSENT (a rebuild, a restore, a partial replay) CREATE OR
-- REPLACE *creates* it with the SECURITY DEFINER default of EXECUTE TO PUBLIC, and
-- Supabase exposes every public-schema function at POST /rest/v1/rpc/<name>. The anon
-- key could then flip arbitrary deferred rows to 'pending' with row_security off.
REVOKE ALL ON FUNCTION claim_due_notifications(TIMESTAMPTZ, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_due_notifications(TIMESTAMPTZ, INTEGER) TO service_role;


-- ── RLS + grants ─────────────────────────────────────────────────────────────
-- Backend-internal working table. The iOS app never touches Supabase directly — every
-- read goes through APIClient → FastAPI → the service key — so anon/authenticated need
-- no grant even though this backs a user-facing inbox.

ALTER TABLE notification_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "notification_events_service_all" ON notification_events;
CREATE POLICY "notification_events_service_all" ON notification_events
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- REVOKE explicitly rather than merely omitting the GRANT: Supabase projects commonly
-- carry `ALTER DEFAULT PRIVILEGES ... GRANT ALL ON TABLES TO anon, authenticated`, so a
-- new public-schema table can arrive already granted. RLS would still block the rows,
-- but that leaves a single layer (cf. migrations 078/079/094/107/109).
REVOKE ALL ON notification_events FROM anon, authenticated;

GRANT ALL ON notification_events TO service_role;

COMMIT;
