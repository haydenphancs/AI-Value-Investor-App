-- 099_theme_titles_shorten.sql
--
-- Why: on the compact "Emerging Frontiers" cards, the two longest theme titles —
-- "Hacking Human Health" and "Powering the Machine" — wrap onto a second line,
-- making those cards taller than the rest and misaligning the carousel grid.
-- Shorten both so every title fits a single line and all cards share one height.
--
-- Note: `title` is server-driven and shared by the card AND the detail-screen hero,
-- so this also shortens the big hero title on those two themes (intended).
-- Idempotent: plain UPDATE keyed by the stable `slug`; safe to re-run.

UPDATE trending_themes SET title = 'Hack Human Health' WHERE slug = 'hacking-health';
UPDATE trending_themes SET title = 'Power the Machine' WHERE slug = 'powering-machine';
