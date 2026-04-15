-- Add associated_ticker and return metadata to whales table
-- Used for tiered annual return calculation:
--   Tier 1: Stock/ETF CAGR (associated_ticker)
--   Tier 2: 13F portfolio average
--   Tier 3: N/A (congressional)

ALTER TABLE whales ADD COLUMN IF NOT EXISTS associated_ticker TEXT;
ALTER TABLE whales ADD COLUMN IF NOT EXISTS return_source TEXT DEFAULT '';
ALTER TABLE whales ADD COLUMN IF NOT EXISTS return_label TEXT DEFAULT '';

-- Seed known ticker associations
UPDATE whales SET associated_ticker = 'BRK-B' WHERE name = 'Warren Buffett' AND associated_ticker IS NULL;
UPDATE whales SET associated_ticker = 'ARKK' WHERE name = 'Cathie Wood' AND associated_ticker IS NULL;
UPDATE whales SET associated_ticker = 'IEP' WHERE name = 'Carl Icahn' AND associated_ticker IS NULL;
UPDATE whales SET associated_ticker = 'MSTR' WHERE name = 'Michael Saylor' AND associated_ticker IS NULL;
