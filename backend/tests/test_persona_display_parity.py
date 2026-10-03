"""Persona display-copy parity across the places the copy can live.

The five research personas were renamed from real people's names (Warren Buffett, …) to
style names to remove right-of-publicity / false-endorsement exposure and satisfy App
Review 5.2.1. Their copy — name, tagline, description — now has to agree in up to five
independent places:

  1. `persona_config.PersonaConfig.display_name`   — the canonical name
  2. `research.py _FALLBACK_PERSONAS`              — served when the DB query fails
  3. `migrations/NNN_*.sql` from 103 on            — the `agent_personas` rows served normally,
                                                     resolved LAST-WRITER-WINS (103 → 155 → 187)
  4. iOS `AnalysisPersona` fallbacks              — what paints offline / before the fetch
                                                     (`ResearchModels.swift`)
  5. `pdf_report_service._PERSONA_DISPLAY`         — the exported PDF's persona header

Nothing asserted this before, and it drifted immediately: the DB-down fallback kept the
pre-rename tagline "Growth at Value" while the migration and iOS both said "Growth at a
Reasonable Price". These tests are that missing guard.

They also assert that no real investor's name survives as user-facing copy, and (since
migration 187) that the copy makes no SUITABILITY claim — the Quality Compounder's
description used to end "Ideal for conservative investors." and its tagline called the style
"Safe". The report PROMPTS are now name-free too; that is pinned separately, by
`test_persona_no_real_names.py`.

No network / Supabase — source and SQL text only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.v1.endpoints.research import _FALLBACK_PERSONAS
from app.services.agents.persona_config import PERSONA_KEYS, get_persona_config
from app.services.pdf_report_service import _PERSONA_DISPLAY, _persona_display

import _persona_name_guard as guard

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _REPO / "backend/database/migrations"
# 103 is where the style names were introduced; it is the FLOOR, not the answer.
# Migrations are immutable once applied, so a later rename supersedes it rather
# than editing it, and this test has to resolve names the way Postgres does —
# by applying them in order and letting the last writer win.
_MIGRATION = _MIGRATIONS_DIR / "103_persona_style_names.sql"
_FLOOR = 103
_RESEARCH_MODELS = _REPO / "frontend/ios/ios/Models/ResearchModels.swift"

# The columns of `agent_personas` that GET /research/personas serves as COPY.
_COPY_FIELDS = ("name", "tagline", "description")

# Names that must never appear as a user-facing label again.
_REAL_INVESTOR_NAMES = (
    "Warren Buffett", "Cathie Wood", "Peter Lynch", "Bill Ackman", "Michael Burry",
)

# A style is an analysis METHOD, not a recommendation: copy may not say who it is FOR, or call
# it safe. (migration 187: "Ideal for conservative investors." / "Safe, Long-term Value")
_SUITABILITY = re.compile(
    r"\b(?:ideal|suited|suitable|right|perfect|best|great)\s+for\b"
    r"|\bconservative investors?\b"
    r"|\bsafe\b",
    re.IGNORECASE,
)


def _fallback_by_key() -> dict[str, dict]:
    return {p["key"]: p for p in _FALLBACK_PERSONAS}


# ── Last-writer-wins resolution of the agent_personas copy ────────────────────

# One UPDATE statement on agent_personas keyed on a SINGLE key. `[^;]` keeps the SET clause
# inside its own statement (a `WHERE key IN (…)` statement must not borrow the next
# statement's WHERE), and the copy literals never contain a semicolon.
_UPDATE = re.compile(
    r"UPDATE\s+(?:public\.)?agent_personas\s+SET\s+([^;]*?)\s+WHERE\s+key\s*=\s*'([^']+)'\s*;",
    re.IGNORECASE,
)
# `col = 'lit'` with Postgres adjacent-literal concatenation ('a' 'b') and '' escapes.
_ASSIGN = re.compile(r"(\w+)\s*=\s*((?:'(?:[^']|'')*'\s*)+)", re.IGNORECASE)
_LITERAL = re.compile(r"'((?:[^']|'')*)'")


def _strip_sql_comments(sql: str) -> str:
    # The copy literals contain no "--", so a line-comment strip cannot cut into one.
    return re.sub(r"--[^\n]*", "", sql)


def _parse_updates(sql: str) -> list[tuple[str, dict[str, str]]]:
    """Every `UPDATE agent_personas SET col = '…' … WHERE key = '…';` in `sql`, in order."""
    out = []
    for set_clause, key in _UPDATE.findall(_strip_sql_comments(sql)):
        cols = {}
        for col, lits in _ASSIGN.findall(set_clause):
            cols[col.lower()] = "".join(
                m.replace("''", "'") for m in _LITERAL.findall(lits)
            )
        out.append((key, cols))
    return out


def _resolved_copy() -> dict[str, dict[str, str]]:
    """The copy each persona row ends up with after every migration from 103 onward.

    Applied in numeric order, last writer wins — the same resolution Postgres
    performs. Reading 103 alone was correct only while it was the newest rename;
    the moment a later migration supersedes one of its rows (155 renamed
    `peter_lynch`, 187 restated all three columns), a 103-only read reports copy the
    database does not hold and fails a change that is actually correct.

    Deliberately scans the whole directory rather than naming migrations: a
    hardcoded list is a second place to forget to update, which is the exact
    failure mode this module exists to catch. Comments are stripped first — 187's own
    header quotes the copy it replaces.
    """
    rows: dict[str, dict[str, str]] = {}
    for path in sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if int(path.name[:3]) < _FLOOR:
            continue                     # pre-style-name era; 043 sets investor names
        for key, cols in _parse_updates(path.read_text()):
            rows.setdefault(key, {}).update(
                {c: v for c, v in cols.items() if c in _COPY_FIELDS}
            )
    return rows


def _migration_names() -> dict[str, str]:
    return {k: v["name"] for k, v in _resolved_copy().items() if "name" in v}


# ── iOS AnalysisPersona fallbacks ─────────────────────────────────────────────

_SWIFT_STRING = r'"((?:[^"\\\n]|\\.)*)"'
_IOS_PERSONA = re.compile(
    r"static\s+let\s+\w+\s*=\s*AnalysisPersona\(\s*"
    r"key:\s*" + _SWIFT_STRING + r",\s*"
    r"name:\s*" + _SWIFT_STRING + r",\s*"
    r"tagline:\s*" + _SWIFT_STRING + r",.*?"
    r"description:\s*" + _SWIFT_STRING + r"\s*\)",
    re.DOTALL,
)


# Innermost `/* … */` first, repeated: Swift block comments NEST, so a single non-greedy pass
# would stop at the first inner `*/` and leave the rest of a commented-out persona live.
_SWIFT_BLOCK_COMMENT = re.compile(r"/\*(?:(?!/\*|\*/).)*\*/", re.DOTALL)


def _strip_swift_comments(src: str) -> str:
    """Drop block comments (nesting-aware) and whole `//` lines. A trailing `// …` after code
    is left alone: it cannot hide a declaration, and stripping it would cut URLs in strings."""
    prev = None
    while prev != src:
        prev, src = src, _SWIFT_BLOCK_COMMENT.sub("", src)
    return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _parse_ios_fallbacks(src: str) -> dict[str, dict[str, str]]:
    return {
        key: {"name": name, "tagline": tagline, "description": description}
        for key, name, tagline, description in _IOS_PERSONA.findall(_strip_swift_comments(src))
    }


def _ios_fallbacks() -> dict[str, dict[str, str]]:
    return _parse_ios_fallbacks(_RESEARCH_MODELS.read_text())


# ── The resolver itself ───────────────────────────────────────────────────────

def test_the_resolver_joins_literals_unescapes_and_ignores_comments():
    sql = """
    -- UPDATE public.agent_personas SET name = 'Commented Out' WHERE key = 'a';
    UPDATE public.agent_personas
       SET name = 'First',
           description = 'It''s two '
                         'literals.'
     WHERE key = 'a';
    UPDATE public.agent_personas SET is_active = FALSE WHERE key IN ('x', 'y');
    UPDATE public.agent_personas SET tagline = 'T' WHERE key = 'b';
    """
    assert _parse_updates(sql) == [
        ("a", {"name": "First", "description": "It's two literals."}),
        ("b", {"tagline": "T"}),
    ]


def test_the_resolver_is_last_writer_wins_from_the_floor():
    rows = _resolved_copy()
    # 155 renamed peter_lynch after 103; 187 restated it. Either way it is not 103's value.
    assert rows["peter_lynch"]["name"] == "The Growth Hunter"
    assert "Everyday" not in rows["peter_lynch"]["name"]


# 043 deactivated two rows named after real investors. GET /research/personas serves every
# is_active row and iOS renders any key it is sent, so these must END inactive.
_REAL_NAME_ROWS = ("charlie_munger", "benjamin_graham")

# Every statement that writes agent_personas, in file order. `[^;]` holds: no literal in these
# migrations carries a semicolon (the copy resolver above relies on the same fact).
_PERSONA_WRITE = re.compile(
    r"\b(UPDATE|INSERT\s+INTO)\s+(?:public\.)?agent_personas\b([^;]*);", re.IGNORECASE,
)
_SET_WHERE = re.compile(r"^\s*SET\s+(.*?)(?:\s+WHERE\s+(.*?))?\s*$", re.IGNORECASE | re.DOTALL)
_IS_ACTIVE_ASSIGN = re.compile(r"\bis_active\s*=\s*(\w+)", re.IGNORECASE)
_WHERE_KEYS = re.compile(
    r"^key\s*(?:=\s*'([^']+)'|IN\s*\(([^)]*)\))$", re.IGNORECASE | re.DOTALL,
)


def _is_active_writes(sql: str) -> tuple[list[tuple[str, bool]], list[str]]:
    """Ordered `(key, active)` writes in `sql`, plus every statement it could not resolve.

    * An UPDATE whose SET clause assigns `is_active` ANYWHERE (alone, or beside copy columns
      as 043 does) and whose WHERE is `key = 'k'` / `key IN (…)` is a write. String literals
      are blanked first, so copy text that mentions "is_active = TRUE" is not one.
    * An UPDATE that assigns `is_active` to anything but TRUE/FALSE, or under any other WHERE
      (none, `AND`, `LIKE`, `ANY`), is UNRESOLVED — fail-closed, a human must look.
    * An INSERT naming a real-name key counts as ACTIVE (pessimistic): a re-seed, or an
      `ON CONFLICT … DO UPDATE`, can switch the row back on, so only a LATER explicit
      deactivation clears it.
    """
    writes: list[tuple[str, bool]] = []
    unresolved: list[str] = []
    for verb, body in _PERSONA_WRITE.findall(_strip_sql_comments(sql)):
        stmt = " ".join(f"{verb} {body}".split())
        if verb.upper().startswith("INSERT"):
            writes += [(k, True) for k in _REAL_NAME_ROWS if f"'{k}'" in body]
            continue
        m = _SET_WHERE.match(body)
        if not m:
            if _IS_ACTIVE_ASSIGN.search(_LITERAL.sub("''", body)):
                unresolved.append(stmt)
            continue
        set_clause, where = m.group(1), (m.group(2) or "").strip()
        flag = _IS_ACTIVE_ASSIGN.search(_LITERAL.sub("''", set_clause))
        if not flag:
            continue
        value = flag.group(1).upper()
        keys_m = _WHERE_KEYS.match(where)
        if value not in ("TRUE", "FALSE") or not keys_m:
            unresolved.append(stmt)
            continue
        one, many = keys_m.groups()
        for key in ([one] if one else re.findall(r"'([^']+)'", many)):
            writes.append((key, value == "TRUE"))
    return writes, unresolved


def _resolved_is_active() -> tuple[dict[str, bool], list[str]]:
    state: dict[str, bool] = {}
    unresolved: list[str] = []
    for path in sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if int(path.name[:3]) < _FLOOR:
            continue
        writes, bad = _is_active_writes(path.read_text())
        state.update(writes)                      # dict.update keeps the LAST write per key
        unresolved += [f"{path.name}: {s}" for s in bad]
    return state, unresolved


def test_the_real_name_rows_end_deactivated():
    """If 043 is missing or was reverted, 'Charlie Munger' would show on the persona row;
    187 re-asserts the deactivation so the served list does not depend on 043's history."""
    state, unresolved = _resolved_is_active()
    assert not unresolved, (
        f"is_active writes this resolver cannot place (rewrite as `WHERE key = '…'` / "
        f"`WHERE key IN (…)`, TRUE/FALSE): {unresolved}"
    )
    for key in _REAL_NAME_ROWS:
        assert state.get(key) is False, (
            f"no migration >= {_FLOOR} leaves {key} inactive (resolved: {state.get(key)!r})"
        )


