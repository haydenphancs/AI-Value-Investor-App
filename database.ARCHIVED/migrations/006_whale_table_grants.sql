-- ===== Migration 006: Table Grants =====
GRANT SELECT ON TABLE public.whales TO anon;
GRANT SELECT ON TABLE public.whales TO authenticated;
GRANT ALL ON TABLE public.whales TO service_role;

GRANT SELECT, INSERT, DELETE ON TABLE public.whale_follows TO authenticated;
GRANT ALL ON TABLE public.whale_follows TO service_role;

GRANT SELECT ON TABLE public.whale_holdings TO anon;
GRANT SELECT ON TABLE public.whale_holdings TO authenticated;
GRANT ALL ON TABLE public.whale_holdings TO service_role;

GRANT SELECT ON TABLE public.whale_sector_allocations TO anon;
GRANT SELECT ON TABLE public.whale_sector_allocations TO authenticated;
GRANT ALL ON TABLE public.whale_sector_allocations TO service_role;

GRANT SELECT ON TABLE public.whale_trade_groups TO anon;
GRANT SELECT ON TABLE public.whale_trade_groups TO authenticated;
GRANT ALL ON TABLE public.whale_trade_groups TO service_role;

GRANT SELECT ON TABLE public.whale_trades TO anon;
GRANT SELECT ON TABLE public.whale_trades TO authenticated;
GRANT ALL ON TABLE public.whale_trades TO service_role;

GRANT SELECT ON TABLE public.whale_filing_snapshots TO anon;
GRANT SELECT ON TABLE public.whale_filing_snapshots TO authenticated;
GRANT ALL ON TABLE public.whale_filing_snapshots TO service_role;

-- ===== Migration 007: RLS on whale_filing_snapshots =====
ALTER TABLE whale_filing_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "whale_filing_snapshots_select_all"
    ON whale_filing_snapshots FOR SELECT
    USING (true);

CREATE POLICY "whale_filing_snapshots_service_all"
    ON whale_filing_snapshots FOR ALL
    USING (auth.role() = 'service_role');
