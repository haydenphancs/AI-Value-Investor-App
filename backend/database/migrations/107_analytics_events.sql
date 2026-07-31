-- 107_analytics_events.sql
--
-- Why: the app is about to launch with ZERO product analytics. Sentry reports crashes
-- and errors; nothing records what people actually do. On launch day we could not
-- answer: what % of installs generate a report, where the paywall gets seen and what
-- converts, which persona people pick, whether anyone finishes a Journey lesson, or
-- D1/D7 retention. Every product decision after launch would be a guess.
--
-- Why FIRST-PARTY instead of an SDK (PostHog/Amplitude/TelemetryDeck): the App Privacy
-- questionnaire and both privacy-policy surfaces were just finalised
-- (documents/legal/app-privacy-answers.md — 8 data types, tracking = No). A third-party
-- analytics SDK reopens all of that and adds a processor to disclose. Events into our
-- own Postgres disclose nothing new and reuse the RLS/migration patterns already here.
-- Trade-off accepted: no funnel UI out of the box — these are SQL-queryable only.
--
-- NOTE ON identity_key: like chat_usage_budget and guest_report_budget, NO foreign key
-- to public.users. The key is the same abuse/analytics bucket used elsewhere — a real
-- user id, or the per-INSTALL synthetic uuid from guest_user_id_for() — so a guest's
-- events are attributable to their install without a users row existing. That is also
-- what lets a guest → signup funnel be stitched later: the install id is stable across
-- the sign-up boundary.
--
-- PRIVACY: `props` is a bounded JSONB bag for low-cardinality dimensions (ticker,
-- persona, screen name, tier). It must never carry free text the user typed, message
-- bodies, emails, or tokens — the endpoint enforces an allowlist of event names and a
-- size cap, and the iOS layer only ever sends literals. Rows are swept after
-- RETENTION_DAYS; nothing here is needed long-term at row grain.

BEGIN;

CREATE TABLE IF NOT EXISTS analytics_events (
    id           BIGSERIAL PRIMARY KEY,
    identity_key UUID        NOT NULL,
    session_id   TEXT,
    event        TEXT        NOT NULL,
    props        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    app_version  TEXT,
    platform     TEXT,
    -- Client clock: useful for ordering within a session, NEVER trusted for
    -- bucketing (device clocks are wrong and user-settable). Aggregate on server_ts.
    client_ts    TIMESTAMPTZ,
    server_ts    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE analytics_events IS
    'First-party product analytics. identity_key = real user id OR per-install guest '
    'uuid (no FK, by design). props is low-cardinality dimensions only — never '
    'user-typed text. Swept on a retention window; not a system of record.';

-- "How many X happened over time" — the shape of almost every question asked here.
CREATE INDEX IF NOT EXISTS idx_analytics_events_event_time
    ON analytics_events (event, server_ts DESC);

-- Per-user/per-install funnels and retention cohorts.
CREATE INDEX IF NOT EXISTS idx_analytics_events_identity_time
    ON analytics_events (identity_key, server_ts DESC);

-- Retention sweep deletes by server_ts alone.
CREATE INDEX IF NOT EXISTS idx_analytics_events_server_ts
    ON analytics_events (server_ts);


-- ── RLS + grants ─────────────────────────────────────────────────────────────
-- Backend-internal: the ingest endpoint writes with the service role. anon and
-- authenticated get NOTHING — not even SELECT. Reading another user's behavioural
-- history is exactly the thing to keep off the client, and the app never reads these.

ALTER TABLE analytics_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "analytics_events_service_all" ON analytics_events;
CREATE POLICY "analytics_events_service_all" ON analytics_events
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- REVOKE explicitly, don't just omit the GRANT. Supabase projects commonly carry
-- `ALTER DEFAULT PRIVILEGES ... GRANT ALL ON TABLES TO anon, authenticated`, so a new
-- public-schema table can arrive already granted — absence of a GRANT is NOT absence
-- of privilege. RLS would still block the rows today, but that leaves a single layer,
-- and one future permissive policy removes it. Migrations 078/079 exist because of
-- exactly this; 094_earnings_cache.sql does the same for the same posture.
REVOKE ALL ON analytics_events FROM anon, authenticated;
REVOKE ALL ON SEQUENCE analytics_events_id_seq FROM anon, authenticated;

GRANT ALL ON analytics_events TO service_role;
GRANT USAGE, SELECT ON SEQUENCE analytics_events_id_seq TO service_role;

COMMIT;
