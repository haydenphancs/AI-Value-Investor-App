-- 215_marketing_replacement_runs.sql
--
-- Why: owner decision 2026-11-27. Reviewing a Money Map post in Telegram he wants "an option to replace to the other
-- companies (like the next one)", and chose "Full replacement: the next company's post (video + picture) comes to
-- Telegram for review about 20-75 min after the tap. Max 2 replacements a day. Money Map posts only. Needs migration
-- 215, off until you switch it on." (SYSTEM_DESIGN_GUIDELINES §12.25.)
--
-- A replacement is a NEW marketing run of the same (ET day, lane): its own claim, its own script (PK run_id), its own
-- assets and posts, so the media worker runs it like any day's run with no change. Since 210 a (day, lane) held exactly
-- ONE run (UNIQUE (run_date, lane)); this migration makes the claim (run_date, lane, replacement):
--
--   * marketing_runs.replacement: 0 for every run the engine has ever made and for every INSERT that does not name it
--     (the deployed web never does), 1 or 2 for a replacement. NOT NULL DEFAULT 0 + CHECK 0..2. Written ONCE, by
--     public.marketing_request_replacement (below); nothing updates it (run_service.update_run has no parameter for it).
--   * marketing_runs.replaces_run_id: the run this one replaces (NULL exactly when replacement = 0, CHECKed), an FK ON
--     DELETE RESTRICT like every other marketing_runs reference.
--   * UNIQUE (run_date, lane, replacement) replaces UNIQUE (run_date, lane) (marketing_runs_run_date_lane_key, 210). The
--     new key is ADDED FIRST; then a DO block drops every UNIQUE constraint on the table whose definition is exactly
--     `UNIQUE (run_date, lane)`, matched by DEFINITION, not name, exactly as 210 dropped the run_date-only key. The
--     claim detects a lost race by the 23505 CODE, never a constraint name, so the deployed web's claim behaves
--     exactly as before: its INSERT omits `replacement` (→ 0) and conflicts on (run_date, lane, 0).
--   * marketing_scripts.replacement: the same number beside the script, copied from its run by the ONE writer
--     (run_service.insert_script), so the readers that bound a window by "scripts per (day, lane)" (the digest's token
--     read) can tell a day's replacements apart from one table. Sent only for a replacement, so a gen-0 script INSERT
--     is byte for byte today's.
--   * public.marketing_request_replacement(run, by, not_after): the ONE writer of a replacement row. In ONE
--     transaction, under a per-ET-day advisory lock (both lanes), it checks the run is the newest of its (day, lane),
--     media_ready, an ACCEPTED money_map script, with no post approved / queued / published / failed / retracted (every
--     status from which a post can reach, or has reached, a platform) and at least one pending_review; counts the day's
--     replacements against the owner's cap (2 a day, both lanes); then INSERTs the replacement row (status 'planned',
--     attempts 0) that the worker's next tick claims, rejects every pending_review post of the run, and stamps the run
--     `metadata.replaced_by` (with the tap cutoff the caller passed, for the record). A refusal writes nothing and
--     returns its reason. Row locks on the posts serialise it with the review bot's Approve / Reject (their
--     UPDATEs are fenced on status = 'pending_review') and with the publisher's claim (approved → queued): whichever
--     commits first wins and the other sees the result.
--     IDEMPOTENT (a lost response, a retried tap): when the newest run of the (day, lane) is already THIS run's
--     replacement (replaces_run_id = the run, replacement = its own + 1) and the run was never restored, it returns
--     `requested` again with `replayed: true` and the SAME payload, read back from the ledger (the new run id, the
--     day's count recorded at the request, the posts it rejected and their decision message ids), plus that
--     replacement's status NOW (so the bot words a late retry by what became of it). It never writes twice. The
--     replay is checked BEFORE the cutoff: a request that committed just before it is still answered on a retry
--     after it.
--     The decision message ids it returns are `metadata.review_message_id` values whose JSON type is NUMBER, of posts
--     that were never a read-only preview (`review_preview_at` empty): a preview's id is its caption message, which
--     has no keyboard to remove.
--     The new row carries the replacement request (when, from which run, which record) but NEVER the owner's id: the
--     row is echoed to the media worker on its claim, the least-trusted process. `created_at` is the clock read AFTER
--     the lock, so replacement numbers and created_at rise together (the web reads a (day, lane)'s newest run by
--     created_at, then id).
--   * public.marketing_restore_replaced(run, by, now): "↩ Post {SYM} after all" on the notice that says nothing
--     replaces the original. Under the SAME per-ET-day lock, it puts back to pending_review EXACTLY the posts the
--     request rejected for that replacement (status rejected, metadata.review.replaced_by_run_id = it), with their
--     review record and review-offer stamps removed (so the next review sweep offers them as a fresh bundle) and an
--     audit record `metadata.restored`; the run gets `metadata.restored_from`. Only while the replaced run is still
--     media_ready, its `replaced_by` names the NEWEST run of its (day, lane), that replacement is over (skipped,
--     published or media_ready, or failed at its 2-attempt cap — a failed run below its cap may still be re-claimed
--     and post) and has NO post at all (so at most one PUBLISHING run per (day, lane), whatever a later change does),
--     and it is still the run's own ET day — until its END, not the lane's tap cutoff: a replacement asked for in the
--     last hour before that cutoff only starts after it (the next :15 tick), and one never started is closed after
--     it, so a cutoff-bound restore could never bring the original back in exactly those cases. A replay returns
--     `restored` with `replayed: true` and the same post ids, read back (answered before the day check); it never
--     writes twice. It is the ONLY way a rejected post returns to pending_review, and only for a post a replacement
--     request rejected.
--
-- Both functions are SECURITY INVOKER: their only caller is service_role, which holds the table grants and bypasses
-- RLS; REVOKEd from PUBLIC, anon and authenticated like 173's function.
--
-- No existing CHECK list changes: marketing_runs.status already allows 'planned'; marketing_scripts.reject_reason
-- already allows 'nothing_qualified' (210), which a replacement that finds no other company writes.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS; every named constraint is DROP CONSTRAINT IF EXISTS + ADD (a re-run re-creates
-- the same definitions); the DO block drops only what is still there; CREATE OR REPLACE FUNCTION; REVOKE/GRANT and
-- COMMENT are declarative. One transaction: a failure part-way leaves the tables as they were, never without a claim
-- key.
--
-- Never re-run 210 after this migration. Before any replacement row exists, 210 succeeds and re-ADDs UNIQUE (run_date,
-- lane); every later replacement INSERT then fails 23505 (the web logs "UNIQUE (run_date, lane) is back — was 210
-- re-run after 215?" at ERROR and the tap answers "Could not record that — tap again"; nothing else breaks). Once a
-- replacement row exists, 210's ADD CONSTRAINT fails on the duplicate (run_date, lane) and 210 rolls back whole
-- (harmless).
--
-- Safe on the live tables: both ADD COLUMNs carry a constant default (catalog-only, no rewrite); the CHECKs and the FK
-- validate against a few hundred rows; the new unique index builds in milliseconds. A run in flight feels only that
-- brief lock. Apply OUTSIDE 10:00-10:45 and 16:00-16:45 ET (the two lanes' run windows), out of caution.
--
-- Deploy order: apply BEFORE the web deploy that carries replacements. That web reads and filters the `replacement`
-- column (the ledger reads, the notice step) and calls the two functions; without this migration those fail 42703
-- (undefined_column) / PGRST202. Applying it first is safe for the web and worker deployed today: their INSERT omits
-- the column (→ 0), only gen-0 rows exist until the new web's function is called, and their unordered run read finds
-- the one row. The worker does not change.
--
-- Rollback: switch MARKETING_REPLACE_ENABLED off first. Never roll the WEB back below this release while a
-- replacement row is dated within the last 15 ET days (the 14-day spotlight cooldown read plus the run day; it also
-- covers the digest's 2-week window): the old web reads a (day, lane) unordered and could hand a replacement row to a
-- claim (its manifest then lands on the replaced run's storage path → 422 every attempt); its per-day bounds were
-- written for one run per (day, lane), so its spotlight cooldown read (last_checked_window) raises and every
-- insider-spotlight build fails, its cross-lane read refuses more than one script per (day, lane), its weekly digest
-- silently drops a week's newest runs and its cost line reads "unreadable". Never drop this migration's columns, key
-- or functions while a replacement-aware web is deployed.
--
-- Verify after applying (read-only):
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conrelid = 'public.marketing_runs'::regclass AND contype = 'u';
--       -- expect exactly ONE row: marketing_runs_run_date_lane_replacement_key | UNIQUE (run_date, lane, replacement)
--   SELECT table_name, column_name, data_type, column_default, is_nullable FROM information_schema.columns
--    WHERE table_schema = 'public' AND table_name IN ('marketing_runs', 'marketing_scripts')
--      AND column_name IN ('replacement', 'replaces_run_id') ORDER BY 1, 2;
--       -- expect three rows: marketing_runs.replacement smallint 0 NO; marketing_runs.replaces_run_id uuid (null) YES;
--       --                    marketing_scripts.replacement smallint 0 NO
--   SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
--    WHERE conname IN ('marketing_runs_replacement_valid', 'marketing_runs_replaces_run_id_valid',
--                      'marketing_runs_replaces_run_id_fkey', 'marketing_scripts_replacement_valid') ORDER BY 1;
--       -- expect four rows
--   SELECT replacement, count(*) FROM public.marketing_runs GROUP BY 1;
--       -- expect 0 only, until MARKETING_REPLACE_ENABLED is switched on and the owner taps
--   SELECT p.proname, p.prosecdef, pg_get_function_identity_arguments(p.oid) FROM pg_proc p
--    WHERE p.oid IN ('public.marketing_request_replacement(uuid, text, timestamptz)'::regprocedure,
--                    'public.marketing_restore_replaced(uuid, text, timestamptz)'::regprocedure) ORDER BY 1;
--       -- expect: marketing_request_replacement | f | p_run_id uuid, p_by text, p_not_after timestamp with time zone
--       --         marketing_restore_replaced    | f | p_run_id uuid, p_by text, p_now timestamp with time zone
--   SELECT has_function_privilege('anon', 'public.marketing_request_replacement(uuid, text, timestamptz)', 'EXECUTE'),
--          has_function_privilege('authenticated', 'public.marketing_request_replacement(uuid, text, timestamptz)', 'EXECUTE'),
--          has_function_privilege('service_role', 'public.marketing_request_replacement(uuid, text, timestamptz)', 'EXECUTE'),
--          has_function_privilege('anon', 'public.marketing_restore_replaced(uuid, text, timestamptz)', 'EXECUTE'),
--          has_function_privilege('authenticated', 'public.marketing_restore_replaced(uuid, text, timestamptz)', 'EXECUTE'),
--          has_function_privilege('service_role', 'public.marketing_restore_replaced(uuid, text, timestamptz)', 'EXECUTE');
--       -- expect: f | f | t | f | f | t
--
-- AFTER APPLYING: re-run scripts/dump_schema.sh and scripts/generate_schema_doc.py (no new table, so no
-- schema_curation.py entry), then backend/scripts/check_function_grants.py.

BEGIN;

-- (a) marketing_runs.replacement / replaces_run_id
ALTER TABLE public.marketing_runs ADD COLUMN IF NOT EXISTS replacement SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE public.marketing_runs ADD COLUMN IF NOT EXISTS replaces_run_id UUID;

ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS marketing_runs_replacement_valid;
ALTER TABLE public.marketing_runs
    ADD CONSTRAINT marketing_runs_replacement_valid CHECK (replacement BETWEEN 0 AND 2);

ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS marketing_runs_replaces_run_id_valid;
ALTER TABLE public.marketing_runs
    ADD CONSTRAINT marketing_runs_replaces_run_id_valid CHECK ((replacement = 0) = (replaces_run_id IS NULL));

ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS marketing_runs_replaces_run_id_fkey;
ALTER TABLE public.marketing_runs
    ADD CONSTRAINT marketing_runs_replaces_run_id_fkey
    FOREIGN KEY (replaces_run_id) REFERENCES public.marketing_runs(id) ON DELETE RESTRICT;

-- (b) the claim key: (run_date, lane, replacement) first, then the (run_date, lane) key goes
ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS marketing_runs_run_date_lane_replacement_key;
ALTER TABLE public.marketing_runs
    ADD CONSTRAINT marketing_runs_run_date_lane_replacement_key UNIQUE (run_date, lane, replacement);

DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT conname
          FROM pg_constraint
         WHERE conrelid = 'public.marketing_runs'::regclass
           AND contype = 'u'
           AND pg_get_constraintdef(oid) = 'UNIQUE (run_date, lane)'
    LOOP
        EXECUTE format('ALTER TABLE public.marketing_runs DROP CONSTRAINT %I', c.conname);
        RAISE NOTICE 'marketing_runs: dropped the (run_date, lane) claim key %', c.conname;
    END LOOP;
END
$$;

-- (c) marketing_scripts.replacement
ALTER TABLE public.marketing_scripts ADD COLUMN IF NOT EXISTS replacement SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE public.marketing_scripts DROP CONSTRAINT IF EXISTS marketing_scripts_replacement_valid;
ALTER TABLE public.marketing_scripts
    ADD CONSTRAINT marketing_scripts_replacement_valid CHECK (replacement BETWEEN 0 AND 2);

-- (d) comments
COMMENT ON COLUMN public.marketing_runs.replacement IS
    '0 for a day''s own run of its lane; 1 or 2 for a replacement the owner asked for from the review bot '
    '("🔄 Next company", Money Map only, at most 2 a day over both lanes), written once by '
    'marketing_request_replacement. The claim key is UNIQUE (run_date, lane, replacement) (migration 215).';
COMMENT ON COLUMN public.marketing_runs.replaces_run_id IS
    'The run this replacement replaces (its pending posts were rejected in the same transaction); NULL exactly when '
    'replacement = 0.';
COMMENT ON COLUMN public.marketing_scripts.replacement IS
    'The replacement number of the script''s run, copied by run_service.insert_script (the one writer).';
COMMENT ON TABLE public.marketing_runs IS
    'One row per (ET calendar day, lane, replacement) of the zero-touch marketing engine (SYSTEM_DESIGN_GUIDELINES '
    '§12). The media worker CLAIMS a day''s run by inserting this row (UNIQUE (run_date, lane, replacement), '
    'migration 215: the main lane every day, the spotlight lane on its own weekdays, and up to two owner-requested '
    'replacements a day) and checkpoints its last completed stage here so a killed or skipped Railway cron slot '
    'resumes on the next hourly tick instead of starting over or double-producing. Written through the token-gated '
    'internal API (app/api/v1/endpoints/marketing_internal.py), marketing_request_replacement and '
    'marketing_restore_replaced; the worker holds no Supabase key.';

-- (e) the replacement request
CREATE OR REPLACE FUNCTION public.marketing_request_replacement(
    p_run_id    UUID,
    p_by        TEXT,
    p_not_after TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    -- Pinned equal to app/schemas/marketing.py REPLACEMENTS_PER_DAY / REPLACEABLE_SERIES / RUN_REPLACEMENT_MAX.
    c_per_day   CONSTANT INTEGER := 2;
    c_series    CONSTANT TEXT    := 'money_map';
    c_max       CONSTANT INTEGER := 2;
    c_zone      CONSTANT TEXT    := 'America/New_York';
    v_now       TIMESTAMPTZ;
    v_day       DATE;
    v_run       public.marketing_runs%ROWTYPE;
    v_newest    public.marketing_runs%ROWTYPE;
    v_script    RECORD;
    v_blocking  TEXT;
    v_pending   INTEGER;
    v_today     INTEGER;
    v_next      SMALLINT;
    v_new_id    UUID;
    v_rejected  UUID[];
    v_messages  JSONB;
BEGIN
    IF p_run_id IS NULL OR p_by IS NULL OR length(p_by) NOT BETWEEN 1 AND 64 OR p_not_after IS NULL THEN
        RAISE EXCEPTION 'marketing_request_replacement: p_run_id, p_by (1-64 chars) and p_not_after are required'
            USING ERRCODE = '22023';  -- invalid_parameter_value
    END IF;

    SELECT run_date INTO v_day FROM public.marketing_runs WHERE id = p_run_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;

    -- One replacement decision per ET day at a time, over BOTH lanes: the day's cap is counted and spent under it.
    -- marketing_restore_replaced takes the SAME lock.
    PERFORM pg_advisory_xact_lock(hashtextextended('marketing_request_replacement:' || v_day::text, 0));
    v_now := clock_timestamp();   -- after the lock wait: every stamp below and the cutoff use the same instant

    SELECT * INTO v_run FROM public.marketing_runs WHERE id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;

    -- The (day, lane)'s newest run: a retried request finds its own replacement there and is answered again,
    -- read back from the ledger, never written twice. A run that was restored is never replayed. The replay is
    -- answered BEFORE the time check: a request that committed just before its cutoff and lost its response is still
    -- answered `requested` when the owner taps again after the cutoff (it changes nothing; it only reads back).
    SELECT * INTO v_newest FROM public.marketing_runs r
     WHERE r.run_date = v_run.run_date AND r.lane = v_run.lane
     ORDER BY r.replacement DESC
     LIMIT 1;
    IF v_newest.replaces_run_id = v_run.id AND v_newest.replacement = v_run.replacement + 1
       AND NOT (v_run.metadata ? 'restored_from') THEN
        RETURN jsonb_build_object(
            'outcome', 'requested', 'replayed', true,
            'run_id', v_newest.id,
            'replacement', v_newest.replacement,
            'status', v_newest.status,
            'replacements_today', CASE
                WHEN jsonb_typeof(v_run.metadata -> 'replaced_by' -> 'replacements_today') = 'number'
                THEN v_run.metadata -> 'replaced_by' -> 'replacements_today'
                ELSE to_jsonb((SELECT count(*) FROM public.marketing_runs r2
                                WHERE r2.run_date = v_run.run_date AND r2.replacement >= 1))
            END,
            'rejected_post_ids', COALESCE(
                (SELECT jsonb_agg(p.id ORDER BY p.id) FROM public.marketing_posts p
                  WHERE p.run_id = v_run.id
                    AND p.metadata -> 'review' ->> 'replaced_by_run_id' = v_newest.id::text),
                '[]'::jsonb),
            'review_message_ids', COALESCE(
                (SELECT jsonb_agg(DISTINCT p.metadata -> 'review_message_id') FROM public.marketing_posts p
                  WHERE p.run_id = v_run.id
                    AND p.metadata -> 'review' ->> 'replaced_by_run_id' = v_newest.id::text
                    AND jsonb_typeof(p.metadata -> 'review_message_id') = 'number'
                    AND NULLIF(p.metadata ->> 'review_preview_at', '') IS NULL),
                '[]'::jsonb),
            'from_source_ref', v_newest.metadata -> 'replacement_request' -> 'from_source_ref');
    END IF;

    IF v_now >= p_not_after OR (v_now AT TIME ZONE c_zone)::date <> v_run.run_date THEN
        RETURN jsonb_build_object('outcome', 'too_late');
    END IF;
    IF v_newest.replacement > v_run.replacement THEN
        RETURN jsonb_build_object('outcome', 'already_replaced', 'run_id', v_newest.id,
                                  'replacement', v_newest.replacement, 'status', v_newest.status);
    END IF;
    IF v_run.status <> 'media_ready' THEN
        RETURN jsonb_build_object('outcome', 'not_ready', 'status', v_run.status);
    END IF;

    SELECT s.status, s.template_id, s.source_ref INTO v_script
      FROM public.marketing_scripts s WHERE s.run_id = p_run_id;
    IF NOT FOUND THEN    -- separate IF: plpgsql does not promise to short-circuit OR, and v_script is unset here
        RETURN jsonb_build_object('outcome', 'not_money_map');
    END IF;
    IF v_script.status <> 'accepted' OR v_script.template_id IS DISTINCT FROM c_series THEN
        RETURN jsonb_build_object('outcome', 'not_money_map');
    END IF;

    -- Every post of the run, locked in ONE order (id) for every caller of either function.
    PERFORM 1 FROM public.marketing_posts p WHERE p.run_id = p_run_id ORDER BY p.id FOR UPDATE;
    -- The blocking set: every status from which a post can reach, or has reached, a platform. Pinned equal to
    -- app/schemas/marketing.py REPLACEMENT_BLOCKING_POST_STATUSES.
    SELECT string_agg(DISTINCT p.status, ',' ORDER BY p.status) INTO v_blocking
      FROM public.marketing_posts p
     WHERE p.run_id = p_run_id AND p.status IN ('approved', 'queued', 'published', 'failed', 'retracted');
    IF v_blocking IS NOT NULL THEN
        RETURN jsonb_build_object('outcome', 'member_decided', 'statuses', v_blocking);
    END IF;
    SELECT count(*) INTO v_pending FROM public.marketing_posts p
     WHERE p.run_id = p_run_id AND p.status = 'pending_review';
    IF v_pending = 0 THEN
        RETURN jsonb_build_object('outcome', 'nothing_pending');
    END IF;

    SELECT count(*) INTO v_today FROM public.marketing_runs r
     WHERE r.run_date = v_run.run_date AND r.replacement >= 1;
    IF v_today >= c_per_day OR v_run.replacement >= c_max THEN
        RETURN jsonb_build_object('outcome', 'cap_reached', 'today', v_today);
    END IF;
    v_next := v_run.replacement + 1;

    -- No `p_by` on the new row: it is echoed to the media worker on its claim.
    INSERT INTO public.marketing_runs
        (run_date, lane, replacement, replaces_run_id, status, stage, content_class, dry_run, attempts,
         metadata, created_at, updated_at)
    VALUES
        (v_run.run_date, v_run.lane, v_next, v_run.id, 'planned', 'planned', v_run.content_class, v_run.dry_run, 0,
         jsonb_build_object('replacement_request', jsonb_build_object(
             'at', v_now, 'from_run_id', v_run.id, 'from_source_ref', v_script.source_ref)),
         v_now, v_now)
    RETURNING id INTO v_new_id;

    WITH rejected AS (
        UPDATE public.marketing_posts p
           SET status = 'rejected',
               updated_at = v_now,
               metadata = p.metadata || jsonb_build_object('review', jsonb_build_object(
                   'decision', 'rejected', 'by', p_by, 'at', v_now,
                   'replaced_by_run_id', v_new_id, 'replacement', v_next))
         WHERE p.run_id = p_run_id AND p.status = 'pending_review'
        RETURNING p.id,
                  p.metadata -> 'review_message_id' AS mid,
                  p.metadata ->> 'review_preview_at' AS preview
    )
    SELECT array_agg(id ORDER BY id),
           COALESCE(jsonb_agg(DISTINCT mid) FILTER (
               WHERE jsonb_typeof(mid) = 'number' AND NULLIF(preview, '') IS NULL), '[]'::jsonb)
      INTO v_rejected, v_messages
      FROM rejected;

    UPDATE public.marketing_runs
       SET metadata = metadata || jsonb_build_object('replaced_by', jsonb_build_object(
               'run_id', v_new_id, 'replacement', v_next, 'at', v_now, 'by', p_by,
               'rejected', coalesce(cardinality(v_rejected), 0),
               'replacements_today', v_today + 1, 'not_after', p_not_after)),
           updated_at = v_now
     WHERE id = p_run_id;

    RETURN jsonb_build_object(
        'outcome', 'requested', 'replayed', false,
        'run_id', v_new_id,
        'replacement', v_next,
        'replacements_today', v_today + 1,
        'rejected_post_ids', COALESCE(to_jsonb(v_rejected), '[]'::jsonb),
        'review_message_ids', v_messages,
        'from_source_ref', v_script.source_ref);
END;
$$;

COMMENT ON FUNCTION public.marketing_request_replacement(UUID, TEXT, TIMESTAMPTZ) IS
    'The review bot''s "🔄 Next company" (owner decision 2026-11-27, Money Map only, 2 a day over both lanes): in one '
    'transaction, reject every pending_review post of a media_ready money_map run and insert its planned '
    'replacement run, or return why not ({"outcome": not_found | too_late | already_replaced | not_ready | '
    'not_money_map | member_decided | nothing_pending | cap_reached | requested}). A retry of a request that '
    'already landed answers requested with replayed = true, read back, never a second write. INVOKER; service_role '
    'only (migration 215).';

-- (f) the restore: "↩ Post {SYM} after all" when nothing replaced the original
CREATE OR REPLACE FUNCTION public.marketing_restore_replaced(
    p_run_id UUID,
    p_by     TEXT,
    p_now    TIMESTAMPTZ
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    -- Pinned equal to app/schemas/marketing.py REPLACEMENT_MAX_ATTEMPTS / RESTORE_FINAL_RUN_STATUSES and to
    -- app/services/marketing/run_service.py _REVIEW_OFFER_STAMPS (the stamps that mark a post as offered).
    c_max_attempts CONSTANT INTEGER := 2;
    c_offer_stamps CONSTANT TEXT[]  := ARRAY['review_bundle', 'review_notified_at', 'review_message_id',
                                             'review_message_ids'];
    c_zone         CONSTANT TEXT    := 'America/New_York';
    c_uuid         CONSTANT TEXT    := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
    v_now          TIMESTAMPTZ;
    v_judge        TIMESTAMPTZ;
    v_day          DATE;
    v_run          public.marketing_runs%ROWTYPE;
    v_rep          public.marketing_runs%ROWTYPE;
    v_rep_text     TEXT;
    v_rep_id       UUID;
    v_newest       UUID;
    v_restored     UUID[];
BEGIN
    IF p_run_id IS NULL OR p_by IS NULL OR length(p_by) NOT BETWEEN 1 AND 64 OR p_now IS NULL THEN
        RAISE EXCEPTION 'marketing_restore_replaced: p_run_id, p_by (1-64 chars) and p_now are required'
            USING ERRCODE = '22023';  -- invalid_parameter_value
    END IF;

    SELECT run_date INTO v_day FROM public.marketing_runs WHERE id = p_run_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;

    -- The SAME per-ET-day lock as marketing_request_replacement: a restore and a request never interleave.
    PERFORM pg_advisory_xact_lock(hashtextextended('marketing_request_replacement:' || v_day::text, 0));
    v_now := clock_timestamp();

    SELECT * INTO v_run FROM public.marketing_runs WHERE id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;

    -- (1) the replaced run is still media_ready
    IF v_run.status <> 'media_ready' THEN
        RETURN jsonb_build_object('outcome', 'not_restorable', 'status', v_run.status);
    END IF;

    -- (2) its replaced_by names the NEWEST run of its (day, lane), which replaces it, and that run is over
    v_rep_text := v_run.metadata -> 'replaced_by' ->> 'run_id';
    IF COALESCE(v_rep_text ~ c_uuid, false) = false THEN
        RETURN jsonb_build_object('outcome', 'not_restorable');
    END IF;
    v_rep_id := v_rep_text::uuid;
    SELECT r.id INTO v_newest FROM public.marketing_runs r
     WHERE r.run_date = v_run.run_date AND r.lane = v_run.lane
     ORDER BY r.replacement DESC
     LIMIT 1;
    IF v_newest IS DISTINCT FROM v_rep_id THEN
        RETURN jsonb_build_object('outcome', 'not_restorable');
    END IF;
    SELECT * INTO v_rep FROM public.marketing_runs WHERE id = v_rep_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_restorable');
    END IF;
    IF v_rep.replaces_run_id IS DISTINCT FROM v_run.id THEN
        RETURN jsonb_build_object('outcome', 'not_restorable');
    END IF;
    -- Over = it can never post: skipped / published / media_ready (no claim takes those again), or failed at its
    -- 2-attempt cap. A failed run below its cap may still be re-claimed and make posts, so it is "running".
    IF NOT (v_rep.status IN ('skipped', 'published', 'media_ready')
            OR (v_rep.status = 'failed' AND v_rep.attempts >= c_max_attempts)) THEN
        RETURN jsonb_build_object('outcome', 'replacement_running', 'status', v_rep.status,
                                  'attempts', v_rep.attempts);
    END IF;

    -- (3) the replacement made NO post at all, of any status: at most one PUBLISHING run per (day, lane)
    IF EXISTS (SELECT 1 FROM public.marketing_posts p WHERE p.run_id = v_rep.id) THEN
        RETURN jsonb_build_object('outcome', 'replacement_has_posts');
    END IF;

    -- (5) already restored: answer again, read back, never a second write. Answered BEFORE the day check (4): a
    -- restore that committed just before midnight and lost its response is still answered `restored` on a retry.
    IF v_run.metadata ? 'restored_from' THEN
        RETURN jsonb_build_object(
            'outcome', 'restored', 'replayed', true,
            'post_ids', COALESCE(
                (SELECT jsonb_agg(p.id ORDER BY p.id) FROM public.marketing_posts p
                  WHERE p.run_id = v_run.id
                    AND p.metadata -> 'restored' ->> 'by_run_id' = v_rep.id::text),
                '[]'::jsonb));
    END IF;

    -- (4) the run's own ET day (the later of the two clocks judges). Its END, not the lane's tap cutoff: a replacement
    -- asked for late (its first start tick, 22:15 ET main / 15:15 ET spotlight, is already past the 22:10 / 15:10 tap
    -- cutoff) or one that was never started ends after that cutoff, and the original must still come back. The
    -- original's posts stay fresh for the publisher through the next ET day (run_service.is_fresh).
    v_judge := GREATEST(v_now, p_now);
    IF (v_judge AT TIME ZONE c_zone)::date <> v_run.run_date THEN
        RETURN jsonb_build_object('outcome', 'too_late');
    END IF;

    -- Every post of the run, locked in ONE order (id), as marketing_request_replacement locks them.
    PERFORM 1 FROM public.marketing_posts p WHERE p.run_id = v_run.id ORDER BY p.id FOR UPDATE;

    WITH restored AS (
        UPDATE public.marketing_posts p
           SET status = 'pending_review',
               updated_at = v_now,
               metadata = (p.metadata - 'review' - c_offer_stamps)
                          || jsonb_build_object('restored', jsonb_build_object(
                                 'by_run_id', v_rep.id, 'at', v_now, 'by', p_by))
         WHERE p.run_id = v_run.id
           AND p.status = 'rejected'
           AND p.metadata -> 'review' ->> 'replaced_by_run_id' = v_rep.id::text
        RETURNING p.id
    )
    SELECT array_agg(id ORDER BY id) INTO v_restored FROM restored;

    UPDATE public.marketing_runs
       SET metadata = metadata || jsonb_build_object('restored_from', jsonb_build_object(
               'run_id', v_rep.id, 'at', v_now)),
           updated_at = v_now
     WHERE id = v_run.id;

    RETURN jsonb_build_object(
        'outcome', 'restored', 'replayed', false,
        'post_ids', COALESCE(to_jsonb(v_restored), '[]'::jsonb));
END;
$$;

COMMENT ON FUNCTION public.marketing_restore_replaced(UUID, TEXT, TIMESTAMPTZ) IS
    'The review bot''s "↩ Post {SYM} after all" (owner decision 2026-11-27): when the replacement of a media_ready '
    'run ended with NO post (skipped, published, media_ready, or failed at its 2-attempt cap), put back to '
    'pending_review exactly the posts its request rejected, until the end of the run''s own ET day, or '
    'return why not ({"outcome": not_found | not_restorable | replacement_running | replacement_has_posts | '
    'too_late | restored}). A replay answers restored with replayed = true, read back. INVOKER; service_role only '
    '(migration 215).';

-- (g) not on the public RPC surface. REVOKE before GRANT; both idempotent.
REVOKE ALL ON FUNCTION public.marketing_request_replacement(UUID, TEXT, TIMESTAMPTZ)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.marketing_request_replacement(UUID, TEXT, TIMESTAMPTZ)
    TO service_role;

REVOKE ALL ON FUNCTION public.marketing_restore_replaced(UUID, TEXT, TIMESTAMPTZ)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.marketing_restore_replaced(UUID, TEXT, TIMESTAMPTZ)
    TO service_role;

COMMIT;
