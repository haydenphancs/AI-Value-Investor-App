-- manual_override_internet_content.sql
--
-- One-off manual override for `Internet Content & Information` in
-- `industry_dossier`. Both Phase A (USNGSP all-industry fallback,
-- $1,695B) and Phase B (Gemini, $184B) produced wrong values for this
-- industry. The user has chosen specific numbers and wants the row
-- pinned to them.
--
-- ⚠ This is NOT persistent — the next Phase A recompute (quarterly
--   cron OR `POST /api/v1/admin/refresh-industry-dossier`) will
--   overwrite the row from Census/FRED again. After every Phase A
--   run, re-apply this UPDATE if the values are still desired.
--
-- Run in three steps. Paste each block separately so you can verify
-- before and after.


-- ─── 1. Snapshot the current row (before overriding) ────────────────
SELECT
    industry, sector,
    current_tam_b, future_tam_b, current_year, future_year,
    cagr_5y_pct, lifecycle_phase, concentration_label,
    source_grain, LEFT(source_label, 60) AS source_label_preview,
    computed_at
FROM public.industry_dossier
WHERE industry = 'Internet Content & Information';


-- ─── 2. Apply the manual override ──────────────────────────────────
--
-- TAM:                $1,695.0B (current, 2025) → $2,311.4B (5y, 2030)
--                     future_tam derived from CAGR:
--                       1695 * (1.064 ^ 5) ≈ 2311.4
-- CAGR:               6.4%
-- Lifecycle:          'mature' (CAGR 6.4% → falls between 0% and 15%)
-- Concentration:      untouched (kept whatever Phase A computed from
--                     FMP constituents). Uncomment the line in the SET
--                     clause if you also want to override it.
-- source_label:       'Manual override (operator)' — distinguishes
--                     this row from Census/FRED + Gemini in the audit
--                     log and the iOS source caption.
-- expires_at:         NOW() + 8 days — same TTL as Phase A.

UPDATE public.industry_dossier
SET
    current_tam_b      = 1695.00,
    future_tam_b       = 2311.40,
    current_year       = '2025',
    future_year        = '2030',
    cagr_5y_pct        = 6.4000,
    lifecycle_phase    = 'mature',
    -- concentration_label = 'oligopoly',   -- uncomment to override too
    source_grain       = 'industry',
    source_label       = 'Manual override (operator)',
    computed_at        = NOW(),
    expires_at         = NOW() + INTERVAL '8 days'
WHERE industry = 'Internet Content & Information';


-- ─── 3. Verify the override took ───────────────────────────────────
SELECT
    industry,
    current_tam_b, future_tam_b, current_year, future_year,
    cagr_5y_pct, lifecycle_phase, concentration_label,
    source_grain, LEFT(source_label, 60) AS source_label_preview,
    computed_at, expires_at
FROM public.industry_dossier
WHERE industry = 'Internet Content & Information';
