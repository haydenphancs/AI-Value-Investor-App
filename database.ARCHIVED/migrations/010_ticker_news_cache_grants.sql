-- Migration 010: Ticker News Cache Grants
-- Fixes "permission denied for table ticker_news_cache" error
-- The service_role needs explicit GRANT to access the table even with RLS bypass

GRANT ALL ON TABLE public.ticker_news_cache TO service_role;
GRANT SELECT ON TABLE public.ticker_news_cache TO anon;
GRANT SELECT ON TABLE public.ticker_news_cache TO authenticated;
