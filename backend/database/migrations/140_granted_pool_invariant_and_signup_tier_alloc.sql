-- 140_granted_pool_invariant_and_signup_tier_alloc.sql
--
-- Three loose ends left by 117 and 139, all on the granted credit pool.
--
--   A. THE GRANTED POOL HAS NO `used <= total` TRIPWIRE. The purchased pool got one in 117
--      (`user_credits_purchased_used_le_total`, "the tripwire that protects `spendable` …
--      a violation means a new writer got the arithmetic wrong"). The granted pool never
--      did, and `spend_credits` codes defensively around the gap — 118 says so in as many
--      words: "GREATEST(0, total - used) rather than (total - used): `used > total` is not
--      forbidden by any CHECK, and a negative granted remainder would otherwise make LEAST()
--      subtract from `used` — a silent credit mint." That clamp is currently load-bearing.
--      With this constraint it becomes belt-and-braces, and a future writer that gets the
--      arithmetic wrong fails loudly at the row instead of quietly minting credits.
--
--   B. A ROW CREATED AFTER 139 GETS `tier_alloc = 0`. 139 added the column and backfilled it
--      from `total`, but only for rows that existed at apply time. `create_user_credits()`
--      (135) and `handle_new_auth_user()` still use the pre-139 column list, so every account
--      created since carries `tier_alloc = 0` next to a non-zero `total`. That makes the
--      column's own COMMENT false ("the allocation actually granted this period") and leaves
--      `grant_tier_upgrade`'s replay guard reading a value that never described a grant.
--
--   C. `create_user_credits` WRITES AN UNSPLIT LEDGER ROW. It logs the opening grant as
--      `delta = v_alloc` with `granted_delta`/`purchased_delta` defaulting to 0, so
--      `delta = granted_delta + purchased_delta` — the invariant 139 restored across
--      `ensure_credit_period`, `grant_tier_upgrade` and `revoke_tier_credits` — is false for
--      the very first row of every account. 139 simply missed this one; it lives in a trigger
--      rather than beside the other four RPCs. Not directly farmable (a `grant` row has a
--      POSITIVE delta, and `refund_credits` only pairs against debits), but it is
--      indistinguishable from a pre-117 unknown-split row, which is the shape that fed the
--      minting fallback 139 had to narrow.
--
-- Safety: the live table was probed before writing this — 4 rows, zero violations of either
-- `used <= total` or the purchased equivalent — so VALIDATE passes without a repair pass.
--
-- The NOT VALID → VALIDATE two-step follows 047's shape on this same table, but be clear about
-- what it does and does not buy HERE: inside one transaction, nothing. `ADD CONSTRAINT ... NOT
-- VALID` takes ACCESS EXCLUSIVE and holds it until COMMIT, so VALIDATE's weaker SHARE UPDATE
-- EXCLUSIVE never comes into play and writers are blocked for the whole scan exactly as a plain
-- `ADD CONSTRAINT` would block them. Splitting the lock is only real across SEPARATE
-- transactions. A drifted database fails identically either way — error, full rollback, no
-- constraint stranded as NOT VALID. It is kept for symmetry with 047 and because atomicity is
-- worth more than lock duration on a 4-row table, NOT because it makes this safer.
--
-- Idempotent: `ADD CONSTRAINT` has no `IF NOT EXISTS`, so the constraint is guarded on
-- `pg_constraint` exactly as 047 does. Both functions are `CREATE OR REPLACE`.

BEGIN;

-- ── 1. The granted pool's `used <= total` tripwire ────────────────────────────────────────
--
-- `<=`, NOT `<`. `revoke_tier_credits` deliberately lands on equality: with
-- `v_floor = GREATEST(v_free, used)` and `v_writeoff = GREATEST(used - v_free, 0)`, a user
-- whose spend exceeds the free allocation ends at `total = used = v_free`. A strict `<`
-- would reject every Apple subscription refund.
--
-- Every current writer preserves it, and each moves both columns in a SINGLE statement, so
-- no intermediate row is ever checked:
--   ensure_credit_period    total = alloc, used = 0                        (0 <= alloc)
--   grant_tier_upgrade      total = GREATEST(alloc, total), used untouched (only widens)
--   spend_credits           used += LEAST(amount, GREATEST(0, total-used)) (capped at total)
--   refund_credits          used -= back_granted                           (only lowers used)
--   revoke_tier_credits     total = floor-writeoff, used = used-writeoff   (equality, above)
--   create_user_credits     (alloc, 0, alloc)                              (0 <= alloc)
--   handle_new_auth_user    (alloc, 0, alloc)                              (0 <= alloc)
--   guest seed              total = GREATEST(total, ...)                   (only raises)
--
-- TWO MORE WRITERS ARE STILL LIVE and are easy to miss because nothing calls them: migration
-- 041's `charge_user_credits` and `refund_user_credits` were never dropped (118:374-377 still
-- REVOKEs and re-GRANTs EXECUTE on them). Both preserve the invariant on their intended input —
-- `charge_user_credits` is guarded by `AND (total - used) >= p_amount`, and
-- `refund_user_credits` is `used = GREATEST(0, used - p_amount)`, which only lowers `used`.
--
-- ⚠️ One NEW failure mode, and it is the desired one: `refund_user_credits` called with a
-- NEGATIVE amount raises `used` with no guard, and will now trip this CHECK instead of silently
-- minting credits. That is exactly the direction 118:371 called out. It fails loudly at the row
-- rather than being absorbed, which is the point of adding the constraint at all.
DO $$
BEGIN
    -- `conrelid` as well as `conname`: constraint names are unique per RELATION, not per
    -- database, so a conname-only guard would silently skip if any other table ever carried
    -- the same name. (047's user_credits block is conname-only; its FK block gets this right.)
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'user_credits_used_le_total'
           AND conrelid = 'public.user_credits'::regclass
    ) THEN
        ALTER TABLE public.user_credits
            ADD CONSTRAINT user_credits_used_le_total CHECK (used <= total) NOT VALID;
        ALTER TABLE public.user_credits VALIDATE CONSTRAINT user_credits_used_le_total;
    END IF;
