-- 145_whale_activity_disclosure.sql
--
-- Why: a tracked filer can stop producing data at any time — a fund deregisters or drops
-- below the $100M 13F threshold, a politician retires or simply stops trading — and the
-- app has no way to detect or disclose it. A dormant whale therefore renders as a BROKEN
-- screen rather than as a quiet one.
--
-- Live example at the time of writing: Michael Burry's profile served a confident
-- portfolio value and annual return next to ZERO holdings and ZERO trades. Scion stopped
-- filing after 2025-Q3; nothing on screen said so. The only hint was a "Q3 2025" caption
-- inside a stat tile.
--
-- Two layers, and this migration is the storage for both:
--
--   DERIVED  — `last_filing_period` + `last_activity_date` let the API classify a filer
--              as current / late / dormant / quiet WITHOUT a live probe. That matters:
--              every FMP method swallows its exception and returns [], so "empty" is
--              indistinguishable from a 429, a plan downgrade or a timeout. Inferring
--              dormancy from a probe would mark real whales dormant during an outage.
--              What we already HOLD cannot be corrupted by an outage.
--
--   CURATED  — `lifecycle_status` + `lifecycle_note` carry facts the data cannot show.
--              "Nancy Pelosi retired" is invisible in her filings (she still shows recent
--              trades), so it can only ever be written by a human, in whale_registry.json.
--
-- ⚠️ 13F staleness is measured in MISSED QUARTERS, never in days. A 13F is due 45 days
-- after quarter end, so every healthy filer is ~51 days stale the moment a quarter closes.
-- The classification lives in app/services/_whale_common.compute_activity, which reuses
-- app/utils/period_labels.latest_filed_13f_quarter rather than re-deriving the lag.

-- ── 1. Columns ────────────────────────────────────────────────────────────────

ALTER TABLE public.whales
    ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT '';
ALTER TABLE public.whales
    ADD COLUMN IF NOT EXISTS lifecycle_note TEXT;
ALTER TABLE public.whales
    ADD COLUMN IF NOT EXISTS last_filing_period TEXT;
ALTER TABLE public.whales
    ADD COLUMN IF NOT EXISTS last_activity_date TEXT;

COMMENT ON COLUMN public.whales.lifecycle_status IS
    'CURATED, from whale_registry.json: '''' (= active, the default) or ''inactive''. '
    'Never inferred — a fund closing or a member retiring is not visible in filing data. '
    'When not active, lifecycle_note is required and explains why.';
COMMENT ON COLUMN public.whales.lifecycle_note IS
    'CURATED prose shown verbatim to the user, e.g. "Scion Asset Management deregistered '
    'as an investment adviser in 2025 and no longer files 13F." Overrides the derived '
    'label on the profile: a real reason beats an inferred one.';
COMMENT ON COLUMN public.whales.last_filing_period IS
    'DERIVED: newest 13F quarter held for this whale, as YYYY-Qn. NULL for congressional '
    'filers, whose snapshots key on a wall-clock month (YYYY-MM) and who never file a 13F. '
    'Compared against latest_filed_13f_quarter() to count MISSED QUARTERS.';
COMMENT ON COLUMN public.whales.last_activity_date IS
    'DERIVED: MAX(whale_trade_groups.date) — the newest 13F filing date or congressional '
    'disclosure date. The one activity field whose meaning is consistent across both '
    'sources. Deliberately NOT last_hydrated_at: the congressional path re-stamps that '
    'every month at the snapshot period rollover even when zero trades were disclosed.';

-- ── 2. Backfill — load-bearing, not a convenience ─────────────────────────────
--
-- A dormant whale is skipped by the raw_hash idempotency guard on EVERY run
-- (scripts/hydrate_whales.py returns before _persist), so hydration alone would never
-- populate these columns for exactly the whales that need them. Michael Burry would stay
-- NULL forever. Seeding here is what makes the feature work on day one.

UPDATE public.whales w
SET last_activity_date = sub.max_date
FROM (
    SELECT whale_id, MAX(date) AS max_date
    FROM public.whale_trade_groups
    GROUP BY whale_id
) sub
WHERE sub.whale_id = w.id
  AND w.last_activity_date IS DISTINCT FROM sub.max_date;

-- The regex is load-bearing: congressional snapshots write filing_period as 'YYYY-MM'.
-- Letting one through would render a politician as having filed a 13F quarter that does
-- not exist. Mirrors _FILING_PERIOD_RE in app/utils/period_labels.py.
UPDATE public.whales w
SET last_filing_period = sub.max_period
FROM (
    SELECT whale_id, MAX(filing_period) AS max_period
    FROM public.whale_filing_snapshots
    WHERE filing_period ~ '^[0-9]{4}-Q[1-4]$'
    GROUP BY whale_id
) sub
WHERE sub.whale_id = w.id
  AND w.last_filing_period IS DISTINCT FROM sub.max_period;

-- ── 3. Index ──────────────────────────────────────────────────────────────────
-- The operational check ("who has gone quiet?") sorts on last_activity_date, and the
-- roster select reads it for all 56 rows on every cache miss.

CREATE INDEX IF NOT EXISTS idx_whales_last_activity
    ON public.whales (last_activity_date NULLS FIRST);
