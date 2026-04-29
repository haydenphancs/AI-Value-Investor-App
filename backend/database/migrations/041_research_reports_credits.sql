-- 041_research_reports_credits.sql
--
-- Generate Analysis lifecycle: 5-credit upfront charge + refund on failure.
--
--   1. `is_refunded BOOLEAN` lets the iOS Reports tab render the
--      "[Refunded]" chip on failed cards (ReportCard.swift).
--   2. `credits_charged INT` makes the cost auditable per row and
--      future-proofs tier-based pricing without another migration.
--   3. `charge_user_credits(uuid, int)` does an atomic check-and-debit
--      against `user_credits.used` so two concurrent Generate Analysis
--      requests can't both succeed when only one user has enough credits
--      for one. Returns the new `remaining` value (NULL if rejected).
--
-- Safe to re-run: every statement is idempotent.

ALTER TABLE public.research_reports
    ADD COLUMN IF NOT EXISTS is_refunded BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS credits_charged INT NOT NULL DEFAULT 5;

-- Atomic charge. Single UPDATE matched on the (total - used) >= amount
-- predicate; if no row matches, no row is updated and the function
-- returns NULL. The Python caller treats NULL as INSUFFICIENT_CREDITS.
-- `remaining` is a GENERATED column (total - used) — we mutate `used`.
CREATE OR REPLACE FUNCTION public.charge_user_credits(
    p_user_id UUID,
    p_amount  INT
) RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    new_remaining INT;
BEGIN
    UPDATE public.user_credits
       SET used       = used + p_amount,
           updated_at = now()
     WHERE user_id   = p_user_id
       AND (total - used) >= p_amount
    RETURNING (total - used) INTO new_remaining;

    RETURN new_remaining;  -- NULL when WHERE missed (insufficient balance)
END;
$$;

-- Refund counterpart. GREATEST(0, ...) guards against double-refund
-- driving `used` negative if the same task is somehow retried.
CREATE OR REPLACE FUNCTION public.refund_user_credits(
    p_user_id UUID,
    p_amount  INT
) RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    new_remaining INT;
BEGIN
    UPDATE public.user_credits
       SET used       = GREATEST(0, used - p_amount),
           updated_at = now()
     WHERE user_id   = p_user_id
    RETURNING (total - used) INTO new_remaining;

    RETURN new_remaining;
END;
$$;
