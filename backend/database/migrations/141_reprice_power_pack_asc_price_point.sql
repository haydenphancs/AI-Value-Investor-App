-- 141_reprice_power_pack_asc_price_point.sql
--
-- Why: App Store Connect does not offer an $11.99 US price point for this product, so the
-- Power pack had to move to $12.99 — the nearest available point above it. The product IDs
-- are unchanged, so this is a config change, not a catalog change.
--
--   138:  Starter $2.99/130  Plus $5.99/280  Power $11.99/600  Mega $24.99/1300
--   141:  Starter $2.99/130  Plus $5.99/280  Power $12.99/650  Mega $24.99/1300
--                                                  ^^^^^^^^^^
--
-- ── Why the CREDITS had to move too, and are not a nicety ─────────────────────────
--
-- Keeping 600 credits at the new price would have INVERTED the ladder. Migration 138's
-- invariant #2 is that a dearer pack must be BETTER per credit, never worse:
--
--      Plus     599 /  280 = $0.021393
--      Power   1299 /  600 = $0.021650   <-- 1.2% WORSE than the cheaper Plus pack
--
-- A customer buying the bigger pack would have paid more per credit than one buying the
-- smaller — the exact failure 138 rejected at the top of the ladder when it refused to
-- keep Mega at 1,200. `test_the_pack_ladder_is_strictly_monotonic` fails the build on it.
--
-- At $12.99 the credit count has to land strictly between 608 and 675 to stay monotonic
-- (below 608 it undercuts Plus, above 675 it undercuts Mega). 650 sits mid-window:
--
--      Starter  299 /  130 = $0.023000   1.84x Pro
--      Plus     599 /  280 = $0.021393   1.71x
--      Power   1299 /  650 = $0.019985   1.60x   <-- unchanged rate, by design
--      Mega    2499 / 1300 = $0.019223   1.54x
--
-- Note the per-credit rate is essentially identical to 138's $0.019983: the buyer is not
-- worse off, they pay $1 more and get 50 more credits at the same effective rate.
--
-- Invariant #1 (no pack undercuts a plan) is untouched — Pro binds at $14.99/1200 =
-- $0.0124917 and every pack stays well above it.
--
-- ── The three surfaces that must agree with this row ──────────────────────────────
--
-- 1. App Store Connect — price $12.99, and the DESCRIPTION must read "650 credits. Never
--    expire." A stale "600 credits" there is a user-visible lie about what they receive,
--    because `credits` is read server-side FROM THIS ROW on every purchase and is what the
--    buyer actually gets, regardless of what ASC displays.
-- 2. frontend/ios/Caydex.storekit — the local StoreKit config used for simulator testing.
-- 3. subscription_service._FALLBACK_PACKS — display-only fallback for when this table is
--    briefly unreachable; a stale constant there misquotes the pack on the Buy Credits
--    screen. It cannot cause a wrong grant (the grant path always reads the DB).
--
-- Both are updated in the same change as this migration.
--
-- No DDL: the table, CHECKs, RLS and grants all still come from 117.

-- ⚠️ Seeds the COMPLETE ladder, not just the changed row. `_effective_seed` in
-- tests/test_iap_product_and_privacy_parity.py resolves the HIGHEST-numbered migration that
-- seeds credit_packs and treats it as authoritative, asserting it carries all four packs. A
-- one-row delta would become that authority and hide the other three — so the unchanged rows
-- are restated verbatim from 138 on purpose.
INSERT INTO public.credit_packs (product_id, credits, price_cents, display_name, sort_order) VALUES
    ('com.phan.caydex.credits.starter',  130,  299, 'Starter', 1),
    ('com.phan.caydex.credits.plus',     280,  599, 'Plus',    2),
    ('com.phan.caydex.credits.power',    650, 1299, 'Power',   3),
    ('com.phan.caydex.credits.mega',    1300, 2499, 'Mega',    4)
ON CONFLICT (product_id) DO UPDATE
    SET credits      = EXCLUDED.credits,
        price_cents  = EXCLUDED.price_cents,
        display_name = EXCLUDED.display_name,
        sort_order   = EXCLUDED.sort_order,
        updated_at   = NOW();
-- Deliberately does NOT re-set is_active, same rule as 117/138.
