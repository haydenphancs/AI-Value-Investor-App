-- seed_demo_account_credits.sql
--
-- Give the App Review demo account a credit balance that SURVIVES.
-- Run in Supabase Studio → SQL Editor. Not a migration: this is data for one account.
--
-- ═══ WHY A PLAIN UPDATE TO user_credits.total DOES NOT WORK ═══
--
-- `user_credits` holds TWO pools (migration 117):
--
--     granted    total / used                    -- monthly allowance, use-it-or-lose-it
--     purchased  purchased_total / purchased_used -- consumable IAP, NEVER expires
--     spendable  = (total - used) + (purchased_total - purchased_used)   [generated column]
--
-- The split exists because App Store Guideline 3.1.1 forbids expiring something the user
-- bought. `ensure_credit_period` (migration 100) enforces the granted half:
--
--     IF v_row.resets_at IS NULL OR now() >= v_row.resets_at THEN
--         UPDATE public.user_credits SET total = v_alloc, used = 0, ...
--
-- `v_alloc` is the TIER allocation — 50 for free (`plan_credits`). And `GET /users/me/credits`
-- calls it BEFORE reading, so:
--
--   * seeding `total = 476` by hand leaves `resets_at` NULL,
--   * the reset therefore fires on the VERY FIRST balance read (not at the month boundary),
--   * and the reviewer sees 50 credits — two reports — where you left 476.
--
-- It never touches the purchased columns. That is the pool to seed.
--
-- ═══ WHAT THIS DOES ═══
--
-- Calls `add_purchased_credits` rather than UPDATEing the columns, because that RPC also:
--   * runs `ensure_credit_period` first, which CREATES the balance row if the account has
--     never read its balance and stamps a correct `resets_at` (so the granted 50 behaves
--     normally from here on),
--   * takes `SELECT ... FOR UPDATE` on the row, so it cannot race a concurrent spend,
--   * is idempotent on `(environment, transaction_id)` — re-running this file is a no-op
--     that returns `{"outcome": "replay"}`, not a double grant,
--   * writes a `credit_purchases` audit row, so six months from now it is obvious where
--     these credits came from.
--
-- The transaction id below is deliberately non-numeric. Apple's are numeric strings, so it
-- can never collide with a real purchase.

-- ── 1. LOOK FIRST. Confirm the account and see the damage. ──────────────────
--    Change the email, then run this block alone.
SELECT
    u.id,
    u.email,
    u.tier,
    uc.total            AS granted_total,
    uc.used             AS granted_used,
    uc.purchased_total,
    uc.purchased_used,
    uc.spendable,
    uc.resets_at,
    CASE
        WHEN uc.user_id IS NULL              THEN 'no balance row yet — first read will create it with 50'
        WHEN uc.resets_at IS NULL            THEN '⚠️ resets_at IS NULL — the granted pool collapses to the tier allowance on the NEXT read'
        WHEN uc.resets_at <= NOW()           THEN '⚠️ reset is due — the granted pool collapses on the next read'
        ELSE 'granted pool is stable until ' || uc.resets_at::TEXT
    END AS granted_pool_verdict
FROM public.users u
LEFT JOIN public.user_credits uc ON uc.user_id = u.id
WHERE u.email = 'REPLACE_WITH_DEMO_EMAIL';


-- ── 2. SEED. 600 purchased credits = 30 reports, or 30 reports' worth of chat. ──
--    Costs nothing and removes any chance a reviewer runs dry mid-review.
--    Re-runnable: the second run returns {"outcome": "replay"}.
SELECT public.add_purchased_credits(
    p_transaction_id => 'manual-demo-seed-v1',   -- non-numeric ⇒ cannot collide with Apple
    p_user_id        => (SELECT id FROM public.users WHERE email = 'REPLACE_WITH_DEMO_EMAIL'),
    p_product_id     => 'manual.demo.seed',      -- not a real pack; the RPC does not validate it
    p_credits        => 600,
    p_environment    => 'Production'
) AS result;

-- Expect: {"outcome": "granted", "credits": 600, "spendable": 650}
--   650 = 600 purchased + the 50 granted that `ensure_credit_period` just stamped.
-- A second run: {"outcome": "replay", ...} with spendable unchanged. That is correct.
--
-- ⚠️ `{"outcome": "invalid", "reason": "guest_or_null_user"}` means the email matched no row
--    in `public.users` — check the address, and that the account is CONFIRMED.


-- ── 3. VERIFY. Re-run block 1. You want: ───────────────────────────────────
--   purchased_total = 600, purchased_used = 0, spendable = 650,
--   resets_at = the first of next month (ET). The granted 50 will roll monthly and the
--   600 will not move — which is the whole point.
