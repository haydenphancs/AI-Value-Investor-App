-- Dedicated cache for insiders & ownership snapshot.
-- Same pattern as other snapshot cache tables.
-- TTL: 24 hours (checked in application code via cached_at).

CREATE TABLE IF NOT EXISTS snapshot_ownership_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker TEXT NOT NULL UNIQUE,
    response_json JSONB NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT now()
);

GRANT ALL ON snapshot_ownership_cache TO service_role;
GRANT ALL ON snapshot_ownership_cache TO authenticated;
