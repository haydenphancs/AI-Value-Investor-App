-- seed_demo_account_credits.sql
--
-- Give the App Review demo account a credit balance that SURVIVES.
-- Run in Supabase Studio → SQL Editor. Not a migration: this is data for one account.
--
-- ┌──────────────────────────────────────────────────────────────────────────┐
-- │  EDIT THE EMAIL IN ONE PLACE PER BLOCK. It is marked <<< EDIT >>>.        │
-- │                                                                          │
-- │  An earlier version of this file repeated the address in both blocks and  │
-- │  the second copy got left as the placeholder. The subquery matched no     │
-- │  rows, passed NULL as p_user_id, and the RPC answered                     │
-- │  {"outcome":"invalid","reason":"guest_or_null_user"} — a clean no-op that │
-- │  reads like a failure of the ACCOUNT rather than of the SQL. Block 2 now  │
-- │  draws the id from a CTE, so p_user_id can never be NULL: a wrong address │
-- │  returns ZERO ROWS, which is unmistakable.                                │
-- └──────────────────────────────────────────────────────────────────────────┘
--
-- ═══ WHY A PLAIN UPDATE TO user_credits.total DOES NOT WORK ═══
--
-- `user_credits` holds TWO pools (migration 117):
--
--     granted    total / used                     -- monthly allowance, use-it-or-lose-it
--     purchased  purchased_total / purchased_used  -- consumable IAP, NEVER expires
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
-- ═══ WHY THE RPC AND NOT AN UPDATE ═══
--
-- `add_purchased_credits` also:
--   * runs `ensure_credit_period` first, which CREATES the balance row if the account has
--     never read its balance and stamps a correct `resets_at`,
--   * takes `SELECT ... FOR UPDATE`, so it cannot race a concurrent spend,
--   * is idempotent on `(environment, transaction_id)` — re-running is a no-op returning
--     {"outcome": "replay"}, not a double grant,
--   * writes a `credit_purchases` audit row explaining where the credits came from.
--
-- The transaction id is deliberately non-numeric; Apple's are numeric, so it cannot collide.


-- ── 1. LOOK FIRST ──────────────────────────────────────────────────────────
SELECT
    u.id, u.email, u.tier,
    uc.total AS granted_total,
    uc.used  AS granted_used,
    uc.purchased_total,
    uc.purchased_used,
    uc.spendable,
    uc.resets_at,
    CASE
        WHEN uc.user_id IS NULL    THEN 'no balance row yet — first read creates it at the tier allowance'
        WHEN uc.resets_at IS NULL  THEN '⚠️ resets_at IS NULL — the granted pool collapses on the NEXT read'
        WHEN uc.resets_at <= NOW() THEN '⚠️ reset is due — the granted pool collapses on the next read'
        ELSE 'granted pool stable until ' || uc.resets_at::TEXT
    END AS granted_pool_verdict
FROM public.users u
LEFT JOIN public.user_credits uc ON uc.user_id = u.id
WHERE u.email = 'appreview@caydexinvest.com';   -- <<< EDIT >>>
-- ZERO ROWS here means the address is wrong or the account was never created.


-- ── 2. SEED — 600 purchased credits = 30 AI reports ────────────────────────
WITH target AS (
    SELECT id, email
    FROM public.users
    WHERE email = 'appreview@caydexinvest.com'   -- <<< EDIT >>>  (the only copy in this block)
)
SELECT
    t.email AS seeded_account,
    public.add_purchased_credits(
        p_transaction_id => 'manual-demo-seed-v2',
        p_user_id        => t.id,
        p_product_id     => 'manual.demo.seed',
        p_credits        => 600,
        p_environment    => 'Production'
    ) AS result
FROM target t;

-- Expect ONE row: seeded_account = the address, result = {"outcome":"granted","credits":600,...}
--
-- ⚠️ "Success. No rows returned" means the email matched NO account — nothing was seeded.
--    Check the address, and that the account exists and is confirmed. This is the case that
--    used to surface as {"outcome":"invalid","reason":"guest_or_null_user"}.
--
-- A second run returns {"outcome":"replay"} with spendable unchanged. That is correct.


-- ── 3. VERIFY — re-run block 1 ─────────────────────────────────────────────
--   purchased_total = 600, purchased_used = 0, and resets_at set to the first of next
--   month (ET). The granted pool rolls monthly; the 600 never moves.