END $$;


-- ── 2. create_user_credits: stamp tier_alloc, and ledger an honest split ──────────────────
--
-- Body is migration 135's verbatim except the `tier_alloc` column on the INSERT and the two
-- split columns on the ledger row.
CREATE OR REPLACE FUNCTION public.create_user_credits()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $function$
DECLARE
    _GUEST       CONSTANT UUID := '00000000-0000-4000-8000-00000000dead';
    v_alloc      INTEGER;
    v_now_et     TIMESTAMP;
    v_next_reset TIMESTAMPTZ;
BEGIN
    -- Never touch the shared guest bucket's fixed balance.
    IF NEW.id = _GUEST THEN
        RETURN NEW;
    END IF;

    SELECT monthly_credits INTO v_alloc
      FROM public.plan_credits
     WHERE tier = NEW.tier;

    -- An unknown tier seeds 0 rather than NULL. `total` is NOT NULL, so the old CASE (no
    -- ELSE arm) would have raised here and taken the parent INSERT with it.
    IF v_alloc IS NULL THEN
        v_alloc := 0;
    END IF;

    v_now_et     := (now() AT TIME ZONE 'America/New_York');
    v_next_reset := (date_trunc('month', v_now_et) + INTERVAL '1 month')
                        AT TIME ZONE 'America/New_York';

    -- `tier_alloc` alongside `total`: this IS the allocation granted for this period, and
    -- it is what `grant_tier_upgrade`'s replay guard reads. Left at its 0 default, a fresh
    -- account's guard compares against a number that never described a grant.
    INSERT INTO public.user_credits (user_id, total, used, tier_alloc, resets_at, updated_at)
    VALUES (NEW.id, v_alloc, 0, v_alloc, v_next_reset, NOW())
    ON CONFLICT (user_id) DO NOTHING;

    -- Only log the opening grant if we are the writer that created the row, so a re-insert
    -- or a race cannot double-log. Mirrors ensure_credit_period's own grant row shape —
    -- including, now, the split: `delta = granted_delta + purchased_delta` is a documented
    -- invariant, and an unsplit row here is indistinguishable from a pre-117 one.
    IF FOUND THEN
        INSERT INTO public.credit_transactions
            (user_id, delta, granted_delta, purchased_delta, reason, ref_id, balance_after)
        VALUES (NEW.id, v_alloc, v_alloc, 0, 'grant',
                to_char(v_now_et, 'YYYY-MM'), v_alloc);
    END IF;

    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    -- Seeding credits must never block account creation (the 044 pattern). Without this,
    -- any failure here unwinds handle_new_auth_user's block and leaves an auth.users row
    -- with no public.users mirror. ensure_credit_period creates the row on first read, so
    -- a user who lands here is degraded, not broken.
    RAISE WARNING 'create_user_credits failed for user % (tier %): %', NEW.id, NEW.tier, SQLERRM;
    RETURN NEW;
