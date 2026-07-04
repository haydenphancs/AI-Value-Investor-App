-- 081_trending_themes.sql
--
-- Why: the Caydex Home "Emerging Frontiers" section (formerly the mock-only
-- "2026 Trending Themes") is the last Home section still stubbed — the iOS
-- repository returned `themes: []`. This makes it a SERVER-DRIVEN editorial
-- surface: each theme card (a "Next-Wave" title, an image, and a curated list of
-- tickers) lives in a row here, so editors add/remove cards or change
-- tickers/images/order in Supabase with NO app release. The backend reads the
-- active rows, computes each card's ticker_count (len of tickers) and a live avg
-- daily % change (one FMP batch-quote fan-out over the deduped ticker union),
-- and folds `themes` into the existing GET /api/v1/home/dashboard response —
-- the same single-response pattern used by scanners + signals.
--
-- Schema mirrors the money_move_articles / lessons content pattern: first-class
-- columns for querying/ordering, RLS public-read + service-write, table GRANTs
-- (RLS restricts rows but does NOT grant table access — the 062/065 gotcha), a
-- sort index, a unique slug (deterministic upsert/lookup key), and a public
-- Storage bucket for the card images (mirror of 065's money-moves-media). The
-- card shows ONLY `title`; `category` and `tickers` organize/drive the metrics
-- and are not sent to the client.
--
-- Idempotent: every statement is guarded (IF NOT EXISTS / DROP POLICY IF EXISTS /
-- ON CONFLICT / GRANT) and safe to re-run. id is a uuid with a
-- gen_random_uuid() default, so there is no sequence to grant.

-- 1. Table -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.trending_themes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE,                 -- stable id, e.g. "silicon-rush"
    category    text NOT NULL,                        -- "boring name" (editorial grouping); NOT shown on the card
    title       text NOT NULL,                        -- the "Next-Wave" name shown on the card
    image_url   text,                                 -- public home-theme-media URL; NULL → iOS accent-gradient fallback
    accent_hex  text NOT NULL DEFAULT '22D3EE',       -- card accent (no leading '#'); editable per row
    tickers     text[] NOT NULL DEFAULT '{}',         -- curated constituents; drive ticker_count + avg change
    is_active   boolean NOT NULL DEFAULT true,        -- soft-hide a card without deleting it
    sort_order  integer NOT NULL DEFAULT 0,           -- ascending display order
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE  public.trending_themes IS
    'Server-driven Home "Emerging Frontiers" theme cards. Edit rows to change cards/tickers/images with no app release.';
COMMENT ON COLUMN public.trending_themes.category IS
    'Editorial grouping (the "boring name", e.g. "AI & Semiconductors"). Not sent to the client.';
COMMENT ON COLUMN public.trending_themes.tickers IS
    'Curated constituents. Backend serves ticker_count = cardinality(tickers) and avg daily % change; not sent verbatim.';
COMMENT ON COLUMN public.trending_themes.accent_hex IS
    'Card accent colour as a 6-digit hex WITHOUT a leading #, e.g. "22D3EE". iOS decodes via Color(hex:).';

-- 2. Indexes -----------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS idx_trending_themes_slug
    ON public.trending_themes(slug);
CREATE INDEX IF NOT EXISTS idx_trending_themes_active_sort
    ON public.trending_themes(is_active, sort_order);

-- 3. RLS + table GRANTs (public read, service write) -------------------------
ALTER TABLE public.trending_themes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "trending_themes_select_all" ON public.trending_themes;
CREATE POLICY "trending_themes_select_all" ON public.trending_themes
    FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "trending_themes_service_all" ON public.trending_themes;
CREATE POLICY "trending_themes_service_all" ON public.trending_themes
    FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT SELECT ON public.trending_themes TO anon, authenticated;
GRANT ALL    ON public.trending_themes TO service_role;

-- 4. Public Storage bucket for theme images (mirror of 065 money-moves-media) -
--    Layout: home-theme-media/<slug>.jpg (or .png/.webp). image_url stays NULL
--    on a row until an image is uploaded + the row's image_url is set.
INSERT INTO storage.buckets (id, name, public)
VALUES ('home-theme-media', 'home-theme-media', true)
ON CONFLICT (id) DO UPDATE SET public = EXCLUDED.public;

DROP POLICY IF EXISTS "home_theme_media_public_read" ON storage.objects;
CREATE POLICY "home_theme_media_public_read" ON storage.objects
    FOR SELECT TO anon, authenticated
    USING (bucket_id = 'home-theme-media');

DROP POLICY IF EXISTS "home_theme_media_service_write" ON storage.objects;
CREATE POLICY "home_theme_media_service_write" ON storage.objects
    FOR ALL TO service_role
    USING (bucket_id = 'home-theme-media')
    WITH CHECK (bucket_id = 'home-theme-media');

-- 5. Seed — curated starter themes (from the design's Boring Name → Next Wave
--    mapping) with well-known large-cap constituents. image_url NULL until
--    uploaded; editors trim/extend tickers + reorder in Supabase (no release).
--    Idempotent: ON CONFLICT (slug) DO NOTHING leaves any hand-edited row alone.
INSERT INTO public.trending_themes (slug, category, title, accent_hex, tickers, sort_order) VALUES
 ('silicon-rush',       'AI & Semiconductors', 'The Silicon Rush',       '22D3EE', ARRAY['NVDA','AVGO','AMD','TSM','ASML','MU','ARM','MRVL'], 10),
 ('modern-battlefield', 'War & Defense',       'The Modern Battlefield', 'FBBF24', ARRAY['LMT','RTX','NOC','GD','LHX','PLTR','HII'],        20),
 ('the-new-oil',        'Rare Earth Mining',   'The New Oil',            'FB923C', ARRAY['MP','ALB','LAC','FCX','SCCO'],                     30),
 ('robot-workforce',    'Robotics',            'The Robot Workforce',    'C084FC', ARRAY['ISRG','ABB','ROK','TER','SYM','PATH'],            40),
 ('hacking-health',     'Biotech & Pharma',    'Hacking Human Health',   '2DD4BF', ARRAY['LLY','NVO','VRTX','REGN','AMGN','MRNA'],          50),
 ('cyber-wars',         'Cybersecurity',       'The Cyber Wars',         '34D399', ARRAY['CRWD','PANW','ZS','FTNT','S','NET'],              60),
 ('powering-machine',   'Nuclear & Grid',      'Powering the Machine',   'FACC15', ARRAY['CEG','VST','GEV','NEE','SMR','OKLO'],             70),
 ('final-frontier',     'Space & Satellites',  'The Final Frontier',     '3B82F6', ARRAY['RKLB','ASTS','LUNR','BA','RTX'],                  80)
ON CONFLICT (slug) DO NOTHING;
