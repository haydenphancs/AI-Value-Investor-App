-- 138_reprice_credit_packs.sql
--
-- Why: the consumable credit-pack ladder is repriced. The four PRODUCT IDS are unchanged,
-- so this is a config change, not a catalog change — nothing needs re-creating in App Store
-- Connect, only the prices and the credit grants change.
--
--   old:  Starter $1.99/90   Plus $4.99/250   Power $9.99/550   Mega $19.99/1200
--   new:  Starter $2.99/130  Plus $5.99/280   Power $11.99/600  Mega $24.99/1300
--
-- ── The two invariants this ladder has to satisfy ─────────────────────────────────
--
-- 1. EVERY PACK STAYS ABOVE THE SUBSCRIPTION PER-CREDIT RATE, so a top-up can never
--    undercut a plan. Pro is the binding constraint at $14.99/1200 = $0.0124917/credit
--    (Max at $39.99/4000 = $0.0099975 is looser). The new ladder runs 1.84x -> 1.54x Pro:
--
--      Starter  299 /  130 = $0.023000   1.84x
--      Plus     599 /  280 = $0.021393   1.71x
--      Power   1199 /  600 = $0.019983   1.60x
--      Mega    2499 / 1300 = $0.019223   1.54x
--
-- 2. STRICTLY MONOTONIC: a dearer pack must be BETTER per credit, never worse.
--
--    This is what forced Mega to 1,300 rather than keeping migration 117's "Mega mirrors
--    Pro's 1,200 allowance" device. At the new $24.99, 1,200 credits would be
--    $0.020825/credit — 4% WORSE than Power's $0.019983. The ladder would invert at the
--    top and the biggest pack would become the worst value, penalising the user who
--    spends the most. The replacement framing is stronger anyway, and pushes toward the
--    subscription rather than away from it: Mega is 1,300 credits ONCE for $24.99, while
--    Pro is 1,200 credits EVERY MONTH for $14.99.
--
-- Both invariants are now enforced by tests/test_iap_product_and_privacy_parity.py rather
-- than asserted in a comment — they are derived from this seed and from plan_credits, so a
-- future reprice on either side re-arms the guard automatically.
--
-- ── Relationship to 117 ───────────────────────────────────────────────────────────
--
-- SUPERSEDES the seed in 117_purchased_credits_and_packs.sql:228-241. That migration is
-- applied and must not be edited, so its ladder comment (1.77x -> 1.33x, "Mega mirrors
-- Pro", "$5 less") is stale BY CONSTRUCTION. This file is the current one. No DDL here:
-- the table, its CHECK constraints, RLS policies and grants all still come from 117.
--
-- ⚠️ App Store Connect must be repriced in the SAME session as this migration. The
-- products do not exist there yet (LAUNCH_CHECKLIST §6b, blocked on the Paid Applications
-- Agreement), so today this is safe — nothing is purchasable. Once they DO exist, a
-- repriced credit_packs row against an old ASC price means the app quotes $5.99 while
-- Apple charges $4.99. `credits` is the half that actually matters: it is read server-side
-- on every purchase from this row, so it is what the buyer receives regardless of ASC.

INSERT INTO public.credit_packs (product_id, credits, price_cents, display_name, sort_order) VALUES
    ('com.phan.caydex.credits.starter',  130,  299, 'Starter', 1),
    ('com.phan.caydex.credits.plus',     280,  599, 'Plus',    2),
    ('com.phan.caydex.credits.power',    600, 1199, 'Power',   3),
    ('com.phan.caydex.credits.mega',    1300, 2499, 'Mega',    4)
ON CONFLICT (product_id) DO UPDATE
    SET credits      = EXCLUDED.credits,
        price_cents  = EXCLUDED.price_cents,
        display_name = EXCLUDED.display_name,
        sort_order   = EXCLUDED.sort_order,
        updated_at   = NOW();
-- Deliberately does NOT re-set is_active: retiring a pack is done in the DB and must
-- survive a re-run of this migration. Same rule as 117.
