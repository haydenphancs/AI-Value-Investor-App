-- check_phase_a_data.sql
--
-- Ad-hoc diagnostic: has Phase A (Census/FRED) populated `industry_dossier`
-- yet? Paste blocks one-at-a-time into Supabase Studio SQL editor.
--
-- Phase A populates ~100-150 rows covering every FMP industry, with
-- source_grain in {'industry','sector','all_industry'} and source_label
-- mentioning Census / BEA / FRED. Phase B (the AI override) overwrites
-- 9 curated industries with research-based source_labels.


-- 1. Total rows + most recent recompute timestamp.
SELECT
    COUNT(*)                          AS total_rows,
    MIN(computed_at)                  AS oldest_row,
    MAX(computed_at)                  AS newest_row,
    COUNT(*) FILTER (WHERE expires_at > NOW())  AS still_fresh
FROM public.industry_dossier;


-- 2. Coverage by source_grain — Phase A's 4-tier fallback chain.
--    Healthy table has the majority at 'industry' grain.
SELECT
    source_grain,
    COUNT(*) AS n
FROM public.industry_dossier
GROUP BY source_grain
ORDER BY n DESC;


-- 3. Have the 9 curated override industries been populated?
--    If a row is missing, Phase A hasn't reached it yet.
--    `is_override` true ⇒ Phase B has overwritten the Phase A row.
SELECT
    expected.industry,
    expected.sector,
    d.current_tam_b,
    d.cagr_5y_pct,
    d.lifecycle_phase,
    d.concentration_label,
    d.source_grain,
    d.source_label,
    -- Phase B writes labels derived from grounded publishers (no
    -- "Census", "BEA", or "FRED" wording). Use that to flag override rows.
    (d.source_label IS NOT NULL
        AND d.source_label NOT ILIKE '%Census%'
        AND d.source_label NOT ILIKE '%BEA%'
        AND d.source_label NOT ILIKE '%FRED%'
        AND d.source_label NOT ILIKE '%USNGSP%'
    ) AS is_override,
    d.computed_at
FROM (VALUES
    ('Semiconductors',                          'Technology'),
    ('Biotechnology',                           'Healthcare'),
    ('Drug Manufacturers - General',            'Healthcare'),
    ('Drug Manufacturers - Specialty & Generic','Healthcare'),
    ('Medical - Devices',                       'Healthcare'),
    ('Medical - Instruments & Supplies',        'Healthcare'),
    ('Auto - Manufacturers',                    'Consumer Cyclical'),
    ('Aerospace & Defense',                     'Industrials'),
    ('Internet Content & Information',          'Communication Services')
) AS expected(industry, sector)
LEFT JOIN public.industry_dossier d USING (industry)
ORDER BY expected.industry;


-- 4. First 20 rows ordered by industry (sanity-check the data shape).
SELECT
    industry,
    sector,
    current_tam_b,
    future_tam_b,
    cagr_5y_pct,
    lifecycle_phase,
    concentration_label,
    source_grain,
    LEFT(source_label, 60) AS source_label_preview,
    computed_at
FROM public.industry_dossier
ORDER BY industry
LIMIT 20;


-- 5. Latest Phase B (override) audit run summary, if any.
SELECT
    run_id,
    MAX(computed_at)  AS run_at,
    COUNT(*)          AS industries_attempted,
    COUNT(*) FILTER (WHERE status = 'applied')              AS applied,
    COUNT(*) FILTER (WHERE status = 'rejected_validation')  AS rejected_validation,
    COUNT(*) FILTER (WHERE status = 'gemini_error')         AS gemini_error,
    COUNT(*) FILTER (WHERE status = 'skipped_kill_switch')  AS skipped
FROM public.industry_override_audit
GROUP BY run_id
ORDER BY run_at DESC
LIMIT 5;
