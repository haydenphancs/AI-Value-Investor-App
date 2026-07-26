-- 098_theme_image_urls.sql
--
-- Why: the 8 "Emerging Frontiers" themes in `trending_themes` shipped with
-- image_url = NULL, so both the Home cards and the detail-screen heroes fell back
-- to the flat accent-color gradient. This sets each theme's image_url to its
-- cinematic hero image, uploaded to the public `home-theme-media` Storage bucket
-- as <slug>.jpg. The iOS side already renders image_url via AsyncImage on BOTH the
-- card thumbnail (TrendingThemeTile) and the 300pt detail hero (ThemeDetailView),
-- so this lights up everywhere with NO app release and NO code change.
--
-- Images: real, free-for-commercial-use photos with NO attribution required —
-- NASA / U.S. Air Force public-domain (modern-battlefield, final-frontier) and the
-- Pexels license (the other six) — each run through one shared cinematic grade
-- (desaturate -> per-topic mood tint -> film grain -> vignette -> darkened bottom).
--
-- Idempotent: plain UPDATE keyed by the stable `slug`; safe to re-run.

UPDATE trending_themes SET image_url = 'https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/home-theme-media/silicon-rush.jpg'       WHERE slug = 'silicon-rush';
UPDATE trending_themes SET image_url = 'https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/home-theme-media/modern-battlefield.jpg' WHERE slug = 'modern-battlefield';
UPDATE trending_themes SET image_url = 'https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/home-theme-media/the-new-oil.jpg'        WHERE slug = 'the-new-oil';
UPDATE trending_themes SET image_url = 'https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/home-theme-media/robot-workforce.jpg'    WHERE slug = 'robot-workforce';
UPDATE trending_themes SET image_url = 'https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/home-theme-media/hacking-health.jpg'     WHERE slug = 'hacking-health';
UPDATE trending_themes SET image_url = 'https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/home-theme-media/cyber-wars.jpg'         WHERE slug = 'cyber-wars';
UPDATE trending_themes SET image_url = 'https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/home-theme-media/powering-machine.jpg'   WHERE slug = 'powering-machine';
UPDATE trending_themes SET image_url = 'https://gutlnhsjxrkxvrbqbbqq.supabase.co/storage/v1/object/public/home-theme-media/final-frontier.jpg'     WHERE slug = 'final-frontier';
