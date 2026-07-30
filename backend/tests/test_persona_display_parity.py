"""Persona display-label parity across the four places a label can live.

The five research personas were renamed from real people's names (Warren Buffett, …) to
style names to remove right-of-publicity / false-endorsement exposure and satisfy App
Review 5.2.1. That label now has to agree in four independent places:

  1. `persona_config.PersonaConfig.display_name`   — the canonical source
  2. `research.py _FALLBACK_PERSONAS`              — served when the DB query fails
  3. `migrations/103_persona_style_names.sql`      — the `agent_personas` rows served normally
  4. `pdf_report_service._PERSONA_DISPLAY`         — the exported PDF's persona header

Nothing asserted this before, and it drifted immediately: the DB-down fallback kept the
pre-rename tagline "Growth at Value" while the migration and iOS both said "Growth at a
Reasonable Price". These tests are that missing guard.

They also assert no real investor's name survives as a user-facing LABEL. Prompt prose
may still reference an investor's documented method by name (that is ordinary commentary
and is deliberately kept) — the rule is about labels, not references.

No network / Supabase — source and SQL text only.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.api.v1.endpoints.research import _FALLBACK_PERSONAS
from app.services.agents.persona_config import PERSONA_KEYS, get_persona_config
from app.services.pdf_report_service import _PERSONA_DISPLAY, _persona_display

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "database/migrations/103_persona_style_names.sql"
)

# Names that must never appear as a user-facing label again.
_REAL_INVESTOR_NAMES = (
    "Warren Buffett", "Cathie Wood", "Peter Lynch", "Bill Ackman", "Michael Burry",
)


def _fallback_by_key() -> dict[str, dict]:
    return {p["key"]: p for p in _FALLBACK_PERSONAS}


def _migration_names() -> dict[str, str]:
    """Parse `SET name = '…' … WHERE key = '…'` pairs out of migration 103."""
    sql = _MIGRATION.read_text()
    pattern = re.compile(
        r"SET\s+name\s*=\s*'([^']+)'.*?WHERE\s+key\s*=\s*'([^']+)'",
        re.DOTALL | re.IGNORECASE,
    )
    return {key: name for name, key in pattern.findall(sql)}


# ── Parity ────────────────────────────────────────────────────────────────────

def test_migration_exists_and_covers_every_persona():
    assert _MIGRATION.exists(), f"missing {_MIGRATION}"
    names = _migration_names()
    assert set(names) == PERSONA_KEYS, (
        f"migration 103 covers {sorted(names)}, PERSONA_KEYS is {sorted(PERSONA_KEYS)}"
    )


def test_config_and_migration_names_agree():
    names = _migration_names()
    for key in sorted(PERSONA_KEYS):
        assert names[key] == get_persona_config(key).display_name, (
            f"{key}: migration says {names[key]!r}, "
            f"persona_config says {get_persona_config(key).display_name!r}"
        )


def test_config_and_fallback_names_agree():
    fallback = _fallback_by_key()
    assert set(fallback) == PERSONA_KEYS, "fallback catalogue key set drifted"
    for key in sorted(PERSONA_KEYS):
        assert fallback[key]["name"] == get_persona_config(key).display_name, (
            f"{key}: _FALLBACK_PERSONAS name {fallback[key]['name']!r} != "
            f"display_name {get_persona_config(key).display_name!r}"
        )


def test_fallback_and_migration_taglines_agree():
    """The exact drift that shipped: fallback kept 'Growth at Value'."""
    sql = _MIGRATION.read_text()
    pattern = re.compile(
        r"SET\s+name\s*=\s*'[^']+',\s*\n\s*tagline\s*=\s*'([^']+)'.*?WHERE\s+key\s*=\s*'([^']+)'",
        re.DOTALL | re.IGNORECASE,
    )
    migration_taglines = {key: tag for tag, key in pattern.findall(sql)}
    assert migration_taglines, "tagline regex found nothing — migration format changed"
    fallback = _fallback_by_key()
    for key, tagline in sorted(migration_taglines.items()):
        assert fallback[key]["tagline"] == tagline, (
            f"{key}: fallback tagline {fallback[key]['tagline']!r} != "
            f"migration {tagline!r}"
        )


# ── No real names as labels ───────────────────────────────────────────────────

def test_no_display_name_is_a_real_investors_name():
    for key in sorted(PERSONA_KEYS):
        name = get_persona_config(key).display_name
        assert name not in _REAL_INVESTOR_NAMES, f"{key} still labeled {name!r}"


def test_no_fallback_or_migration_label_is_a_real_investors_name():
    for p in _FALLBACK_PERSONAS:
        assert p["name"] not in _REAL_INVESTOR_NAMES, f"fallback: {p['name']!r}"
    sql = _MIGRATION.read_text()
    for real in _REAL_INVESTOR_NAMES:
        assert f"name    = '{real}'" not in sql, f"migration sets name to {real!r}"


def test_pdf_persona_labels_carry_no_surnames():
    surnames = ("buffett", "wood", "lynch", "ackman", "burry")
    for lookup, label in _PERSONA_DISPLAY.items():
        low = label.lower()
        assert not any(s in low for s in surnames), (
            f"PDF label for {lookup!r} still names a real person: {label!r}"
        )


def test_pdf_labels_match_config_agent_labels():
    for key in sorted(PERSONA_KEYS):
        cfg = get_persona_config(key)
        assert _PERSONA_DISPLAY[key] == cfg.agent_label, (
            f"{key}: PDF {_PERSONA_DISPLAY[key]!r} != agent_label {cfg.agent_label!r}"
        )
        # The agent TAG form must resolve identically — the frozen report_data.agent
        # carries the tag, not the key.
        assert _PERSONA_DISPLAY[cfg.agent_tag] == cfg.agent_label


# ── PDF resolution, including pre-rename reports ──────────────────────────────

def test_pdf_resolves_by_key_and_by_tag():
    for key in sorted(PERSONA_KEYS):
        cfg = get_persona_config(key)
        assert _persona_display({"key": key}) == cfg.agent_label
        assert _persona_display({"name": cfg.display_name}) == cfg.agent_label


def test_pdf_resolves_reports_frozen_before_the_rename():
    """A report frozen pre-rename has agent.name == "Warren Buffett"; it must still map
    to the current label rather than printing the old surname."""
    legacy = {
        "Warren Buffett": "Quality Agent",
        "Cathie Wood": "Disruption Agent",
        "Peter Lynch": "GARP Agent",
        "Bill Ackman": "Activist Agent",
        "Michael Burry": "Contrarian Agent",
    }
    for old_name, expected in legacy.items():
        assert _persona_display({"name": old_name}) == expected, old_name
        # The pre-rename "<Surname> Agent" form too.
        surname = old_name.split()[-1]
        assert _persona_display({"name": f"{surname} Agent"}) == expected


def test_pdf_display_degrades_on_missing_or_unknown_agent():
    assert _persona_display({}) == "Cay AI Agent"
    assert _persona_display({"name": ""}) == "Cay AI Agent"
    assert _persona_display({"name": "   "}) == "Cay AI Agent"
    # Unknown persona: still produces something, never raises.
    assert _persona_display({"name": "Some New Style"}) == "Style Agent"
    assert _persona_display({"name": "Custom Agent"}) == "Custom Agent"