def test_the_is_active_resolver_reads_both_forms():
    sql = (
        "UPDATE public.agent_personas SET is_active = FALSE WHERE key IN ('a', 'b');\n"
        "-- UPDATE public.agent_personas SET is_active = TRUE WHERE key = 'a';\n"
        "UPDATE agent_personas SET is_active = TRUE WHERE key = 'b';"
    )
    assert _is_active_writes(sql) == ([("a", False), ("b", False), ("b", True)], [])


def test_the_is_active_resolver_sees_a_reactivation_beside_other_columns():
    """043's own shape: `SET name = …, is_active = TRUE WHERE key = …`. The old parser required
    is_active to be the ONLY column, so this re-activation was invisible."""
    sql = (
        "UPDATE public.agent_personas SET is_active = FALSE WHERE key = 'charlie_munger';\n"
        "UPDATE public.agent_personas\n"
        "   SET name = 'Some Name', is_active    = TRUE,\n"
        "       description = 'Mentions is_active = FALSE in copy.'\n"
        " WHERE key = 'charlie_munger';"
    )
    assert _is_active_writes(sql) == (
        [("charlie_munger", False), ("charlie_munger", True)], [],
    )


def test_the_is_active_resolver_treats_an_insert_of_a_real_name_row_as_active():
    sql = (
        "UPDATE public.agent_personas SET is_active = FALSE WHERE key = 'benjamin_graham';\n"
        "INSERT INTO public.agent_personas (key, name, is_active)\n"
        "VALUES ('benjamin_graham', 'X', FALSE)\n"
        "ON CONFLICT (key) DO UPDATE SET is_active = EXCLUDED.is_active;\n"
        "INSERT INTO public.agent_personas (key, name) VALUES ('michael_burry', 'Y');"
    )
    assert _is_active_writes(sql) == (
        [("benjamin_graham", False), ("benjamin_graham", True)], [],
    )


