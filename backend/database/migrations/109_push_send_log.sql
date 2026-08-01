-- 109_push_send_log.sql
--
-- Why: the insight sweeper is about to send push notifications, and nothing today
-- prevents sending the SAME alert to the same person twice.
--
-- `claim_updates_insight_scope` (migration 088) protects GENERATION — one instance
-- regenerates a scope's card — but delivery is a separate act. A re-trip of the same
-- scope later in the day, a Railway rolling deploy where two instances overlap, or a
-- retry after a partial failure would each re-notify everyone watching that ticker.
--
-- Getting this wrong is uniquely bad: a duplicate cache write is invisible, a
-- duplicate push is a buzz in someone's pocket. Users uninstall over it, and unlike a
-- bad row it cannot be corrected after the fact.
--
-- Design: a unique (user_id, dedup_key) claim, inserted BEFORE the send. The insert
-- itself is the lock — if it conflicts, someone already sent this and we skip. That
-- makes double-sending impossible even across instances, without any coordination.
--
-- `dedup_key` is composed by the caller and encodes what "the same alert" means —
-- today `move:{TICKER}:{ET trading date}`, i.e. at most one price-move alert per
-- ticker per user per day, no matter how many times the sweeper revisits it.
--
-- NOTE ON user_id: no foreign key, same as the other backend-internal tables here
-- (chat_usage_budget 096, guest_report_budget 106). Push only reaches signed-in users
-- (device tokens are auth-only), so in practice these ARE real user ids — but the
-- account-deletion purge clears the table explicitly rather than relying on a cascade,
-- consistent with everything else in _UNLINKED_USER_TABLES.

BEGIN;

CREATE TABLE IF NOT EXISTS push_send_log (
    user_id   UUID        NOT NULL,
    dedup_key TEXT        NOT NULL,
    sent_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, dedup_key)
);

COMMENT ON TABLE push_send_log IS
    'One row per push actually delivered. The PK is the dedup claim: insert BEFORE '
    'sending, and treat a conflict as "already sent". Prevents duplicate alerts '
    'across retries and across Railway instances. Swept on a retention window.';

-- The retention sweep deletes by sent_at; the PK already serves the point lookup.
CREATE INDEX IF NOT EXISTS idx_push_send_log_sent_at
    ON push_send_log (sent_at);


-- ── RLS + grants ─────────────────────────────────────────────────────────────
-- Backend-internal working table: no anon/authenticated access at all, same posture
-- as chat_usage_budget / guest_report_budget / analytics_events.

ALTER TABLE push_send_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "push_send_log_service_all" ON push_send_log;
CREATE POLICY "push_send_log_service_all" ON push_send_log
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- REVOKE explicitly rather than merely omitting the GRANT: Supabase projects commonly
-- carry `ALTER DEFAULT PRIVILEGES ... GRANT ALL ON TABLES TO anon, authenticated`, so
-- a new public-schema table can arrive already granted. RLS would still block the rows,
-- but that leaves a single layer (cf. migrations 078/079/094/107).
REVOKE ALL ON push_send_log FROM anon, authenticated;

GRANT ALL ON push_send_log TO service_role;

COMMIT;
