-- 134_investor_profile_answered_fields.sql
--
-- Why: ONE flag was doing TWO jobs, and for the two most likely answers they disagree.
--
-- `is_empty_profile()` means "is there anything to RENDER into the prompt?" — every array
-- empty AND every scalar still at its column default. The iOS Settings row reads the same
-- boolean as "did the reader TELL us anything?", which is a different question.
--
-- They diverge because `render_profile_block` deliberately skips an at-default scalar
-- (emitting it would restate the global STYLE block for ~40 tokens a turn and tell the
-- model nothing it does not already know). So a reader who genuinely tapped
-- "Still learning" + "A bit of both" — the middle option on BOTH onboarding questions, and
-- the most likely pair — stored `experience_level='learning', explanation_style='balanced'`,
-- which equals the defaults. Result: `is_empty: true`, `applied: false` even for a
-- consented Pro subscriber with the flag on, and a Settings row reading
-- "add some interests in Settings to give it something to use" to somebody who had just
-- answered both questions. The feature was permanently inert for them and the UI blamed
-- them for it.
--
-- Row existence cannot substitute for this: a row is created by any write, including a
-- consent-only PUT, so `has_profile` answers "has anything ever been written" rather than
-- "has the reader stated a preference".
--
-- This column records WHICH fields the reader actually submitted, so the three questions
-- can be answered separately:
--   has_profile         a row exists                        (unchanged)
--   is_empty            the reader has stated nothing       → answered_fields empty AND arrays empty
--   would_personalize   their answers would change output   → bool(render_profile_block(...))
--   applied             it is changing output right now     → the four-arm may_apply_profile gate
--
-- It is also what lets the new Settings editor show which chips are the reader's own
-- choice versus an untouched default.
--
-- ── ROLLOUT: apply this BEFORE deploying the code that writes it ───────────────
-- `upsert_profile` names `answered_fields` in the PostgREST payload whenever any
-- answerable field is submitted. PostgREST rejects a payload naming a column absent from
-- its schema cache (PGRST204), so on a code-first rollout the ENTIRE profile write would
-- have failed — losing the reader's actual answers over a bookkeeping column, which is
-- precisely the hazard 131's own ROLLOUT note warns about.
--
-- The service now degrades instead of failing: it catches the unknown-column error, drops
-- this key and retries, logging a warning that names this migration. So a code-first
-- deploy is SURVIVABLE rather than destructive — but it still silently loses chosen-default
-- tracking until the migration lands, so apply this first.
--
-- After applying in Studio, PostgREST reloads its schema cache from a DDL event trigger.
-- That is normally seconds; writes naming the new column 400 until it happens.
--
-- ── Same guest-writable posture as 131 ─────────────────────────────────────────
-- This ALTERs the existing table, so no new auth.md §1a obligations arise: the table
-- already has no FK, already has its `_UNLINKED_USER_TABLES` entry, already has a claim
-- step, and already REVOKEs from anon/authenticated. Nothing here changes any of that.
--
-- ── Backfill ───────────────────────────────────────────────────────────────────
-- Existing rows are backfilled from what they actually contain: a non-empty array, or a
-- scalar that DIFFERS from its default, is provably an explicit answer. A stored scalar
-- that equals its default is indistinguishable from "never asked" for rows written before
-- this column existed, so it is left out — the conservative direction, since the cost is
-- a reader being invited to confirm a preference rather than being told they stated one
-- they did not. At the time of writing the table is empty, so this is future-proofing
-- rather than a data fix.

BEGIN;

ALTER TABLE public.user_investor_profile
    ADD COLUMN IF NOT EXISTS answered_fields TEXT[] NOT NULL DEFAULT '{}';

-- Closed vocabulary, mirroring the six editable columns. `<@` (containment) rather than an
-- enum type, matching how `topics` / `learning_goals` / `follow_signals` are constrained on
-- this table: it permits the empty array, rejects any out-of-vocabulary member, and rejects
-- NULL elements. Kept in step with `user_investor_profile_service.SCALAR_FIELDS` +
-- `ARRAY_FIELDS` by `test_investor_profile_validation.py`, which scans this file.
ALTER TABLE public.user_investor_profile
    DROP CONSTRAINT IF EXISTS user_investor_profile_answered_fields_check;
ALTER TABLE public.user_investor_profile
    ADD CONSTRAINT user_investor_profile_answered_fields_check
    CHECK (answered_fields <@ ARRAY[
        'experience_level', 'explanation_style', 'answer_depth',
        'topics', 'learning_goals', 'follow_signals'
    ]::TEXT[]);

-- Idempotent in RESULT, not in work done. The guard is "answered_fields is empty", which
-- cannot distinguish "not yet backfilled" from "correctly backfilled to empty" — so every
-- all-default row is re-UPDATEd on each apply (new ctid, new xmin, dead tuples). Values
-- are stable, which is what matters for a re-apply. Corollary worth knowing: if the app
-- ever deliberately stores an empty `answered_fields` on a row whose value columns are
-- non-default, re-applying this migration would RESURRECT the inferred set.
UPDATE public.user_investor_profile
SET answered_fields = (
    SELECT COALESCE(ARRAY_AGG(f), '{}')
    FROM (
        SELECT 'experience_level' AS f WHERE experience_level <> 'learning'
        UNION ALL SELECT 'explanation_style' WHERE explanation_style <> 'balanced'
        UNION ALL SELECT 'answer_depth' WHERE answer_depth <> 'brief'
        UNION ALL SELECT 'topics' WHERE COALESCE(ARRAY_LENGTH(topics, 1), 0) > 0
        UNION ALL SELECT 'learning_goals' WHERE COALESCE(ARRAY_LENGTH(learning_goals, 1), 0) > 0
        UNION ALL SELECT 'follow_signals' WHERE COALESCE(ARRAY_LENGTH(follow_signals, 1), 0) > 0
    ) AS derived
)
WHERE COALESCE(ARRAY_LENGTH(answered_fields, 1), 0) = 0;

COMMENT ON COLUMN public.user_investor_profile.answered_fields IS
    'Which preference fields the reader has explicitly submitted. Distinguishes "chose the default value" from "never answered" — the two are identical in the value columns, which made a reader who picked both middle options read back as having stated nothing. Drives is_empty on the wire and the pre-selected chips in the Settings editor.';

COMMIT;