@pytest.mark.parametrize("stmt", [
    "UPDATE public.agent_personas SET is_active = TRUE;",
    "UPDATE public.agent_personas SET is_active = TRUE WHERE key LIKE 'charlie%';",
    "UPDATE public.agent_personas SET is_active = TRUE WHERE key = 'a' AND name <> '';",
    "UPDATE public.agent_personas SET is_active = NOT is_active WHERE key = 'a';",
    "UPDATE public.agent_personas SET is_active = TRUE WHERE key = ANY(ARRAY['a']);",
])
def test_the_is_active_resolver_fails_closed_on_a_write_it_cannot_place(stmt: str):
    writes, unresolved = _is_active_writes(stmt)
    assert writes == [] and len(unresolved) == 1, (writes, unresolved)


def test_the_is_active_resolver_ignores_copy_only_updates():
    sql = "UPDATE public.agent_personas SET name = 'is_active = TRUE' WHERE key = 'a';"
    assert _is_active_writes(sql) == ([], [])


def test_the_ios_parser_finds_every_fallback():
    found = _ios_fallbacks()
    assert set(found) == PERSONA_KEYS, f"iOS AnalysisPersona fallbacks parsed: {sorted(found)}"


def test_the_ios_parser_skips_commented_out_personas():
    """A persona inside `/* … */` (Swift block comments nest) or on a `//` line is not live
    copy; parsing it would compare a dead literal and could mask the live one."""
    def decl(k: str) -> str:
        return (f'static let {k} = AnalysisPersona(key: "{k}", name: "N", tagline: "T", '
                f'iconName: "i", description: "D")')
    src = "\n".join([
        decl("live"),
        f"/* outer /* inner */ {decl('nested_block')} */",
        f"/* {decl('block')} */",
        f"    // {decl('line')}",
    ])
    assert set(_parse_ios_fallbacks(src)) == {"live"}


