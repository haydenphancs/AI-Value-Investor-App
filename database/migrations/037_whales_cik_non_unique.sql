-- Allow multiple whales to share the same CIK.
-- Rationale: registry models investor/institution pairs (e.g., Ray Dalio
-- and Bridgewater Associates) as two separate rows both tied to the same
-- SEC filer. Both should display identical 13F data. The prior UNIQUE
-- constraint (idx_whales_cik) forced the second row to have cik=NULL,
-- which broke hydration for the duplicate.
--
-- Name uniqueness (enforced elsewhere via registry sync dedup) is
-- sufficient to prevent accidental row doubling.

DROP INDEX IF EXISTS idx_whales_cik;

-- Keep a non-unique lookup index (CIK is still frequently queried).
CREATE INDEX IF NOT EXISTS idx_whales_cik ON whales(cik) WHERE cik IS NOT NULL;
