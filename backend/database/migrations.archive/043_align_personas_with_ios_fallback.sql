-- 043_align_personas_with_ios_fallback.sql
--
-- The `agent_personas` table was inaccessible to service_role until
-- migration 042 restored the GRANT. While inaccessible, /personas served
-- a hardcoded fallback list (research.py _FALLBACK_PERSONAS), and that
-- styling is what the iOS app has been showing users.
--
-- Now that the table is queryable again, iOS sees the actual DB rows —
-- which drift from the fallback (different taglines, icons, accent
-- colors, and two extra personas iOS doesn't know about). This migration
-- aligns the four core personas to the values iOS users have always
-- seen, and deactivates Munger / Graham since the iOS picker only
-- knows the four core keys.

-- Warren Buffett — building.columns.fill (institutional), blue 3B82F6
UPDATE public.agent_personas
   SET tagline      = 'Safe, Long-term Value',
       icon_name    = 'building.columns.fill',
       accent_color = '3B82F6',
       description  = 'Focuses on fundamental value, strong moats, '
                      'consistent earnings, and long-term competitive '
                      'advantages. Ideal for conservative investors.',
       is_active    = TRUE
 WHERE key = 'warren_buffett';

-- Cathie Wood — bolt.fill (energy/disruption), purple A855F7
UPDATE public.agent_personas
   SET tagline      = 'Disruptive Innovation',
       icon_name    = 'bolt.fill',
       accent_color = 'A855F7',
       description  = 'Emphasizes disruptive innovation, emerging '
                      'technologies, and high-growth potential companies '
                      'that could reshape industries.',
       is_active    = TRUE
 WHERE key = 'cathie_wood';

-- Peter Lynch — chart.line.uptrend.xyaxis, cyan 06B6D4
UPDATE public.agent_personas
   SET tagline      = 'Growth at Value',
       icon_name    = 'chart.line.uptrend.xyaxis',
       accent_color = '06B6D4',
       description  = 'Looks for growth at a reasonable price (GARP), '
                      'with focus on companies you understand and can '
                      'spot in everyday life.',
       is_active    = TRUE
 WHERE key = 'peter_lynch';

-- Bill Ackman — megaphone.fill (activist), orange F97316
UPDATE public.agent_personas
   SET tagline      = 'Activist Value',
       icon_name    = 'megaphone.fill',
       accent_color = 'F97316',
       description  = 'Takes concentrated positions in high-quality '
                      'businesses, uses activist strategies to unlock '
                      'value, and focuses on companies with durable '
                      'competitive advantages.',
       is_active    = TRUE
 WHERE key = 'bill_ackman';

-- Hide personas iOS doesn't know about. The picker filters to its
-- four hardcoded keys; leaving these is_active=TRUE is harmless on iOS
-- but pollutes other clients (web, admin) and the GET /personas count.
UPDATE public.agent_personas
   SET is_active = FALSE
 WHERE key IN ('charlie_munger', 'benjamin_graham');
