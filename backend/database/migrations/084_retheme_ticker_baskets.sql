-- 084_retheme_ticker_baskets.sql
--
-- Why: expand + relevance-check the ticker basket behind every Emerging Frontiers
-- theme card. Each card's % and ticker_count are computed live from its
-- tickers[], so the basket IS the card. A per-card web-research pass (2025-2026
-- sources) validated that every current holding genuinely represents its theme
-- and added US-listed / ADR pure-plays; counts are intentionally uneven per the
-- editorial brief. EVERY symbol below was verified to resolve on the live FMP
-- stable /quote endpoint on 2026-07-04 (the same call the card path uses), so no
-- ticker will silently drop from a card.
--
-- Needed because 081 seeded these rows with `ON CONFLICT (slug) DO NOTHING`, so
-- re-applying 081 will NOT update an already-seeded database. This is an explicit,
-- editor-directed REPLACE of tickers[] keyed by slug (unconditional by slug — the
-- user is the content editor and wants exactly these lists). Idempotent: re-running
-- sets the same arrays. slug is the stable key (drives the theme-detail deep-link)
-- and is intentionally NOT touched. updated_at is refreshed for freshness tracking.
--
-- Editorial calls baked in (relevance over count):
--   * Modern Battlefield: dropped BA (Boeing) — majority-commercial, weak fit.
--   * The Final Frontier: dropped BA + RTX — space is a minor slice of each;
--     they belong on a defense card. Kept space-heavy primes LMT/NOC/LHX/KTOS.
--     Added SPCX (SpaceX) as the anchor — it IPO'd on Nasdaq 2026-06-12 (largest
--     IPO in history); verified live on FMP. Starlink is NOT separately listed
--     (folded into SPCX), so no separate ticker.
--   * Robot Workforce: dropped PATH (UiPath = software RPA, not physical robots);
--     ABB -> ABBNY (its only liquid US line; ABB is divesting its robotics unit).
--   * Cyber Wars: did NOT add CYBR — Palo Alto (PANW) acquired CyberArk Feb 2026.
--   * Still-trading pending-M&A names kept (flagged only): IRDM (Space), D (Power).

UPDATE public.trending_themes SET
    tickers = ARRAY['NVDA','TSM','AVGO','ASML','AMD','MU','ARM','MRVL','QCOM','AMAT','LRCX','KLAC','SNPS','CDNS','ALAB','CRDO','MPWR','COHR','LITE','INTC','GFS'],
    updated_at = now()
    WHERE slug = 'silicon-rush';           -- AI & Semiconductors (8 -> 21)

UPDATE public.trending_themes SET
    tickers = ARRAY['LMT','RTX','NOC','GD','LHX','PLTR','HII','AVAV','KTOS','LDOS','ESLT','TXT','CW','MRCY','KRMN','BAH'],
    updated_at = now()
    WHERE slug = 'modern-battlefield';      -- Defense & modern warfare (7 -> 16)

UPDATE public.trending_themes SET
    tickers = ARRAY['MP','USAR','ALB','SQM','LAC','PLL','SGML','FCX','SCCO','TECK','HBM','ERO','CCJ','UUUU','UEC','NXE','DNN','CRML','RIO'],
    updated_at = now()
    WHERE slug = 'the-new-oil';             -- Critical minerals: RE / Li / Cu / U (5 -> 19)

UPDATE public.trending_themes SET
    tickers = ARRAY['ISRG','NVDA','TSLA','ROK','TER','SYM','CGNX','ZBRA','FANUY','YASKY','EMR','NOVT','PH','SERV','RR','ABBNY'],
    updated_at = now()
    WHERE slug = 'robot-workforce';         -- Robotics & automation (6 -> 16)

UPDATE public.trending_themes SET
    tickers = ARRAY['LLY','NVO','VRTX','REGN','AMGN','MRNA','CRSP','NTLA','BEAM','VKTX','GPCR','AZN','NVS','MRK','ABBV','JNJ','PFE','ILMN'],
    updated_at = now()
    WHERE slug = 'hacking-health';          -- Biotech/pharma innovation (6 -> 18)

UPDATE public.trending_themes SET
    tickers = ARRAY['CRWD','PANW','ZS','FTNT','NET','S','OKTA','NTSK','CHKP','SAIL','RBRK','TENB','QLYS','RPD','VRNS'],
    updated_at = now()
    WHERE slug = 'cyber-wars';              -- Cybersecurity (6 -> 15)

UPDATE public.trending_themes SET
    tickers = ARRAY['CEG','VST','TLN','GEV','NEE','VRT','ETN','PWR','CCJ','OKLO','SMR','POWL','BWXT','LEU','D','NNE'],
    updated_at = now()
    WHERE slug = 'powering-machine';        -- Power for the AI era: nuclear/grid (6 -> 16)

UPDATE public.trending_themes SET
    tickers = ARRAY['SPCX','RKLB','ASTS','LUNR','FLY','VOYG','RDW','KRMN','IRDM','PL','BKSY','KTOS','LMT','NOC','LHX','GSAT','VSAT'],
    updated_at = now()
    WHERE slug = 'final-frontier';          -- Space & satellites (5 -> 17); SPCX = SpaceX (Nasdaq IPO 2026-06-12)
