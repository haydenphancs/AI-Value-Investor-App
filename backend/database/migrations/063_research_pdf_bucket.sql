-- 063_research_pdf_bucket.sql
--
-- Why: The "View Detailed Analysis" feature renders a finished research report
-- (research_reports.ticker_report_data) into a multi-page PDF and stores it for
-- download. These PDFs are per-user, point-in-time analyses, so the bucket is
-- PRIVATE — unlike the public 'journey-media' bucket (#061). Serving goes
-- through an authed backend proxy (GET /research/reports/{id}/pdf) that uses the
-- service role and re-checks user_id ownership per request, so no anon /
-- authenticated read policy is needed (a leaked object URL must not be enough).
--
-- Bucket layout (by convention):
--   research-pdfs/reports/<user_id>/<report_id>.pdf

-- 1. Private bucket (idempotent)
INSERT INTO storage.buckets (id, name, public)
VALUES ('research-pdfs', 'research-pdfs', false)
ON CONFLICT (id) DO UPDATE SET public = EXCLUDED.public;

-- 2. Service role manages all reads/uploads/updates/deletes for this bucket.
--    No anon/authenticated policy — the backend proxy is the only read path.
DROP POLICY IF EXISTS "research_pdfs_service_all" ON storage.objects;
CREATE POLICY "research_pdfs_service_all" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'research-pdfs')
    WITH CHECK (bucket_id = 'research-pdfs');
