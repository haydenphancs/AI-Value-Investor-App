-- Permanent storage for AI-generated crypto snapshots.
-- Snapshots focus on stable knowledge (technology, tokenomics, risks)
-- NOT volatile market data (price, volume, market cap).
-- Generated once via Gemini, stored forever (manually refreshable).

CREATE TABLE IF NOT EXISTS crypto_snapshots (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    symbol TEXT NOT NULL,
    category TEXT NOT NULL,           -- "Origin and Technology", "Tokenomics", "Next Big Moves", "Risks"
    paragraphs JSONB NOT NULL,        -- ["paragraph1", "paragraph2", "paragraph3"]
    generated_at TIMESTAMPTZ DEFAULT now(),
    generated_by TEXT DEFAULT 'gemini-2.5-flash',
    UNIQUE(symbol, category)
);

CREATE INDEX IF NOT EXISTS idx_crypto_snapshots_symbol ON crypto_snapshots(symbol);

ALTER TABLE crypto_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON crypto_snapshots
    FOR ALL USING (true) WITH CHECK (true);

GRANT ALL ON crypto_snapshots TO service_role;
GRANT ALL ON crypto_snapshots TO authenticated;