# ── Parity ────────────────────────────────────────────────────────────────────

def test_migration_exists_and_covers_every_persona():
    assert _MIGRATION.exists(), f"missing {_MIGRATION}"
    rows = _resolved_copy()
    for field in _COPY_FIELDS:
        covered = {k for k, v in rows.items() if field in v}
        assert PERSONA_KEYS <= covered, (
            f"migrations >= {_FLOOR} set {field!r} for {sorted(covered)}, "
            f"PERSONA_KEYS is {sorted(PERSONA_KEYS)}"
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


@pytest.mark.parametrize("field", _COPY_FIELDS)
def test_fallback_and_migration_copy_agree(field: str):
    """The exact drift that shipped: fallback kept 'Growth at Value'. Now every copy column,
    resolved across all migrations >= 103, not 103 alone."""
    rows = _resolved_copy()
    fallback = _fallback_by_key()
    for key in sorted(PERSONA_KEYS):
        assert fallback[key][field] == rows[key][field], (
            f"{key}.{field}: fallback {fallback[key][field]!r} != migration {rows[key][field]!r}"
        )


@pytest.mark.parametrize("field", _COPY_FIELDS)
def test_ios_fallback_and_backend_fallback_copy_agree(field: str):
    """iOS paints its own fallback before the fetch lands (and offline), so a stale literal
    there is what a user sees first."""
    ios = _ios_fallbacks()
    fallback = _fallback_by_key()
    for key in sorted(PERSONA_KEYS):
        assert ios[key][field] == fallback[key][field], (
            f"{key}.{field}: iOS {ios[key][field]!r} != backend fallback {fallback[key][field]!r}"
        )


# ── No real names, no suitability ─────────────────────────────────────────────

def _served_copy_surfaces() -> list[tuple[str, str]]:
    """Every copy value a user can be served, from all three sources."""
    out = []
    for key, cols in _resolved_copy().items():
        out += [(f"migration:{key}.{c}", v) for c, v in cols.items()]
    for p in _FALLBACK_PERSONAS:
        out += [(f"fallback:{p['key']}.{c}", p[c]) for c in _COPY_FIELDS]
    for key, cols in _ios_fallbacks().items():
        out += [(f"ios:{key}.{c}", v) for c, v in cols.items()]
    return out


def test_no_display_name_is_a_real_investors_name():
    for key in sorted(PERSONA_KEYS):
        name = get_persona_config(key).display_name
        assert name not in _REAL_INVESTOR_NAMES, f"{key} still labeled {name!r}"


def test_no_fallback_or_migration_label_is_a_real_investors_name():
    """Runs on the RESOLVED values (comments stripped), across every migration >= 103."""
    for p in _FALLBACK_PERSONAS:
        assert p["name"] not in _REAL_INVESTOR_NAMES, f"fallback: {p['name']!r}"
    hits = [(where, found) for where, text in _served_copy_surfaces()
            if (found := guard.violations(text))]
    assert not hits, f"real investor name / catchphrase in served persona copy: {hits}"


def test_no_served_copy_makes_a_suitability_claim():
    hits = [(where, m.group(0)) for where, text in _served_copy_surfaces()
            if (m := _SUITABILITY.search(text))]
    assert not hits, (
        f"persona copy says who a style is FOR, or calls it safe: {hits} — a style is an "
        "analysis method; Caydex knows nothing about the reader (ADVICE_BOUNDARY)"
    )


@pytest.mark.parametrize("planted", [
    "Ideal for conservative investors.",
    "Safe, Long-term Value",
    "Right for long-term savers.",
    "Best for beginners.",
])
def test_the_suitability_check_catches_a_planted_claim(planted: str):
    assert _SUITABILITY.search(planted)


@pytest.mark.parametrize("clean", [
    "Durable, Long-term Value",
    "asks for a margin of safety on price",
    "Growth at a Reasonable Price",
])
def test_the_suitability_check_leaves_method_copy_alone(clean: str):
    assert not _SUITABILITY.search(clean)


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
