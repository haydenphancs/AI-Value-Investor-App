-- Company profile cache for chat AI context injection
-- Stores formatted company profile + sector/industry data with 24h TTL
CREATE TABLE IF NOT EXISTS company_profile_cache (
    ticker TEXT PRIMARY KEY,
    profile_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_company_profile_cache_cached_at
    ON company_profile_cache (cached_at);

GRANT SELECT, INSERT, UPDATE ON company_profile_cache TO service_role;
