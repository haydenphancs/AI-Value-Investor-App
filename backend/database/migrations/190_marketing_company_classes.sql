-- 190_marketing_company_classes.sql
--
-- Why: Drop 2 of "Company Weekly" (owner decisions 2026-10-09, .claude/rules/marketing.md §1,
-- SYSTEM_DESIGN_GUIDELINES §12.12) adds code-templated company-news posts beside the weekly class-A
-- lesson, in two template classes: C (reportorial filings — Form 4 CEO buys and insider buys, 13F
-- filer moves, congressional counts: the series ceo_buys, insider_buys, thirteen_f, congress_count)
-- and the NEW class F (company fundamentals — company stakes, earnings vs estimates, how a company
-- makes money, theme explainers: company_stakes, earnings, money_map, theme_explainer). Migration
-- 170 CHECKed marketing_runs.content_class to ('A','C') under the generated name
-- marketing_runs_content_class_check (schema_snapshot.sql), so the 'F' mirror the web side writes
-- would fail 23514. This widens it.
--
-- What is authoritative: a day's class is a pure function of marketing_scripts.template_id
-- (app/services/marketing/selection.py content_class_of), frozen by the first-write-wins INSERT and
-- echoed in output.content_class. marketing_runs.content_class stays a best-effort, informational
-- MIRROR (script_service._heal_mirror); create_posts never gates on it.
--
-- It also rewrites two table comments the new §1 made stale (marketing_assets said nothing
-- FMP-licensed may enter the bucket; marketing_scripts said every output is the class-A writer's)
-- and documents the column.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS + ADD re-creates the same named CHECK on every run; COMMENT is
-- re-runnable. Safe on the live table: every existing row holds 'A', so the ADD validates at once (a
-- brief ACCESS EXCLUSIVE lock on one tiny table). No grant, policy, index or data change, and the
-- marketing_scripts CHECKs (status, reject_reason) are unchanged: Drop 2 adds no reject reason.
--
-- Deploy order: apply BEFORE the drop-2 web deploy. Without it the web still works (create_posts
-- reads the class from the script; the mirror write is best-effort and logged), but every kick of a
-- class-F day logs a failed mirror write. Never narrow the CHECK back while an 'F' row exists.
--
-- Verify after applying:
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'marketing_runs_content_class_check';
--       -- expect CHECK ((content_class = ANY (ARRAY['A'::text, 'C'::text, 'F'::text])))
--   SELECT obj_description('public.marketing_assets'::regclass);   -- the new wording

BEGIN;

ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS marketing_runs_content_class_check;
ALTER TABLE public.marketing_runs
    ADD CONSTRAINT marketing_runs_content_class_check CHECK (content_class IN ('A', 'C', 'F'));

COMMENT ON COLUMN public.marketing_runs.content_class IS
    'Informational mirror of the day''s content class, written best-effort by script_service._heal_mirror: '
    'A = educational lesson (writer + semantic judge), C = reportorial-filings template, F = company-'
    'fundamentals template. The authoritative class derives from marketing_scripts.template_id; '
    'create_posts never gates on this column.';

COMMENT ON TABLE public.marketing_assets IS
    'Every artefact the marketing engine renders for a run (narration, podcast MP3, MP4, the video cards '
    'and the 4:5 post image), keyed by its content-addressed path in the PUBLIC marketing-media bucket. '
    'Two-phase upload: pending_upload while the worker holds a signed upload URL, ready once the API has '
    'verified the object (size and content type). Since 2026-10-09 (rules/marketing.md §1) a rendered '
    'asset may carry FMP-derived company facts — statement figures, earnings against estimates, filings '
    'as filed, company logos used only to identify the company — but never a market price, a % price '
    'move, a price chart, ETF data or an FMP credit, never a person''s photo, and congressional data '
    'only as unnamed counts of at least two members. Company logos are stored by the web side under '
    'logos/<sha256[:32]>.<png|jpg> in the same bucket, referenced from marketing_scripts.output; they '
    'have no row here.';

COMMENT ON TABLE public.marketing_scripts IS
    'One row per marketing run: the day''s frozen selection (run_date, source_ref, template_id — whose '
    'registry entry decides the content class) and either the class-A writer''s output with the fact '
    'sheet it was grounded on, or a class-C/F template''s composed output with the as-filed record it '
    'was composed from (inserted accepted by the selecting INSERT: no writer, no lease, tokens_used 0). '
    'Written ONLY by the web side (kick-and-poll through the internal API; the worker never writes it). '
    'Output lives here and never in the public marketing-media bucket or in marketing_runs.metadata, '
    'whose key-by-key merge is not atomic and is echoed to the worker on every response. A writer '
    'generation holds the row through lease_until plus a fresh generation_id taken by one conditional '
    'UPDATE, and every terminal write is fenced on that generation_id. accepted is terminal and its '
    'output immutable: the day''s posts are built from it. Two writer caps: 4 content rejections '
    '(reject_reason content) and 4 generations ending without a verdict (writer_unavailable); a cap '
    'reached under an expired lease is closed by the next kick.';

COMMIT;
