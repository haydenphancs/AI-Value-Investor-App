-- 074_seed_michael_burry_persona.sql
--
-- Why: adding Michael Burry as a 5th research persona — the contrarian / deep-value
-- SKEPTIC (the app's "bear" lens: rewards cheap, hated, balance-sheet-sound names and
-- penalizes expensive, hyped, crowded darlings — it rates the AI darlings LOW, like the
-- real Burry's bets against them). The persona's system PROMPT and scoring weights live
-- in CODE (persona_config.py `_BURRY_CONFIG` + persona_scoring.py `PERSONA_WEIGHTS`);
-- this row carries only the iOS-facing METADATA the GET /personas endpoint serves
-- (name, tagline, icon, accent color, description). It mirrors the four core rows
-- aligned in #043 so the online picker matches the iOS offline fallback
-- (AnalysisPersona.michaelBurry). is_active = TRUE so the picker — which now knows the
-- `michael_burry` key (iOS updated in the same change) — surfaces it.
--
-- Idempotent: INSERT ... ON CONFLICT (key) DO UPDATE (key is UNIQUE).

INSERT INTO public.agent_personas
    (key, name, tagline, style, description, accent_color, icon_name, focus, is_active)
VALUES (
    'michael_burry',
    'Michael Burry',
    'Contrarian Deep Value',
    'Contrarian / Deep Value',
    'A contrarian skeptic who hunts deeply undervalued, out-of-favor companies '
      'with a large margin of safety, scrutinizes the balance sheet for hidden '
      'risk, and is wary of hype, crowded trades, and expensive darlings.',
    'DC2626',
    'magnifyingglass',
    'Margin of safety, balance-sheet forensics, contrarian skepticism of hype',
    TRUE
)
ON CONFLICT (key) DO UPDATE SET
    name         = EXCLUDED.name,
    tagline      = EXCLUDED.tagline,
    style        = EXCLUDED.style,
    description  = EXCLUDED.description,
    accent_color = EXCLUDED.accent_color,
    icon_name    = EXCLUDED.icon_name,
    focus        = EXCLUDED.focus,
    is_active    = TRUE,
    updated_at   = NOW();