END;
$function$;


-- ── 3. handle_new_auth_user: the safety net seeds tier_alloc too ──────────────────────────
--
-- Body is migration 135's verbatim except `tier_alloc` on the user_credits INSERT. This path
-- is a no-op on every real signup (trg_create_user_credits has already inserted, so the
-- ON CONFLICT fires), but a net that catches the user with an inconsistent row is not a net.
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
SET row_security = off
AS $function$
DECLARE
    v_alloc INTEGER;
BEGIN
    INSERT INTO public.users (id, email, display_name, avatar_url)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(
            NEW.raw_user_meta_data->>'display_name',
            NEW.raw_user_meta_data->>'full_name',
            split_part(NEW.email, '@', 1)
        ),
        NEW.raw_user_meta_data->>'avatar_url'
    )
    ON CONFLICT (id) DO NOTHING;

    -- Belt-and-braces only: trg_create_user_credits on public.users has already created
    -- this row, so the ON CONFLICT makes this a no-op on every real signup. It stays as a
    -- safety net for a direct public.users insert that somehow skips the trigger — but it
    -- must read the SAME source, or the net catches the user with the wrong number.
    SELECT monthly_credits INTO v_alloc
      FROM public.plan_credits
     WHERE tier = 'free'::public.user_tier;

    INSERT INTO public.user_credits (user_id, total, used, tier_alloc)
    VALUES (NEW.id, COALESCE(v_alloc, 0), 0, COALESCE(v_alloc, 0))
    ON CONFLICT (user_id) DO NOTHING;

    RETURN NEW;
EXCEPTION WHEN OTHERS THEN
    -- Mirror failure must never block the auth.users insert. Log it (visible in Postgres
    -- logs) and let the auth row through. A backfill script can sync any users that
    -- landed here.
    RAISE WARNING 'handle_new_auth_user failed for user % (%): %',
        NEW.id, NEW.email, SQLERRM;
    RETURN NEW;
END;
$function$;


-- ── 4. Repair rows created between 139 and this migration ─────────────────────────────────
--
-- 139's backfill ran once, at apply time. Anything signed up since carries tier_alloc = 0
-- beside a non-zero total.
--
-- `tier_alloc = total` is only CORRECT where `total` is genuinely the allocation — and `total`
-- is explicitly a high-water mark after `revoke_tier_credits`. For a row whose `total` was
-- raised by anything other than a grant (a manual admin bump, a pre-139 mid-period tier
-- change), this records a grant that never happened, and `grant_tier_upgrade`'s replay guard
-- then refuses a legitimate grant of any allocation <= that number until the ET month rolls.
-- What makes it safe HERE, and did not apply to 139's sweep of the whole table, is that the
-- target population is narrow and known: rows created since 139 by `create_user_credits`, a
-- code path where `total` IS the allocation it just granted.
--
-- `total > 0` is load-bearing, not decoration — it is what excludes every row that legitimately
-- holds tier_alloc = 0: a free tier whose allocation really is 0, an unknown tier seeded 0, and
-- a row revoked when `plan_credits` had no 'free' entry (139:270-272 floors v_free to 0, which
-- drives `total` to 0 in the same statement).
--
-- The guest sentinel is excluded explicitly. Every function in this system opens with
-- `IF ... = _GUEST THEN RETURN`, but neither backfill did — so 139 already stamped the guest
-- bucket's balance into `tier_alloc`, a row now asserting that 100,000,050 credits were "the
-- allocation actually granted this period". Harmless (all three readers of `tier_alloc` return
-- early for the guest) but false, and there is no reason to reproduce it.
-- Safe to re-run: converges after one pass.
UPDATE public.user_credits
   SET tier_alloc = total
 WHERE tier_alloc = 0
   AND total > 0
   AND user_id <> '00000000-0000-4000-8000-00000000dead'::uuid;


COMMENT ON FUNCTION public.create_user_credits() IS
    'AFTER INSERT on public.users: seeds user_credits from plan_credits (never a literal), stamps tier_alloc with the same allocation (140), sets resets_at to the ET month boundary so ensure_credit_period does not treat a fresh row as due, and logs the opening grant with an honest granted/purchased split. Skips the guest sentinel. Fails soft — see 135/140.';

COMMIT;
