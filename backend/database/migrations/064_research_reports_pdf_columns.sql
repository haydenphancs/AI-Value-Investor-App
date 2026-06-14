-- 064_research_reports_pdf_columns.sql
--
-- Why: Track the detailed-analysis PDF lifecycle per report. After a report
-- completes, the background task eagerly renders the PDF (WeasyPrint) and
-- uploads it to the private 'research-pdfs' bucket (#063), then records the
-- object path + status here. iOS reads pdf_status to know when "View Detailed
-- Analysis" is ready; the proxy endpoint serves the bytes from pdf_path.
--
--   pdf_status: 'pending' (generating) | 'ready' | 'failed'
--   pdf_path:   storage object key, e.g. reports/<user_id>/<report_id>.pdf
--
-- Existing completed reports default to 'pending' with no file; they are
-- backfilled on demand via POST /research/reports/{id}/pdf/regenerate.
-- research_reports already has RLS, so no policy changes are needed.

ALTER TABLE research_reports
    ADD COLUMN IF NOT EXISTS pdf_path TEXT,
    ADD COLUMN IF NOT EXISTS pdf_status TEXT DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS pdf_generated_at TIMESTAMPTZ;
