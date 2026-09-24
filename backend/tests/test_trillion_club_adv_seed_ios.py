"""Trillion-Dollar Club Bets — ADVERSARIAL tests: the seed, its script, migration 175, the Swift layer.

Written by an independent test writer, not by the feature's authors. It adds to
`test_trillion_club_seed_integrity.py`, `test_trillion_club_schema_parity.py` and
`test_ios_trillion_club_guards.py`; it does not repeat them.

A. The seed VALIDATOR vs EVERY constraint migration 175 declares on the two seeded tables.
   The constraints are parsed out of the SQL (never restated). Every CHECK / UNIQUE / FK / PK and
   every NOT NULL gets at least one violating row, and the validator must reject each one. When a
   local PostgreSQL is installed (`initdb`, `pg_ctl`, `psql`), a throwaway cluster on a PRIVATE
   UNIX SOCKET (no TCP, no network, deleted afterwards) applies 175 twice and receives the same
   rows the way PostgREST writes them (`json_populate_record`). Each row must be refused by the
   exact constraint it targets, the real seed must load, and a dry-run plan against the
   database's own read-back must be empty.
B. The gaps: values the validator passes that Postgres refuses (INTEGER overflow, NUL, lone
   surrogates), values that CRASH the validator, duplicate JSON keys, and identity collisions
   (same ticker / CIK under two slugs, case- or Unicode-variant stake keys) that nothing checks.
C. The script's write discipline under --apply / --update: additive, never deletes, never
   rewrites a key column (so it can never fire ON UPDATE CASCADE), and what a Studio slug rename
   (ON UPDATE CASCADE) or delete (ON DELETE CASCADE) does to the next run.
D. Swift, EXECUTED with `xcrun swift -` (Foundation-only models file): every field of every
   `app.schemas.trillion_club` response model survives decode → re-encode through the real DTOs
   (typed sentinels; ints are 2^53+1, so a Double would be caught), every field tolerates null and
   a wrong JSON type, unknown enum strings are hidden, dates do not move with the device time
   zone, the logo fallback, and the chip sentences. Plus source scans: an ALLOW-list of colour
   tokens, a VoiceOver label or hide on every icon and row, the view model's stale-reload guard,
   and the detail's own ticker cover.

The `test_regression_*` tests exposed real defects (fixed 2026-09-24) and now pin the fixes.
Hermetic: no FMP, no Supabase, no network. The Postgres half skips when the tools are absent, the
Swift half when `xcrun` is.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import typing
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pytest
from pydantic import BaseModel

from app.schemas import trillion_club as tc
from scripts import seed_trillion_club as seed_mod
from test_trillion_club_schema_parity import match_brace, scan_swift, type_body

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
MIGRATION = BACKEND / "database" / "migrations" / "175_trillion_club.sql"
SEED_JSON = BACKEND / "data" / "trillion_club_seed.json"
IOS = REPO / "frontend" / "ios" / "ios"
MODELS = IOS / "Models" / "TrillionClubModels.swift"
CARD = IOS / "Views" / "Molecules" / "TrillionClubCard.swift"
CHIP = IOS / "Views" / "Molecules" / "ClubStakeChip.swift"
ROW = IOS / "Views" / "Molecules" / "ClubHoldingRow.swift"
INFO = IOS / "Views" / "Molecules" / "TrillionClubInfoSheet.swift"
SECTION = IOS / "Views" / "Organisms" / "TrillionClubSection.swift"
DETAIL = IOS / "Views" / "Screens" / "TrillionClubDetailView.swift"
DETAIL_VM = IOS / "ViewModels" / "TrillionClubDetailViewModel.swift"
LOGO_ATOM = IOS / "Views" / "Atoms" / "CompanyLogoView.swift"
VIEW_FILES = [SECTION, CARD, CHIP, ROW, INFO, DETAIL]
CLUB_SWIFT_FILES = [MODELS, DETAIL_VM, *VIEW_FILES]

TODAY = date(2026, 9, 24)
COMPANIES = "trillion_club_companies"
STAKES = "trillion_club_stakes"


def _seed() -> Dict[str, List[Dict[str, Any]]]:
    return seed_mod.load_seed(str(SEED_JSON))


def _src(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path} not present")
    return path.read_text(encoding="utf-8")


def _code(src: str) -> str:
    return scan_swift(src)[0]


# ══════════════════════════════════════════════════════════════════════════════════════
# A. Migration 175 parsed: every constraint on the two seeded tables
# ══════════════════════════════════════════════════════════════════════════════════════


def _create_block(sql: str, table: str) -> str:
    m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table}\s*\((.*?)\n\);", sql, re.S)
    assert m, f"CREATE TABLE public.{table} not found"
    return re.sub(r"--[^\n]*", "", m.group(1))


def _top_level_parts(block: str) -> List[str]:
    parts, depth, quote, cur = [], 0, False, []
    for ch in block:
        if ch == "'":
            quote = not quote
        elif not quote and ch == "(":
            depth += 1
        elif not quote and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quote:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def parsed_constraints(sql: str, table: str) -> Dict[str, str]:
    """{Postgres constraint name: kind} — `c` CHECK, `u` UNIQUE, `f` FK, `p` PK. Column-level
    constraints get Postgres' own generated names (`<table>_<column>_check` / `_fkey`)."""
    out: Dict[str, str] = {}
    for part in _top_level_parts(_create_block(sql, table)):
        named = re.match(r"CONSTRAINT\s+(\w+)\s+(CHECK|UNIQUE)\b", part)
        if named:
            out[named.group(1)] = "c" if named.group(2) == "CHECK" else "u"
            continue
        col = re.match(r"(\w+)\s", part).group(1)
        if re.search(r"\bCHECK\s*\(", part):
            out[f"{table}_{col}_check"] = "c"
        if re.search(r"\bREFERENCES\b", part):
            out[f"{table}_{col}_fkey"] = "f"
        if re.search(r"\bPRIMARY\s+KEY\b", part):
            out[f"{table}_pkey"] = "p"
    return out


def parsed_not_null(sql: str, table: str) -> List[str]:
    cols = []
    for part in _top_level_parts(_create_block(sql, table)):
        if part.startswith("CONSTRAINT"):
            continue
        if re.search(r"\bNOT\s+NULL\b|\bPRIMARY\s+KEY\b", part):
            cols.append(re.match(r"(\w+)\s", part).group(1))
    return cols


def _migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_parser_finds_the_constraints_it_should():
    """Non-vacuity of the parser: known constraints of both kinds are found, and a mutated
    migration (one CHECK deleted) loses exactly that one."""
    sql = _migration_sql()
    c = parsed_constraints(sql, COMPANIES)
    s = parsed_constraints(sql, STAKES)
    assert c["trillion_club_companies_slug_check"] == "c"
    assert c["trillion_club_manual_cap_is_explicit"] == "c"
    assert c["trillion_club_companies_pkey"] == "p"
    assert s["trillion_club_stakes_company_slug_fkey"] == "f"
    assert s["trillion_club_stake_natural_key"] == "u"
    assert len(c) >= 15 and len(s) >= 14
    mutated = sql.replace(" CHECK (card_kind IN ('thirteen_f', 'no_thirteen_f', 'non_us', 'whale_link'))", "", 1)
    assert mutated != sql
    assert set(c) - set(parsed_constraints(mutated, COMPANIES)) == {"trillion_club_companies_card_kind_check"}
    assert "display_name" in parsed_not_null(sql, COMPANIES)
    assert "verified_on" in parsed_not_null(sql, STAKES)


# ── The mutation catalogue: at least one violating row per constraint ──────────────────


def _bases(seed: Mapping) -> Dict[str, Dict[str, Any]]:
    """Valid rows to mutate (checked valid by the validator AND by Postgres below)."""
    by_slug = {c["slug"]: c for c in seed["companies"]}
    stakes = {(s["company_slug"], s["investee_name"], s["kind"]): s for s in seed["stakes"]}
    co = dict(copy.deepcopy(by_slug["apple"]), slug="adv-co", display_name="Adv Co",
              cap_symbol="ADVX", detail_symbol="ADVX", logo_symbol="ADVX", ciks=["0009999991"])
    man = dict(copy.deepcopy(by_slug["saudi-aramco"]), slug="adv-man", display_name="Adv Manual",
               logo_symbol=None)
    f13 = dict(copy.deepcopy(by_slug["amd"]), slug="adv-13f", display_name="Adv Filer",
               cap_symbol="ADVY", detail_symbol="ADVY", logo_symbol="ADVY", ciks=["0009999992"])
    st = dict(copy.deepcopy(stakes[("microsoft", "OpenAI Group PBC", "private")]),
              investee_name="Adv Stake", background=None)
    commit = dict(copy.deepcopy(stakes[("microsoft", "Anthropic", "commitment")]),
                  investee_name="Adv Commitment", background=None)
    return {"co": co, "man": man, "f13": f13, "st": st, "commit": commit}


_TABLE_OF_BASE = {"co": COMPANIES, "man": COMPANIES, "f13": COMPANIES, "st": STAKES, "commit": STAKES}

# (tag, base, overrides, expected Postgres outcome: (kind, constraint-or-column))
#   kind c = CHECK 23514, u/p = UNIQUE/PK 23505, f = FK 23503, n = NOT NULL 23502 (column)
_CATALOGUE: List[Tuple[str, str, Dict[str, Any], Tuple[str, str]]] = [
    ("slug-underscore", "co", {"slug": "Adv_Co"}, ("c", "trillion_club_companies_slug_check")),
    ("slug-trailing-newline", "co", {"slug": "adv-co\n"}, ("c", "trillion_club_companies_slug_check")),
    ("slug-41", "co", {"slug": "a" * 41}, ("c", "trillion_club_companies_slug_check")),
    ("name-empty", "co", {"display_name": ""}, ("c", "trillion_club_companies_display_name_check")),
    ("name-61", "co", {"display_name": "N" * 61}, ("c", "trillion_club_companies_display_name_check")),
    ("cik-short", "co", {"ciks": ["123"]}, ("c", "trillion_club_companies_ciks_check")),
    ("cik-11", "co", {"ciks": ["00099999911"]}, ("c", "trillion_club_companies_ciks_check")),
    ("card-kind", "co", {"card_kind": "thirteenF"}, ("c", "trillion_club_companies_card_kind_check")),
    ("country-lower", "co", {"home_country": "us"}, ("c", "trillion_club_companies_home_country_check")),
    ("country-3", "co", {"home_country": "USA"}, ("c", "trillion_club_companies_home_country_check")),
    ("cap-source", "co", {"cap_source": "fmp"}, ("c", "trillion_club_companies_cap_source_check")),
    ("manual-cap-zero", "man", {"manual_cap_usd": 0}, ("c", "trillion_club_companies_manual_cap_usd_check")),
    ("manual-cap-negative", "man", {"manual_cap_usd": -1.0}, ("c", "trillion_club_companies_manual_cap_usd_check")),
    ("manual-url-http", "man", {"manual_cap_source_url": "http://companiesmarketcap.com/x/"},
     ("c", "trillion_club_companies_manual_cap_source_url_check")),
    ("fx-zero", "man", {"manual_fx_rate": 0}, ("c", "trillion_club_companies_manual_fx_rate_check")),
    ("fx-negative", "man", {"manual_fx_rate": -3.75}, ("c", "trillion_club_companies_manual_fx_rate_check")),
    ("mode", "co", {"membership_mode": "forced"}, ("c", "trillion_club_companies_membership_mode_check")),
    ("job-last-cap", "co", {"last_market_cap": -1.0}, ("c", "trillion_club_companies_last_market_cap_check")),
    ("job-closes-above", "co", {"closes_at_or_above": -1}, ("c", "trillion_club_companies_closes_at_or_above_check")),
    ("job-closes-below", "co", {"closes_below": -1}, ("c", "trillion_club_companies_closes_below_check")),
    ("manual-auto", "man", {"membership_mode": "auto"}, ("c", "trillion_club_manual_cap_is_explicit")),
    ("manual-no-as-of", "man", {"manual_cap_as_of": None}, ("c", "trillion_club_manual_cap_is_explicit")),
    ("manual-no-url", "man", {"manual_cap_source_url": None}, ("c", "trillion_club_manual_cap_is_explicit")),
    ("manual-no-cap", "man", {"manual_cap_usd": None}, ("c", "trillion_club_manual_cap_is_explicit")),
    ("fmp-no-symbol", "co", {"cap_symbol": None}, ("c", "trillion_club_fmp_cap_has_symbol")),
    ("13f-no-cik", "f13", {"ciks": []}, ("c", "trillion_club_13f_needs_cik")),
    ("13f-wrong-card", "f13", {"card_kind": "no_thirteen_f"}, ("c", "trillion_club_13f_needs_cik")),
    ("whale-flag-wrong-card", "co", {"link_whale": True}, ("c", "trillion_club_whale_link_kind")),
    ("whale-card-no-flag", "co", {"card_kind": "whale_link"}, ("c", "trillion_club_whale_link_kind")),
    ("slug-taken", "co", {"slug": "nvidia"}, ("p", "trillion_club_companies_pkey")),
    ("fk", "st", {"company_slug": "no-such-co"}, ("f", "trillion_club_stakes_company_slug_fkey")),
    ("stake-kind", "st", {"kind": "privat"}, ("c", "trillion_club_stakes_kind_check")),
    ("investee-empty", "st", {"investee_name": ""}, ("c", "trillion_club_stakes_investee_name_check")),
    ("investee-61", "st", {"investee_name": "I" * 61}, ("c", "trillion_club_stakes_investee_name_check")),
    ("cusip-short", "st", {"investee_cusip": "ABC"}, ("c", "trillion_club_stakes_investee_cusip_check")),
    ("cusip-lower", "st", {"investee_cusip": "abcdefghi"}, ("c", "trillion_club_stakes_investee_cusip_check")),
    ("pct-zero", "st", {"ownership_pct": 0}, ("c", "trillion_club_stakes_ownership_pct_check")),
    ("pct-over", "st", {"ownership_pct": 100.5}, ("c", "trillion_club_stakes_ownership_pct_check")),
    ("pct-negative", "st", {"ownership_pct": -1.0}, ("c", "trillion_club_stakes_ownership_pct_check")),
    ("value-zero", "st", {"disclosed_value_usd": 0, "value_basis": "invested"},
     ("c", "trillion_club_stakes_disclosed_value_usd_check")),
    ("value-negative", "st", {"disclosed_value_usd": -5.0, "value_basis": "invested"},
     ("c", "trillion_club_stakes_disclosed_value_usd_check")),
    ("basis-unknown", "st", {"disclosed_value_usd": 5e9, "value_basis": "market_value"},
     ("c", "trillion_club_stakes_value_basis_check")),
    ("title-empty", "st", {"source_title": ""}, ("c", "trillion_club_stakes_source_title_check")),
    ("title-121", "st", {"source_title": "T" * 121}, ("c", "trillion_club_stakes_source_title_check")),
    ("url-http", "st", {"source_url": "http://www.sec.gov/x"}, ("c", "trillion_club_stakes_source_url_check")),
    ("url-ftp", "st", {"source_url": "ftp://sec.gov/x"}, ("c", "trillion_club_stakes_source_url_check")),
    ("confidence", "st", {"source_confidence": "tertiary"}, ("c", "trillion_club_stakes_source_confidence_check")),
    ("background-91", "st", {"background": "b" * 91}, ("c", "trillion_club_stakes_background_check")),
    ("value-no-basis", "st", {"disclosed_value_usd": 5e9, "value_basis": None},
     ("c", "trillion_club_stake_value_has_basis")),
    ("commitment-basis", "commit", {"value_basis": "invested"}, ("c", "trillion_club_commitment_basis")),
    ("secondary-published", "st", {"source_confidence": "secondary", "published": True},
     ("c", "trillion_club_secondary_never_published")),
    ("natural-key", "st", {"investee_name": "OpenAI Group PBC"}, ("u", "trillion_club_stake_natural_key")),
    # The seed never carries an id; Postgres gets the id of an existing row (see _probe_sql).
    ("stake-id", "st", {"id": "00000000-0000-4000-8000-000000000001"}, ("p", "trillion_club_stakes_pkey")),
]


def _not_null_mutations() -> List[Tuple[str, str, Dict[str, Any], Tuple[str, str]]]:
    sql = _migration_sql()
    out = []
    for col in parsed_not_null(sql, COMPANIES):
        if col in seed_mod.COMPANY_COLUMNS:
            out.append((f"null-company-{col}", "co", {col: None}, ("n", col)))
    for col in parsed_not_null(sql, STAKES):
        if col in seed_mod.STAKE_COLUMNS:
            out.append((f"null-stake-{col}", "st", {col: None}, ("n", col)))
    return out


def _all_mutations():
    return _CATALOGUE + _not_null_mutations()


def _row(bases, base: str, overrides: Mapping[str, Any]) -> Dict[str, Any]:
    row = copy.deepcopy(bases[base])
    row.update(copy.deepcopy(dict(overrides)))
    return row


def _problems_for_appended(mod, seed, table: str, row) -> List[str]:
    """The validator's problems for `row` appended to the real seed (only that row's)."""
    s = copy.deepcopy(seed)
    key = "companies" if table == COMPANIES else "stakes"
    s[key].append(row)
    label = f"{key}[{len(s[key]) - 1}:"
    return [p for p in mod.validate_seed(s, today=TODAY) if p.startswith(label)]


def test_every_base_row_is_valid():
    """Anti-vacuity: the rows the catalogue mutates are themselves clean, so each mutation's
    rejection is caused by its one change."""
    seed = _seed()
    bases = _bases(seed)
    s = copy.deepcopy(seed)
    s["companies"] += [bases["co"], bases["man"], bases["f13"]]
    s["stakes"] += [bases["st"], bases["commit"]]
    assert seed_mod.validate_seed(s, today=TODAY) == []


def catalogue_coverage_gaps(sql: str) -> List[str]:
    declared = set(parsed_constraints(sql, COMPANIES)) | set(parsed_constraints(sql, STAKES))
    covered = {exp[1] for _, _, _, exp in _CATALOGUE}
    out = [f"no violating row for {n}" for n in sorted(declared - covered)]
    out += [f"catalogue names {n}, which 175 lacks" for n in sorted(covered - declared)]
    nn = {c for c in parsed_not_null(sql, COMPANIES) if c in seed_mod.COMPANY_COLUMNS}
    nn |= {c for c in parsed_not_null(sql, STAKES) if c in seed_mod.STAKE_COLUMNS}
    if not nn or nn != {exp[1] for _, _, _, exp in _not_null_mutations() if exp[0] == "n"}:
        out.append("NOT NULL columns and null-mutations differ")
    return out


def test_catalogue_covers_every_constraint_and_not_null_of_175():
    """A new CHECK in 175 fails this until a violating row is added to _CATALOGUE."""
    assert catalogue_coverage_gaps(_migration_sql()) == []


def test_coverage_checker_fires_on_a_new_check():
    sql = _migration_sql()
    anchor = "    sort_order          INTEGER NOT NULL DEFAULT 0,"
    assert anchor in sql
    mutated = sql.replace(anchor, anchor[:-1] + " CHECK (sort_order < 1000),", 1)
    assert "no violating row for trillion_club_stakes_sort_order_check" in catalogue_coverage_gaps(mutated)


def validator_misses(mod) -> List[str]:
    """Catalogue rows (each violates a 175 constraint) that `mod`'s validator ACCEPTS."""
    seed = _seed()
    bases = _bases(seed)
    missed = []
    for tag, base, over, _ in _all_mutations():
        if not _problems_for_appended(mod, seed, _TABLE_OF_BASE[base], _row(bases, base, over)):
            missed.append(tag)
    return missed


def test_validator_rejects_every_row_the_migration_rejects():
    assert validator_misses(seed_mod) == []


def _mutated_seed_module(monkeypatch, old: str, new: str):
    """The seed script re-executed from an in-memory mutated copy of its source (the file on
    disk is never touched)."""
    src = Path(seed_mod.__file__).read_text(encoding="utf-8")
    assert src.count(old) == 1, f"mutation anchor {old!r} not unique"
    name = "seed_trillion_club_mutant"
    mod = types.ModuleType(name)
    mod.__file__ = seed_mod.__file__
    monkeypatch.setitem(sys.modules, name, mod)
    exec(compile(src.replace(old, new), seed_mod.__file__, "exec"), mod.__dict__)
    return mod


@pytest.mark.parametrize("old,new,expect_missed", [
    ('    if not _is_https(g("source_url")):', '    if False:', "url-ftp"),
    ("if pct is not None and (pct_f is None or not 0 < pct_f <= 100):",
     "if pct is not None and (pct_f is None or not 0 <= pct_f <= 100):", "pct-zero"),
    ('    if card_kind not in CARD_KINDS:', '    if False:', "card-kind"),
    ("    if g(\"use_13f\") is True and (card_kind != \"thirteen_f\" or not isinstance(ciks, list) or not ciks):",
     "    if g(\"use_13f\") is True and (not isinstance(ciks, list) or not ciks):", "13f-wrong-card"),
])
def test_validator_catalogue_catches_a_disabled_rule(monkeypatch, old, new, expect_missed):
    mutant = _mutated_seed_module(monkeypatch, old, new)
    assert expect_missed in validator_misses(mutant)


# ══════════════════════════════════════════════════════════════════════════════════════
# A'. A real Postgres (throwaway, private Unix socket) — the database decides
# ══════════════════════════════════════════════════════════════════════════════════════

# Rows Postgres ACCEPTS but the validator refuses on purpose (seed policy, not a CHECK).
# Pinned so each difference is deliberate — and so it is visible that the validator is the only
# guard for each of them.
_POLICY_ONLY: List[Tuple[str, str, Dict[str, Any]]] = [
    ("manual-without-fx", "man", {"manual_fx_rate": None, "manual_fx_source": None}),
    ("published-unreviewed", "co", {"reviewed_on": None}),
    ("non-us-card-us-country", "co", {"card_kind": "non_us"}),
    ("us-card-foreign-country", "co", {"home_country": "TW"}),
    ("manual-leftover-on-fmp-row", "co", {"manual_cap_usd": 1.0e12}),
    ("lowercase-symbol", "co", {"cap_symbol": "advx"}),
    ("outer-space-name", "co", {"display_name": " Adv Co"}),
    ("alias-repeats-cap-symbol", "co", {"symbol_aliases": ["ADVX"]}),
    ("duplicate-cik", "co", {"ciks": ["0009999991", "0009999991"]}),
    ("pct-without-basis", "st", {"ownership_basis": None}),
    ("basis-without-value", "st", {"value_basis": "invested"}),
    ("committed-up-to-off-commitment", "st", {"disclosed_value_usd": 5e9, "value_basis": "committed_up_to"}),
    ("as-of-after-verified", "st", {"as_of": "2026-09-24", "verified_on": "2026-09-23"}),
    ("verified-in-future", "st", {"verified_on": "2099-01-01"}),
    ("empty-background", "st", {"background": ""}),
    ("forecast-background", "st", {"background": "Microsoft will expand the stake."}),
    ("banned-investee-name", "st", {"investee_name": "Hot Picks Holdings"}),
    ("bare-https", "st", {"source_url": "https://"}),
    ("negative-sort", "st", {"sort_order": -1}),
    ("material-without-figure", "st", {"ownership_pct": None, "ownership_basis": None}),
    ("13f-note-off-13f-card", "st", {"kind": "on_13f_note"}),
    ("off-13f-without-symbol", "st", {"kind": "us_listed_off_13f"}),
    ("non-us-without-listing", "st", {"kind": "non_us_listed"}),
    ("private-with-listing", "st", {"local_listing": "Japan"}),
    ("long-local-listing", "st", {"kind": "non_us_listed", "local_listing": "L" * 61}),
]

# Rows Postgres refuses that the validator used to ACCEPT (fixed 2026-09-24; the regression tests below).
_VALIDATOR_GAPS: List[Tuple[str, str, Dict[str, Any], str]] = [
    ("sort-order-2^31", "st", {"sort_order": 2 ** 31}, "22003"),
    ("sort-order-2^63", "st", {"sort_order": 2 ** 63}, "22003"),
    ("nul-investee-name", "st", {"investee_name": "Adv\u0000Stake"}, "22P05"),
    ("nul-display-name", "co", {"display_name": "Adv\u0000Co"}, "22P05"),
    ("lone-surrogate-title", "st", {"source_title": "Microsoft 10-K \ud800"}, "22P02"),
]

# Rows the old ciks CHECK ACCEPTED (migration gap, fixed 2026-09-24 — the ciks regression test below).
_MIGRATION_GAPS: List[Tuple[str, str, Dict[str, Any]]] = [
    ("cik-null-element", "co", {"ciks": ["0009999991", None]}),
    ("cik-comma-joined-element", "co", {"ciks": ["0009999991,0009999993"]}),
    ("13f-on-a-null-cik", "f13", {"ciks": [None]}),
    # Also accepted by the old regex: array_to_string() turns [""] into "" (the empty match)
    # and flattens a 2-D array. A 2-D array is now refused by array_position (0A000).
    ("cik-empty-string-element", "co", {"ciks": [""]}),
    ("cik-two-dimensional", "co", {"ciks": [["0009999991"], ["0009999992"]]}),
]
# Shapes the tightened CHECK must still ACCEPT (anti-vacuity: the fix is not "refuse everything").
_CIKS_STILL_VALID: List[Tuple[str, str, Dict[str, Any]]] = [
    ("cik-empty-array", "co", {"ciks": []}),
    ("cik-two", "co", {"ciks": ["0009999991", "0009999993"]}),
    ("13f-one-cik", "f13", {"ciks": ["0009999994"]}),
]

_PROBE_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION public.adv_ins(tbl text, doc json) RETURNS void LANGUAGE plpgsql AS $f$
DECLARE cols text;
BEGIN
  -- PostgREST's shape: the column list is the JSON's keys, the values come from
  -- json_populate_record, so a missing key takes the column DEFAULT and an explicit null is NULL.
  SELECT string_agg(quote_ident(k), ',') INTO cols FROM json_object_keys(doc) AS k;
  EXECUTE format('INSERT INTO public.%I (%s) SELECT %s FROM json_populate_record(NULL::public.%I, $1)',
                 tbl, cols, cols, tbl) USING doc;
END $f$;

CREATE OR REPLACE FUNCTION public.adv_probe(tag text, tbl text, doc json) RETURNS void
LANGUAGE plpgsql AS $f$
DECLARE s text; c text; col text; m text;
BEGIN
  BEGIN
    PERFORM public.adv_ins(tbl, doc);
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = '__accepted__';  -- always roll back
  EXCEPTION WHEN others THEN
    GET STACKED DIAGNOSTICS s = RETURNED_SQLSTATE, c = CONSTRAINT_NAME, col = COLUMN_NAME,
                            m = MESSAGE_TEXT;
    IF m = '__accepted__' THEN
      RAISE NOTICE 'PROBE|%|ACCEPT', tag;
    ELSE
      RAISE NOTICE 'PROBE|%|REJECT|%|%|%|%', tag, s, c, col,
        replace(replace(m, E'\n', ' '), '|', '/');
    END IF;
  END;
END $f$;
"""

_SETUP_SQL = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.notification_job_state (
    job text PRIMARY KEY, enabled boolean NOT NULL DEFAULT true, updated_at timestamptz);
"""


def _sql_json(doc: Mapping[str, Any]) -> str:
    text = json.dumps(doc, ensure_ascii=True, allow_nan=False)
    assert "$ADVJ$" not in text
    return "$ADVJ$" + text + "$ADVJ$::json"


def _probe_sql(tag: str, table: str, doc: Mapping[str, Any]) -> str:
    if tag == "stake-id":
        body = {k: v for k, v in doc.items() if k != "id"}
        return (f"SELECT public.adv_probe('{tag}', '{table}', ({_sql_json(body)}::jsonb || "
                f"jsonb_build_object('id', (SELECT id FROM public.{STAKES} ORDER BY company_slug, "
                f"investee_name, kind LIMIT 1)))::json);")
    return f"SELECT public.adv_probe('{tag}', '{table}', {_sql_json(doc)});"


_PROBE_LINE = re.compile(r"PROBE\|([^|]*)\|(ACCEPT|REJECT)(?:\|([^|]*)\|([^|]*)\|([^|]*)\|(.*))?")


def _parse_probes(stderr: str) -> Dict[str, Dict[str, str]]:
    out = {}
    for line in stderr.splitlines():
        m = _PROBE_LINE.search(line)
        if m:
            out[m.group(1)] = {"verdict": m.group(2), "sqlstate": m.group(3) or "",
                               "constraint": m.group(4) or "", "column": m.group(5) or "",
                               "message": m.group(6) or ""}
    return out


def _pg_tool(name: str) -> Optional[str]:
    for cand in (shutil.which(name), f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}",
                 f"/Applications/Postgres.app/Contents/Versions/latest/bin/{name}"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


class _Postgres:
    PORT = "5439"

    def __init__(self) -> None:
        self.tools = {n: _pg_tool(n) for n in ("initdb", "pg_ctl", "psql")}
        self.root = tempfile.mkdtemp(prefix="tcpg")   # short: a socket path is capped at 103 bytes
        self.data = os.path.join(self.root, "data")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        self.started = False

    def start(self) -> Optional[str]:
        if not all(self.tools.values()):
            return "PostgreSQL tools (initdb, pg_ctl, psql) are not installed"
        if len(os.path.join(self.root, f".s.PGSQL.{self.PORT}")) > 100:
            return "temp dir too long for a Unix socket"
        for loc in ("en_US.UTF-8", "C.UTF-8", "C"):
            self.env["LC_ALL"] = self.env["LANG"] = loc  # macOS postmaster needs LC_ALL set
            r = subprocess.run([self.tools["initdb"], "-D", self.data, "-A", "trust", "-U", "postgres",
                                "--no-sync", "-E", "UTF8", f"--locale={loc}"],
                               env=self.env, capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                break
            shutil.rmtree(self.data, ignore_errors=True)
        else:
            return f"initdb failed: {r.stderr[-400:]}"
        opts = (f"-c listen_addresses='' -k '{self.root}' -p {self.PORT} -c fsync=off "
                f"-c full_page_writes=off -c synchronous_commit=off")
        r = subprocess.run([self.tools["pg_ctl"], "-D", self.data, "-l", os.path.join(self.root, "log"),
                            "-w", "-t", "60", "-o", opts, "start"],
                           env=self.env, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            log = Path(self.root, "log").read_text(errors="replace")[-600:] if Path(self.root, "log").exists() else ""
            return f"pg_ctl start failed: {r.stderr[-300:]} {log}"
        self.started = True
        return None

    def psql(self, sql: str, db: str = "postgres") -> subprocess.CompletedProcess:
        return subprocess.run([self.tools["psql"], "-h", self.root, "-p", self.PORT, "-U", "postgres",
                               "-d", db, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1"],
                              input=sql, env=self.env, capture_output=True, text=True, timeout=300)

    def stop(self) -> None:
        if self.started:
            subprocess.run([self.tools["pg_ctl"], "-D", self.data, "-m", "immediate", "stop"],
                           env=self.env, capture_output=True, text=True, timeout=60)
        shutil.rmtree(self.root, ignore_errors=True)


def _load_seed_sql(seed) -> str:
    lines = [f"SELECT public.adv_ins('{COMPANIES}', {_sql_json(c)});" for c in seed["companies"]]
    lines += [f"SELECT public.adv_ins('{STAKES}', {_sql_json(s)});" for s in seed["stakes"]]
    return "\n".join(lines)


def _readback_sql() -> str:
    return (
        f"SELECT '@@C' || coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb)::text FROM "
        f"(SELECT {','.join(seed_mod.COMPANY_COLUMNS)} FROM public.{COMPANIES}) t;\n"
        f"SELECT '@@S' || coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb)::text FROM "
        f"(SELECT {','.join(seed_mod.STAKE_COLUMNS)} FROM public.{STAKES}) t;\n"
    )


def _parse_readback(stdout: str) -> Tuple[List[Dict], List[Dict]]:
    got = {line[2]: json.loads(line[3:]) for line in stdout.splitlines() if line.startswith("@@")}
    return got["C"], got["S"]


@pytest.fixture(scope="module")
def pg():
    """A migrated throwaway cluster holding the real seed, plus every probe's verdict."""
    server = _Postgres()
    why = server.start()
    if why:
        server.stop()
        pytest.skip(why)
    try:
        info: Dict[str, Any] = {"server": server}
        setup = server.psql(_SETUP_SQL)
        assert setup.returncode == 0, setup.stderr
        info["apply_1"] = server.psql(_migration_sql())
        info["apply_2"] = server.psql(_migration_sql())
        funcs = server.psql(_PROBE_FUNCTIONS)
        assert funcs.returncode == 0, funcs.stderr
        seed = _seed()
        info["seed_load"] = server.psql("BEGIN;\n" + _load_seed_sql(seed) + "\nCOMMIT;")
        bases = _bases(seed)
        probes = []
        for tag, base, over in [(f"base-{b}", b, {}) for b in bases]:
            probes.append(_probe_sql(tag, _TABLE_OF_BASE[base], _row(bases, base, over)))
        for tag, base, over, _ in _all_mutations():
            probes.append(_probe_sql(tag, _TABLE_OF_BASE[base], _row(bases, base, over)))
        for tag, base, over in _POLICY_ONLY:
            probes.append(_probe_sql(f"policy-{tag}", _TABLE_OF_BASE[base], _row(bases, base, over)))
        for tag, base, over, _ in _VALIDATOR_GAPS:
            probes.append(_probe_sql(f"gap-{tag}", _TABLE_OF_BASE[base], _row(bases, base, over)))
        for tag, base, over in _MIGRATION_GAPS:
            probes.append(_probe_sql(f"mig-{tag}", _TABLE_OF_BASE[base], _row(bases, base, over)))
        for tag, base, over in _CIKS_STILL_VALID:
            probes.append(_probe_sql(f"ok-{tag}", _TABLE_OF_BASE[base], _row(bases, base, over)))
        run = server.psql("\n".join(probes))
        assert run.returncode == 0, run.stderr[-2000:]
        info["probes"] = _parse_probes(run.stderr)
        info["constraints"] = server.psql(
            "SELECT conrelid::regclass::text || '|' || conname || '|' || contype::text FROM pg_constraint "
            f"WHERE conrelid::regclass::text IN ('{COMPANIES}', '{STAKES}') ORDER BY 1;").stdout
        assert info["constraints"].strip(), "pg_constraint listing came back empty"
        yield info
    finally:
        server.stop()


def test_pg_migration_applies_twice_and_the_whole_seed_loads(pg):
    assert pg["apply_1"].returncode == 0, pg["apply_1"].stderr[-1500:]
    assert pg["apply_2"].returncode == 0, pg["apply_2"].stderr[-1500:]
    assert pg["seed_load"].returncode == 0, pg["seed_load"].stderr[-1500:]
    count = pg["server"].psql(f"SELECT count(*) FROM {COMPANIES}; SELECT count(*) FROM {STAKES};")
    seed = _seed()
    assert count.stdout.split() == [str(len(seed["companies"])), str(len(seed["stakes"]))]


def test_pg_constraint_list_equals_the_parsed_one(pg):
    """The parser (which the always-on catalogue test relies on) reads 175 the way Postgres does."""
    live = {}
    for line in pg["constraints"].splitlines():
        table, name, kind = line.split("|")
        live[name] = kind
    sql = _migration_sql()
    parsed = {**parsed_constraints(sql, COMPANIES), **parsed_constraints(sql, STAKES)}
    assert live == parsed


def test_pg_base_rows_are_accepted(pg):
    for base in ("co", "man", "f13", "st", "commit"):
        assert pg["probes"][f"base-{base}"]["verdict"] == "ACCEPT", pg["probes"][f"base-{base}"]


_SQLSTATE = {"c": "23514", "u": "23505", "p": "23505", "f": "23503", "n": "23502"}


def db_verdict_problems(probes: Mapping[str, Mapping[str, str]], mutations) -> List[str]:
    out = []
    for tag, _, _, (kind, name) in mutations:
        got = probes.get(tag)
        if got is None:
            out.append(f"{tag}: no verdict")
            continue
        if got["verdict"] != "REJECT":
            out.append(f"{tag}: Postgres ACCEPTED a row meant to violate {name}")
            continue
        field = "column" if kind == "n" else "constraint"
        if got["sqlstate"] != _SQLSTATE[kind] or got[field] != name:
            out.append(f"{tag}: rejected by {got['sqlstate']} {got['constraint'] or got['column']} "
                       f"({got['message']}), not by {name}")
    return out


def test_pg_rejects_each_catalogue_row_by_the_exact_constraint_it_targets(pg):
    """Grounds the always-on validator test: every catalogue row really violates THAT
    constraint (and nothing else first) in Postgres itself."""
    assert db_verdict_problems(pg["probes"], _all_mutations()) == []


def test_pg_catalogue_checker_fires_on_a_migration_missing_a_check(pg):
    """Non-vacuity of the DB half: a 175 with the slug CHECK deleted lets the slug rows in, and
    the checker reports them."""
    server = pg["server"]
    sql = _migration_sql()
    mutated = sql.replace(" CHECK (slug ~ '^[a-z0-9-]{1,40}$')", "", 1)
    assert mutated != sql
    server.psql("DROP DATABASE IF EXISTS advmut;")
    made = server.psql("CREATE DATABASE advmut;")
    assert made.returncode == 0, made.stderr
    try:
        for step in (_SETUP_SQL, mutated, _PROBE_FUNCTIONS):
            r = server.psql(step, db="advmut")
            assert r.returncode == 0, r.stderr[-800:]
        bases = _bases(_seed())
        muts = [m for m in _CATALOGUE if m[0] in ("slug-underscore", "slug-41", "name-61")]
        run = server.psql("\n".join(_probe_sql(t, _TABLE_OF_BASE[b], _row(bases, b, o)) for t, b, o, _ in muts),
                          db="advmut")
        problems = db_verdict_problems(_parse_probes(run.stderr), muts)
        assert any(p.startswith("slug-underscore") for p in problems)
        assert any(p.startswith("slug-41") for p in problems)
        assert not any(p.startswith("name-61") for p in problems)
    finally:
        server.psql("DROP DATABASE IF EXISTS advmut;")


def policy_misses(mod) -> List[str]:
    seed = _seed()
    bases = _bases(seed)
    return [tag for tag, base, over in _POLICY_ONLY
            if not _problems_for_appended(mod, seed, _TABLE_OF_BASE[base], _row(bases, base, over))]


def test_validator_enforces_every_policy_only_rule():
    assert policy_misses(seed_mod) == []


def test_policy_checker_fires_on_a_dropped_rule(monkeypatch):
    mutant = _mutated_seed_module(monkeypatch, '        if g("manual_fx_rate") is None or fx_source is None:',
                                  "        if False:")
    assert policy_misses(mutant) == ["manual-without-fx"]


@pytest.mark.parametrize("tag,base,over", _POLICY_ONLY, ids=[p[0] for p in _POLICY_ONLY])
def test_pg_accepts_what_only_the_validator_refuses(pg, tag, base, over):
    """These rules exist ONLY in the validator (a Studio edit bypasses them; the service's read
    validator is the next line). Both halves are pinned so a difference is never accidental."""
    assert pg["probes"][f"policy-{tag}"]["verdict"] == "ACCEPT", pg["probes"][f"policy-{tag}"]
    seed = _seed()
    row = _row(_bases(seed), base, over)
    assert _problems_for_appended(seed_mod, seed, _TABLE_OF_BASE[base], row), f"validator accepts {tag}"


@pytest.mark.parametrize("tag,base,over,sqlstate", _VALIDATOR_GAPS, ids=[g[0] for g in _VALIDATOR_GAPS])
def test_pg_refuses_the_values_the_validator_lets_through(pg, tag, base, over, sqlstate):
    """Grounds the regression tests in section B: Postgres (as PostgREST feeds it) really
    refuses these, so a seed the validator passed would fail half-way through --apply."""
    got = pg["probes"][f"gap-{tag}"]
    assert got["verdict"] == "REJECT" and got["sqlstate"] == sqlstate, got


def test_pg_dry_run_after_apply_plans_nothing(pg):
    """Idempotency against the database's REAL representation (DOUBLE as int or float, DATE as
    text, TEXT[] as a list): after the seed is loaded, a re-run must plan no insert and report no
    drift — otherwise every dry run would show phantom drift and --update would rewrite rows."""
    run = pg["server"].psql(_readback_sql())
    assert run.returncode == 0, run.stderr
    companies, stakes = _parse_readback(run.stdout)
    plan = seed_mod.plan_sync(_seed(), companies, stakes)
    assert (plan.company_inserts, plan.stake_inserts) == ([], [])
    assert (plan.company_updates, plan.stake_updates) == ([], [])
    assert (plan.orphan_companies, plan.orphan_stakes) == ([], [])


def test_pg_readback_checker_fires_on_a_representation_blind_compare(pg, monkeypatch):
    """Non-vacuity: a `_same` that compares types too (25.0 vs Postgres' 25) plans phantom drift
    on the real read-back — the check above would report it."""
    mutant = _mutated_seed_module(monkeypatch, "    if a is _MISSING or b is _MISSING:\n        return a is b\n",
                                  "    if a is _MISSING or b is _MISSING:\n        return a is b\n"
                                  "    if type(a) is not type(b):\n        return False\n")
    run = pg["server"].psql(_readback_sql())
    companies, stakes = _parse_readback(run.stdout)
    plan = mutant.plan_sync(_seed(), companies, stakes)
    assert plan.company_updates or plan.stake_updates


def test_pg_slug_rename_and_delete_cascade_as_the_orphan_warning_says(pg):
    """ON UPDATE CASCADE: a Studio rename carries the stakes; the next run re-plans the seeded slug
    (a second card for the same company) and reports the renamed row as an orphan — it never
    deletes or rewrites it. ON DELETE CASCADE: removing the orphan in Studio removes its stakes,
    which is what the script's DRIFT warning tells the owner."""
    seed = _seed()
    n_aramco = sum(1 for s in seed["stakes"] if s["company_slug"] == "saudi-aramco")
    run = pg["server"].psql(
        "BEGIN;\n"
        f"UPDATE public.{COMPANIES} SET slug = 'aramco' WHERE slug = 'saudi-aramco';\n"
        f"SELECT '@@N' || count(*) FROM public.{STAKES} WHERE company_slug = 'aramco';\n"
        + _readback_sql()
        + f"DELETE FROM public.{COMPANIES} WHERE slug = 'aramco';\n"
        f"SELECT '@@D' || count(*) FROM public.{STAKES} WHERE company_slug IN ('aramco', 'saudi-aramco');\n"
        "ROLLBACK;\n")
    assert run.returncode == 0, run.stderr
    lines = {line[2]: line[3:] for line in run.stdout.splitlines() if line.startswith("@@")}
    assert int(lines["N"]) == n_aramco > 0
    assert int(lines["D"]) == 0
    companies, stakes = _parse_readback(run.stdout)
    plan = seed_mod.plan_sync(seed, companies, stakes)
    assert [r["slug"] for r in plan.company_inserts] == ["saudi-aramco"]
    assert len(plan.stake_inserts) == n_aramco
    assert plan.orphan_companies == ["aramco"] and len(plan.orphan_stakes) == n_aramco
    assert plan.company_updates == [] and plan.stake_updates == []


def test_pg_checks_fire_on_a_non_idempotent_or_non_cascading_migration(pg):
    """Non-vacuity of the apply-twice and cascade tests: a 175 whose CREATE TABLE lost IF NOT
    EXISTS fails its second apply, and one whose FK lost ON UPDATE CASCADE refuses the rename."""
    server = pg["server"]
    sql = _migration_sql()
    no_ine = sql.replace("CREATE TABLE IF NOT EXISTS public.trillion_club_stakes",
                         "CREATE TABLE public.trillion_club_stakes", 1)
    no_cascade = sql.replace("ON DELETE CASCADE ON UPDATE CASCADE", "ON DELETE CASCADE", 1)
    assert no_ine != sql and no_cascade != sql
    try:
        for db in ("advmut2", "advmut3"):
            server.psql(f"DROP DATABASE IF EXISTS {db};")
            assert server.psql(f"CREATE DATABASE {db};").returncode == 0
            assert server.psql(_SETUP_SQL, db=db).returncode == 0
        assert server.psql(no_ine, db="advmut2").returncode == 0
        assert server.psql(no_ine, db="advmut2").returncode != 0, "a second apply should fail"
        for step in (no_cascade, _PROBE_FUNCTIONS, "BEGIN;\n" + _load_seed_sql(_seed()) + "\nCOMMIT;"):
            r = server.psql(step, db="advmut3")
            assert r.returncode == 0, r.stderr[-600:]
        rename = server.psql(f"UPDATE public.{COMPANIES} SET slug = 'aramco' WHERE slug = 'saudi-aramco';",
                             db="advmut3")
        assert rename.returncode != 0 and "foreign key" in rename.stderr
    finally:
        for db in ("advmut2", "advmut3"):
            server.psql(f"DROP DATABASE IF EXISTS {db};")


@pytest.mark.parametrize("tag", [g[0] for g in _MIGRATION_GAPS])
def test_regression_ciks_check_refuses_null_and_comma_joined_elements(pg, tag):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (migration 175): `array_to_string(ciks, ',') ~ '^([0-9]{10}(,[0-9]{10})*)?$'`
    SKIPS NULL elements (array_to_string drops them) and cannot see a comma INSIDE one element.
    So `ciks = '{0009999991,NULL}'`, `'{"0009999991,0009999993"}'` and even
    `use_13f = true, ciks = '{NULL}'` (which also satisfies trillion_club_13f_needs_cik:
    cardinality = 1) all pass — a Studio edit the jobs then refuse at run time
    (`jobs.py` raises "bad CIK"). Fixed: the regex now sits beside `array_position(ciks, NULL)
    IS NULL AND char_length(array_to_string(ciks, '')) = 10 * cardinality(ciks)` — no NULL
    element, exactly 10 characters per element. Also closes `[""]` and a 2-D array."""
    got = pg["probes"][f"mig-{tag}"]
    assert got["verdict"] == "REJECT", f"Postgres accepted {tag}: ciks CHECK has a hole"
    if tag != "cik-two-dimensional":   # 2-D is refused by array_position itself (0A000)
        assert (got["sqlstate"], got["constraint"]) == ("23514", "trillion_club_companies_ciks_check"), got


@pytest.mark.parametrize("tag", [g[0] for g in _CIKS_STILL_VALID])
def test_regression_ciks_check_still_accepts_well_formed_arrays(pg, tag):
    """The tightened CHECK still admits the empty array (Aramco), several CIKs, and a 13F
    company with one CIK."""
    got = pg["probes"][f"ok-{tag}"]
    assert got["verdict"] == "ACCEPT", got


# ══════════════════════════════════════════════════════════════════════════════════════
# B. Validator gaps and crashes (pure — no database needed)
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("value", [2 ** 31, 2 ** 63], ids=["2^31", "2^63"])
def test_regression_validator_refuses_sort_order_outside_postgres_integer(value):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (seed script): `sort_order` is INTEGER (int4) in 175, but `stake_problems` only
    checks `_is_int(v) and v >= 0`. A validated seed with sort_order 2147483648 fails at --apply
    (Postgres 22003 "out of range for type integer", proven by
    test_pg_refuses_the_values_the_validator_lets_through) — after the companies and earlier
    stakes were already written. Fix: also require `sort_order <= 2**31 - 1`."""
    seed = _seed()
    row = _row(_bases(seed), "st", {"sort_order": value})
    assert _problems_for_appended(seed_mod, seed, STAKES, row), "validator accepted an int4 overflow"


def test_regression_sort_order_boundary_is_int4_max():
    """The bound is exactly INTEGER's: 2**31 - 1 is valid, 2**31 is not."""
    seed = _seed()
    ok = _row(_bases(seed), "st", {"sort_order": 2 ** 31 - 1})
    assert _problems_for_appended(seed_mod, seed, STAKES, ok) == []


@pytest.mark.parametrize("base,column,text", [
    ("st", "investee_name", "Adv\u0000Stake"),
    ("co", "display_name", "Adv\u0000Co"),
    ("st", "source_title", "Microsoft 10-K \ud800"),
    # Columns _text_ok never sees: the check is on every string of the row.
    ("st", "source_url", "https://www.sec.gov/a\u0000b"),
    ("man", "manual_fx_source", "SAMA peg \udfff"),
    ("st", "ownership_basis", "as-converted\u0000"),
], ids=["nul-investee-name", "nul-display-name", "lone-surrogate-source-title",
        "nul-source-url", "lone-surrogate-fx-source", "nul-ownership-basis"])
def test_regression_validator_refuses_text_postgres_cannot_store(base, column, text):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (seed script): `_text_ok` checks length and outer whitespace only. JSON can carry
    "\\u0000" and a lone "\\ud800", `json.load` returns them, the validator passes them, and
    Postgres refuses the row at --apply (22P05 / 22P02 — see the pg grounding test). Fix: reject
    any text containing "\\x00" or a surrogate code point (U+D800–U+DFFF), in `_text_ok` or in
    `load_seed`. Fixed: every string of every row (list elements too) is checked, and the
    problem says why — display columns ALSO trip the invisible-character rule, so the reason
    is asserted, not just "some problem"."""
    seed = _seed()
    table = STAKES if base == "st" else COMPANIES
    row = _row(_bases(seed), base, {column: text})
    problems = _problems_for_appended(seed_mod, seed, table, row)
    assert problems, f"validator accepted {column}={text!r}"
    assert any(f"{column} holds text Postgres cannot store" in p for p in problems), problems


def _write_seed(tmp_path: Path, seed: Mapping) -> str:
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("where,column,value", [
    ("companies:saudi-aramco", "manual_cap_usd", 10 ** 400),
    ("stakes:0", "disclosed_value_usd", 10 ** 400),
    ("stakes:0", "ownership_pct", 10 ** 400),
    ("stakes:0", "investee_name", ["Anthropic"]),
    ("stakes:0", "kind", {"private": True}),
    ("stakes:0", "company_slug", ["nvidia"]),
    ("companies:apple", "slug", {"apple": 1}),
    ("companies:apple", "ciks", [["0000320193"]]),
    ("companies:apple", "symbol_aliases", [{"x": 1}]),
    ("stakes:0", "sort_order", 10 ** 400),
], ids=["huge-int-manual-cap", "huge-int-disclosed-value", "huge-int-ownership-pct",
        "list-investee-name", "object-kind", "list-company-slug", "object-slug", "nested-ciks",
        "object-alias", "huge-int-sort-order"])
def test_regression_malformed_value_exits_2_instead_of_crashing(tmp_path, where, column, value):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (seed script): the docstring promises "exit 2 — the seed failed validation", but
    `_finite_number` calls `float(v)` on an int of 400 digits (valid JSON) → OverflowError, and
    `validate_seed` hashes `stake_key(row)` → TypeError for a list/object in a key column. Either
    escapes `main()` as a traceback. Nothing is written (validation precedes the database), but
    the owner gets a stack trace instead of the row's name. Fix: in `_finite_number` catch
    OverflowError → None; in `validate_seed` skip the duplicate-key check (or repr the key) when
    `stake_key(row)` is unhashable."""
    seed = _seed()
    part, ident = where.split(":")
    if part == "companies":
        row = next(c for c in seed["companies"] if c["slug"] == ident)
    else:
        row = seed["stakes"][int(ident)]
    row[column] = value
    path = _write_seed(tmp_path, seed)
    try:
        code = seed_mod.main(["--validate-only", "--seed", path])
    except Exception as e:  # noqa: BLE001 — the defect under test IS the escaping exception
        pytest.fail(f"main() raised {type(e).__name__}: {e} instead of returning 2")
    assert code == 2


def test_overflowing_float_literals_slip_past_load_seed_but_not_the_validator(tmp_path):
    """`load_seed` refuses the NaN / Infinity TOKENS, but `1e400` is an ordinary number literal
    that json parses to float('inf'). Every numeric column must still be refused by the
    validator (it is today; this pins it)."""
    assert overflow_literal_misses(seed_mod, tmp_path) == []


def test_overflow_checker_fires_when_finiteness_is_not_checked(tmp_path, monkeypatch):
    mutant = _mutated_seed_module(monkeypatch, "    return f if math.isfinite(f) else None\n", "    return f\n")
    assert overflow_literal_misses(mutant, tmp_path)


def overflow_literal_misses(mod, tmp_path: Path) -> List[str]:
    seed = _seed()
    missed = []
    numeric = [("companies", "saudi-aramco", "manual_cap_usd"), ("companies", "saudi-aramco", "manual_fx_rate"),
               ("stakes", 0, "disclosed_value_usd"), ("stakes", 4, "ownership_pct"), ("stakes", 0, "sort_order")]
    for sign in ("", "-"):
        for part, ident, column in numeric:
            s = copy.deepcopy(seed)
            row = (next(c for c in s["companies"] if c["slug"] == ident) if part == "companies"
                   else s["stakes"][ident])
            row[column] = "__BIG__"
            raw = json.dumps(s).replace('"__BIG__"', f"{sign}1e400")
            path = tmp_path / "seed.json"
            path.write_text(raw, encoding="utf-8")
            loaded = mod.load_seed(str(path))
            problems = mod.validate_seed(loaded, today=TODAY)
            if not any(column in p for p in problems):
                missed.append(f"{sign}1e400 in {column}")
    return missed


def test_regression_load_seed_refuses_duplicate_json_keys(tmp_path):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (seed script): the seed is hand-edited, and `json.load` keeps only the LAST of two
    identical keys. A row reading `"published": false, ... "published": true` is reviewed as
    unpublished and written as published — no error, no warning. Fix: `json.load(...,
    object_pairs_hook=...)` that raises SeedError on a repeated key."""
    raw = SEED_JSON.read_text(encoding="utf-8")
    anchor = '"published": true'
    assert anchor in raw
    path = tmp_path / "seed.json"
    path.write_text(raw.replace(anchor, '"published": false, "published": true', 1), encoding="utf-8")
    with pytest.raises(seed_mod.SeedError, match="duplicate key 'published'"):
        seed_mod.load_seed(str(path))
    assert seed_mod.main(["--validate-only", "--seed", str(path)]) == 2
    # The top level too: a second "stakes" array would silently replace the first.
    path.write_text(raw.rstrip().rstrip("}") + ', "stakes": []}', encoding="utf-8")
    with pytest.raises(seed_mod.SeedError, match="duplicate key 'stakes'"):
        seed_mod.load_seed(str(path))


def _with_company(seed, **fields):
    s = copy.deepcopy(seed)
    s["companies"].append(fields)
    return s


@pytest.mark.parametrize("variant", ["same-ticker-and-cik", "alias-crosses-cap-symbol", "same-cik-only",
                                     "dot-vs-dash-share-class"])
def test_regression_validator_refuses_two_companies_claiming_one_ticker_or_cik(variant):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (seed script): only `slug` is checked for uniqueness. A second slug for the same
    company (a typo'd re-add, "nvda" beside "nvidia") passes validation and Postgres — neither
    has a UNIQUE on cap_symbol / ciks — so Home shows two cards, the daily job tracks the ticker
    twice, and the service's `_member_tag_map` has to log "symbol claimed by both". Fix: in
    `validate_seed`, refuse a normalised symbol (cap_symbol, detail_symbol, symbol_aliases) or a
    CIK that appears under two slugs."""
    seed = _seed()
    by_slug = {c["slug"]: c for c in seed["companies"]}
    if variant == "same-ticker-and-cik":
        extra = dict(copy.deepcopy(by_slug["nvidia"]), slug="nvda", display_name="NVIDIA Corp")
    elif variant == "alias-crosses-cap-symbol":
        extra = dict(copy.deepcopy(by_slug["alphabet"]), slug="google", display_name="Google",
                     cap_symbol="GOOG", symbol_aliases=["GOOGL"], detail_symbol="GOOG", logo_symbol="GOOG")
    elif variant == "same-cik-only":
        extra = dict(copy.deepcopy(by_slug["apple"]), slug="apple-inc", display_name="Apple Inc",
                     cap_symbol="AAPLX", detail_symbol="AAPLX", logo_symbol="AAPLX")
    else:   # BRK.B is BRK-B: the share-class separator is not an identity
        extra = dict(copy.deepcopy(by_slug["apple"]), slug="brk-dot", display_name="BRK dot",
                     ciks=["0009999995"], cap_symbol="BRK.B", detail_symbol="BRK.B", logo_symbol="BRK.B")
    problems = seed_mod.validate_seed(_with_company(seed, **extra), today=TODAY)
    assert problems, f"{variant}: two slugs claim one company and the validator is silent"
    assert all("already claimed by company" in p for p in problems), problems


def test_regression_distinct_companies_claim_distinct_identities():
    """Anti-vacuity: the real seed (GOOGL + alias GOOG, BRK-B + alias BRK-A, an empty-CIK
    Aramco) raises no claim, and a new company with its own ticker and CIK is clean."""
    seed = _seed()
    assert seed_mod.validate_seed(seed, today=TODAY) == []
    fresh = dict(copy.deepcopy(_bases(seed)["co"]))
    assert seed_mod.validate_seed(_with_company(seed, **fresh), today=TODAY) == []


@pytest.mark.parametrize("variant,name", [
    ("case", "ADV HOLDINGS LTD"),
    ("unicode-nfd", unicodedata.normalize("NFD", "Soci\u00e9t\u00e9 G\u00e9n\u00e9rale")),
    ("zero-width-space", "Adv Holdings\u200b Ltd"),
    ("double-space", "Adv  Holdings Ltd"),
    ("fullwidth", "\uff21dv Holdings Ltd"),
], ids=["case", "unicode-nfd", "zero-width-space", "double-space", "fullwidth"])
def test_regression_validator_refuses_near_duplicate_stake_keys(variant, name):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (seed script): the duplicate check compares (company_slug, investee_name, kind)
    byte-for-byte, as the UNIQUE constraint does. "Adv Holdings Ltd" / "ADV HOLDINGS LTD", an NFC
    and an NFD "Société Générale", or a name with an invisible U+200B are two keys to both, so
    the same stake is published twice on one card (the service sorts by `casefold()`, i.e. it
    treats them as one name). Fix: compare `unicodedata.normalize("NFKC", name).casefold()`
    with format characters (category Cf) removed, and refuse Cf characters in names outright."""
    seed = _seed()
    base = _row(_bases(seed), "st", {})
    first_name = unicodedata.normalize("NFC", name) if variant == "unicode-nfd" else "Adv Holdings Ltd"
    assert first_name != name
    s = copy.deepcopy(seed)
    s["stakes"] += [dict(base, investee_name=first_name), dict(base, investee_name=name, sort_order=9)]
    problems = seed_mod.validate_seed(s, today=TODAY)
    assert any("near-duplicate" in p or "invisible" in p for p in problems), (variant, problems)


def test_regression_distinct_stake_names_are_not_near_duplicates():
    """Anti-vacuity: different names under one company and kind stay valid."""
    seed = _seed()
    base = _row(_bases(seed), "st", {})
    s = copy.deepcopy(seed)
    s["stakes"] += [dict(base, investee_name="Adv Holdings Ltd"),
                    dict(base, investee_name="Adv Holdings Inc", sort_order=9)]
    assert seed_mod.validate_seed(s, today=TODAY) == []


@pytest.mark.parametrize("column,text", [
    ("background", "Follow-on offering closed in Jan 2026."),
    ("investee_name", "Mirror Biologics"),
], ids=["follow-on-at-start", "company-named-mirror"])
def test_regression_banned_copy_lets_a_leading_noun_through(column, text):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (seed script), the "DB accepts, validator WRONGLY rejects" direction: BANNED_COPY's
    extra alternative `^\\s*(?:follow|copy|mirror)\\b` fires on ANY field that begins with one of
    those words, so "Follow-on offering closed in Jan 2026." is refused — although the script's
    own comment says the words are banned "as instructions ... not as nouns ('follow-on
    offering')", mid-sentence "a follow-on offering" passes, and the read-time validator
    (`trillion_club_service.contains_banned_copy`) accepts the same text. A company named
    "Mirror …" / "Copy.ai" can never be seeded either. Fix: anchor the start-of-field rule to an
    imperative, e.g. `^\\s*(?:follow|copy|mirror)\\s+(?:this|these|that|their|its|his|her|the|them|our|what)\\b`
    (already covered by the mid-text rule) — or drop it. Fixed in the shared
    `app/services/trillion_club/copy_rules.py`: the start-of-field rule now needs an OBJECT
    (a club member or investor, someone's moves, or "into ..."), so "Follow Berkshire" is
    still banned (`test_regression_leading_imperative_with_an_object_is_still_banned`)."""
    from app.services import trillion_club_service as svc

    assert not svc.contains_banned_copy(text), "anti-vacuity: the read-time validator accepts this text"
    seed = _seed()
    row = _row(_bases(seed), "st", {column: text})
    assert _problems_for_appended(seed_mod, seed, STAKES, row) == []


@pytest.mark.parametrize("text", [
    "Follow Berkshire", "Follow Berkshire into Japan's trading houses.", "Copy Buffett",
    "Mirror insiders", "Copy Ackman's portfolio", "Mirror trades", "Follow Pelosi into chip stocks",
    "Stake since 2019. Follow NVIDIA.", "\u201cFollow Berkshire\u201d", "Tip: copy Tesla",
])
def test_regression_leading_imperative_with_an_object_is_still_banned(text):
    """The other half of the leading-noun fix: an imperative with an object is still refused,
    by the seed validator and by the shared rules the service imports."""
    from app.services.trillion_club import copy_rules

    assert copy_rules.contains_banned_copy(text), text
    seed = _seed()
    row = _row(_bases(seed), "st", {"background": text})
    assert any("banned" in p for p in _problems_for_appended(seed_mod, seed, STAKES, row)), text


# ══════════════════════════════════════════════════════════════════════════════════════
# C. Write discipline: additive, never deletes, never rewrites a key (no cascade can fire)
# ══════════════════════════════════════════════════════════════════════════════════════


class _Recorder:
    """A PostgREST-shaped fake that records every call chain. It has NO delete/upsert/rpc — a
    call to one is an AttributeError, i.e. a loud failure."""

    def __init__(self, tables: Mapping[str, List[Dict[str, Any]]]):
        self.tables = tables
        self.chains: List[List[Tuple[str, Any]]] = []

    def table(self, name):
        chain: List[Tuple[str, Any]] = [("table", name)]
        self.chains.append(chain)
        return _Chain(self, chain)


class _Chain:
    def __init__(self, rec: _Recorder, chain):
        self.rec, self.chain = rec, chain

    def _add(self, verb, arg):
        self.chain.append((verb, arg))
        return self

    def select(self, cols):
        return self._add("select", cols)

    def insert(self, row):
        return self._add("insert", copy.deepcopy(row))

    def update(self, payload):
        return self._add("update", copy.deepcopy(payload))

    def eq(self, col, val):
        return self._add("eq", (col, val))

    def execute(self):
        self.chain.append(("execute", None))
        name = self.chain[0][1]
        data = copy.deepcopy(self.rec.tables.get(name, [])) if any(v == "select" for v, _ in self.chain) else []
        return types.SimpleNamespace(data=data)


def _drifted_db(seed):
    """Every seeded row present, EVERY non-key column drifted, plus an orphan company + stake,
    and one seeded company/stake missing (so inserts happen too)."""
    companies, stakes = [], []
    for c in seed["companies"][1:]:
        row = {k: c[k] for k in seed_mod.COMPANY_COLUMNS}
        row["display_name"] = c["display_name"] + " (edited)"
        row["published"] = not c["published"]
        companies.append(row)
    companies.append(dict(companies[0], slug="orphan-co", display_name="Orphan"))
    for s in seed["stakes"][1:]:
        row = {k: s[k] for k in seed_mod.STAKE_COLUMNS}
        row["sort_order"] = s["sort_order"] + 100
        stakes.append(row)
    stakes.append(dict(stakes[0], investee_name="Orphan Stake"))
    return {COMPANIES: companies, STAKES: stakes}


_KEYS = {COMPANIES: ("slug",), STAKES: seed_mod.STAKE_KEY}


def write_discipline_violations(mod, argv: Sequence[str]) -> List[str]:
    seed = _seed()
    rec = _Recorder(_drifted_db(seed))
    code = mod.main([*argv, "--seed", str(SEED_JSON)], sb=rec)
    out = [] if code == 0 else [f"exit {code}"]
    forbidden = set(mod.JOB_COLUMNS) | {"id", "created_at"}
    for chain in rec.chains:
        table = chain[0][1]
        verbs = [v for v, _ in chain]
        if table not in (COMPANIES, STAKES):
            out.append(f"touched table {table}")
        if not set(verbs) <= {"table", "select", "insert", "update", "eq", "execute"}:
            out.append(f"unexpected verbs {verbs}")
        if "update" in verbs:
            payload = next(a for v, a in chain if v == "update")
            filters = {a[0]: a[1] for v, a in chain if v == "eq"}
            if set(filters) != set(_KEYS[table]):
                out.append(f"update on {table} filtered by {sorted(filters)}, not the full key")
            if set(payload) & (set(_KEYS[table]) | forbidden):
                out.append(f"update payload rewrites {sorted(set(payload) & (set(_KEYS[table]) | forbidden))}")
            if "orphan" in json.dumps(filters).lower():
                out.append("an orphan row was updated")
        for v, a in chain:
            if v == "insert" and set(a) & forbidden:
                out.append(f"insert carries {sorted(set(a) & forbidden)}")
    return out


def _count(mod, argv, verb):
    seed = _seed()
    rec = _Recorder(_drifted_db(seed))
    mod.main([*argv, "--seed", str(SEED_JSON)], sb=rec)
    return sum(1 for chain in rec.chains if any(v == verb for v, _ in chain))


def test_apply_update_is_additive_and_never_rewrites_a_key():
    """Under --apply --update with drift in every row, orphans and missing rows: only
    select/insert/update on the two tables, every update filtered by the FULL key, and no update
    payload ever carries a key column (so ON UPDATE CASCADE can never be fired by the script) or
    a job-written column."""
    assert write_discipline_violations(seed_mod, ["--apply", "--update"]) == []
    assert _count(seed_mod, ["--apply", "--update"], "update") > 0, "anti-vacuity: no update was made"
    assert _count(seed_mod, ["--apply", "--update"], "insert") == 2, "the two missing rows"


def test_apply_without_update_and_dry_run_write_nothing_they_should_not():
    assert _count(seed_mod, ["--apply"], "update") == 0
    assert _count(seed_mod, ["--apply"], "insert") == 2
    assert _count(seed_mod, [], "insert") == 0 and _count(seed_mod, [], "update") == 0


def test_write_discipline_checker_fires_on_a_key_rewrite(monkeypatch):
    """Non-vacuity: a script whose --update payload also re-sends the key columns is caught."""
    mutant = _mutated_seed_module(
        monkeypatch,
        "            payload = {c: seed_s[key][c] for c in diffs}\n",
        "            payload = {c: seed_s[key][c] for c in (*diffs, 'company_slug', 'investee_name')}\n")
    problems = write_discipline_violations(mutant, ["--apply", "--update"])
    assert any("rewrites" in p for p in problems)


def test_gating_checker_fires_when_update_is_not_gated(monkeypatch):
    mutant = _mutated_seed_module(monkeypatch, "    if update:\n        seed_c = ", "    if True:\n        seed_c = ")
    assert _count(mutant, ["--apply"], "update") > 0


def test_rename_in_studio_is_reported_never_deleted_pure():
    """The same ON UPDATE CASCADE scenario as the pg test, without a database: a renamed company
    comes back as an orphan (with its stakes) and the seeded slug is planned as an insert. The
    script never plans a delete — the DRIFT warning is the owner's only signal."""
    seed = _seed()
    companies = [{k: c[k] for k in seed_mod.COMPANY_COLUMNS} for c in seed["companies"]]
    stakes = [{k: s[k] for k in seed_mod.STAKE_COLUMNS} for s in seed["stakes"]]
    for c in companies:
        if c["slug"] == "saudi-aramco":
            c["slug"] = "aramco"
    for s in stakes:
        if s["company_slug"] == "saudi-aramco":
            s["company_slug"] = "aramco"
    plan = seed_mod.plan_sync(seed, companies, stakes)
    assert [r["slug"] for r in plan.company_inserts] == ["saudi-aramco"]
    assert plan.orphan_companies == ["aramco"]
    assert all(k[0] == "aramco" for k in plan.orphan_stakes) and plan.orphan_stakes
    assert not hasattr(plan, "company_deletes") and not hasattr(plan, "stake_deletes")


# ══════════════════════════════════════════════════════════════════════════════════════
# D. Swift — executed (Foundation-only models file through `xcrun swift -`)
# ══════════════════════════════════════════════════════════════════════════════════════


def _response_models() -> List[type]:
    return [v for v in vars(tc).values() if isinstance(v, type) and issubclass(v, BaseModel)
            and v is not BaseModel and v.__module__ == tc.__name__]


def _unwrap(ann):
    if typing.get_origin(ann) is typing.Union:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        assert len(args) == 1, ann
        return _unwrap(args[0])
    return ann


def _sentinel(cls, salt: int = 0) -> BaseModel:
    """Every field set to a NON-default value of its declared type: str → a unique label, float →
    a non-integral value a Float/Int would lose, int → 2^53+1+i (a Double would lose it), bool →
    true, nested models → their own sentinel."""
    values = {}
    for i, (name, field) in enumerate(cls.model_fields.items()):
        values[name] = _value_for(_unwrap(field.annotation), f"{cls.__name__}.{name}", i + salt)
    return cls(**values)


def _value_for(ann, label: str, i: int):
    if typing.get_origin(ann) in (list, List):
        (inner,) = typing.get_args(ann)
        return [_value_for(_unwrap(inner), label + "[]", i)]
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return _sentinel(ann, i * 100)
    if ann is bool:
        return True
    if ann is int:
        return 9007199254740993 + i
    if ann is float:
        return 0.1234567890123 + i
    if ann is str:
        return f"s:{label}"
    raise AssertionError(f"no sentinel for {ann!r} ({label}) — extend _value_for")


def _wrong_type(ann):
    ann = _unwrap(ann)
    if typing.get_origin(ann) in (list, List):
        return {"not": "a list"}
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return [1, 2]
    if ann is bool:
        return "yes"
    if ann in (int, float):
        return "12"
    return 12345


def _dto_name(model: type) -> str:
    return model.__name__.replace("Response", "DTO")


def _raw(s: str) -> str:
    assert '"##' not in s and "\n" not in s
    return '##"' + s + '"##'


_HARNESS_HEAD = r'''
let __enc: JSONEncoder = { let e = JSONEncoder(); e.outputFormatting = [.sortedKeys]; return e }()
func __rt<T: Codable>(_ t: T.Type, _ tag: String, _ json: String) {
    do {
        let v = try JSONDecoder().decode(T.self, from: Data(json.utf8))
        print("RT|\(tag)|" + String(decoding: try __enc.encode(v), as: UTF8.self))
    } catch { print("RT|\(tag)|THROW \(type(of: error))") }
}
func __out(_ kind: String, _ tag: String, _ value: String?) { print("\(kind)|\(tag)|\(value ?? "<nil>")") }
func __group(_ json: String) -> TrillionClubGroup {
    TrillionClubGroup(dto: try? JSONDecoder().decode(TrillionClubGroupDTO.self, from: Data(json.utf8)))
}
func __detail(_ json: String) -> TrillionClubDetail? {
    guard let dto = try? JSONDecoder().decode(TrillionClubDetailDTO.self, from: Data(json.utf8)) else { return nil }
    return TrillionClubDetail(dto: dto)
}
print("TZ|" + TimeZone.current.identifier + "|" + String(TimeZone.current.secondsFromGMT()))
'''

_SRC = {"as_of": "2026-01-01", "source_title": "S", "source_url": "https://x.example/a", "verified_on": "2026-09-01"}


def _unknown_variants(known: str) -> List[str]:
    return [known.upper(), " " + known, known + " ", known + "\n", known.replace("_", "-") + "x",
            "", "unknown", "brand_new_value"]


def _enum_cases() -> List[Tuple[str, str, str, str]]:
    """(tag, swift expression, json, expected output). A known value is included per field as a
    control, so a mapper that drops EVERYTHING cannot pass."""
    cases = []

    def add(field, value, expr, payload, expect):
        cases.append((f"{field}:{value!r}", expr, json.dumps(payload), expect))

    for v in ["thirteen_f"] + _unknown_variants("thirteen_f"):
        add("card_kind", v, "__group({j}).companies.map(\\.slug).joined(separator: \",\")",
            {"companies": [{"slug": "x", "name": "X", "card_kind": v}, {"slug": "y", "name": "Y", "card_kind": "non_us"}]},
            "x,y" if v == "thirteen_f" else "y")
    for v in ["commitment"] + _unknown_variants("on_13f_note"):
        add("stake.kind", v, "(__group({j}).companies.first?.stakes.map(\\.investeeName).joined(separator: \",\"))",
            {"companies": [{"slug": "a", "name": "A", "card_kind": "no_thirteen_f", "stakes": [
                {"investee_name": "X", "kind": v, **_SRC}, {"investee_name": "Y", "kind": "private", **_SRC}]}]},
            "X,Y" if v == "commitment" else "Y")
    for v in ["invested"] + _unknown_variants("carrying_value"):
        add("value_basis", v, "__group({j}).companies.first?.stakes.first?.figureText",
            {"companies": [{"slug": "a", "name": "A", "card_kind": "no_thirteen_f", "stakes": [
                {"investee_name": "X", "kind": "private", "ownership_pct": 27, "ownership_basis": "as-converted",
                 "disclosed_value": 5e9, "value_basis": v, **_SRC}]}]},
            "27% as-converted · $5B invested" if v == "invested" else "27% as-converted")
    for v in ["increased"] + _unknown_variants("newly_reported"):
        add("holding.change", v,
            "__group({j}).companies.first?.topHoldings.first.map { \"\\($0.name)/\\($0.change?.pillLabel ?? \"-\")\" }",
            {"companies": [{"slug": "a", "name": "A", "card_kind": "thirteen_f", "top_holdings": [
                {"name": "Intel", "symbol": "INTC", "weight": 0.5, "change": v}]}]},
            "Intel/Increased shares" if v == "increased" else "Intel/-")
    for v in ["increased"] + _unknown_variants("no_longer_reported"):
        add("change.change", v, "__detail({j})?.changes.map(\\.name).joined(separator: \",\")",
            {"company": {"slug": "a", "name": "A", "card_kind": "thirteen_f"},
             "changes": [{"name": "A1", "change": v, "shares": 2, "prev_shares": 1, "share_change": 1},
                         {"name": "B1", "change": "decreased", "shares": 1, "prev_shares": 2, "share_change": -1}]},
            "A1,B1" if v == "increased" else "B1")
    for v in ["quarter"] + _unknown_variants("first_filing"):
        add("comparison", v, "__group({j}).companies.first?.changeLine",
            {"companies": [{"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2",
                            "prev_period": "2026-Q1", "comparison": v, "change_counts": {"increased": 1}}]},
            "vs Q1: 1 with more shares" if v == "quarter" else "<nil>")
    for v in ["no_newer_filing"] + _unknown_variants("latest_not_in"):
        add("notice", v, "__group({j}).companies.first?.noticeText",
            {"companies": [{"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2", "notice": v}]},
            "No newer 13F found — the latest on file is Q2 2026." if v == "no_newer_filing" else "<nil>")
    return cases


_DATE_CASES = [
    ("2026-06-30", "Jun 30, 2026"), ("2026-01-01", "Jan 1, 2026"), ("2026-12-31", "Dec 31, 2026"),
    ("2024-02-29", "Feb 29, 2024"), ("2026-06-30T23:59:59Z", "Jun 30, 2026"),
    ("2026-07-01T00:00:00Z", "Jul 1, 2026"), (" 2026-03-31 ", "Mar 31, 2026"),
]


def _seed_logo_cases() -> List[Tuple[str, str, Optional[str]]]:
    """(slug, display name, logo_symbol as the SERVICE emits it) for every published company."""
    from app.services import trillion_club_service as svc

    out = []
    for c in _seed()["companies"]:
        if not c["published"]:
            continue
        parsed = svc._parse_company(dict(c, is_member=True))
        assert parsed is not None, c["slug"]
        out.append((c["slug"], c["display_name"], parsed.logo_symbol))
    return out


def _harness_program(models_src: str) -> str:
    lines = [models_src, _HARNESS_HEAD]
    for model in _response_models():
        dto = _dto_name(model)
        fields = model.model_fields
        full = json.dumps(_sentinel(model).model_dump(), allow_nan=False)
        nulls = json.dumps({k: None for k in fields})
        wrong = json.dumps({k: _wrong_type(f.annotation) for k, f in fields.items()})
        for kind, payload in (("full", full), ("null", nulls), ("wrong", wrong)):
            lines.append(f'__rt({dto}.self, "{kind}.{dto}", {_raw(payload)})')
    for tag, expr, payload, _ in _enum_cases():
        lines.append(f'__out("E", {json.dumps(tag)}, {expr.replace("{j}", _raw(payload))})')
    for iso, _ in _DATE_CASES:
        lines.append(f'__out("D", {json.dumps(iso)}, ClubDate(iso: {json.dumps(iso)})?.long)')
    company = json.dumps({"companies": [{"slug": "a", "name": "A", "card_kind": "thirteen_f",
                                         "period_end": "2026-06-30", "filed_on": "2026-08-14",
                                         "market_cap": 1.5e12, "market_cap_as_of": "2026-01-01",
                                         "stakes": [{"investee_name": "X", "kind": "private", **_SRC}]}]})
    lines.append(f'__out("D", "company.filing", __group({_raw(company)}).companies.first?.filingDatesLine)')
    lines.append(f'__out("D", "company.cap", __group({_raw(company)}).companies.first?.marketValueLine)')
    lines.append(f'__out("D", "stake.source", __group({_raw(company)}).companies.first?.stakes.first?.sourceText)')
    for slug, name, logo in _seed_logo_cases():
        payload = json.dumps({"companies": [{"slug": slug, "name": name, "card_kind": "non_us", "logo_symbol": logo}]})
        lines.append(f'__out("L", {json.dumps(slug)}, __group({_raw(payload)}).companies.first?.logoSymbol)')
    for pct in (99.94, 99.95, 99.97, 99.999, 100.0, 50.04):
        lines.append(f'__out("F", "own.{pct}", TrillionClubFormat.ownership({pct}))')
    lines.append('for c in [ClubChip.privateCompany, .nonUSListed, .tiedToDeal, .commitment, .clubMember, '
                 '.listedSince(ClubDate(iso: "2026-06-12")!)] { __out("A", c.label, c.accessibilityText) }')
    lines.append('if let d = TrillionClubSamples.nvidiaDetailLocked { '
                 'for p in d.holdings + d.changes { __out("P", p.name, p.accessibilityText) }; '
                 'for s in d.stakes { __out("S", s.investeeName, s.accessibilityText(onThirteenFCard: true)) } }')
    lines.append('print("DONE|")')
    return "\n".join(lines)


def _run_swift(program: str, tz: str) -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    env = dict(os.environ, TZ=tz)
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=program, text=True, capture_output=True,
                              timeout=300, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(f"Swift harness did not complete\nstdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-3000:]}")
    return proc.stdout


def _lines(out: str, kind: str) -> Dict[str, str]:
    got = {}
    for line in out.splitlines():
        if line.startswith(kind + "|"):
            _, tag, value = line.split("|", 2)
            got[tag] = value
    return got


_TZ_EAST, _TZ_WEST = "Pacific/Kiritimati", "Pacific/Pago_Pago"   # UTC+14 and UTC-11


@pytest.fixture(scope="module")
def swift_runs() -> Dict[str, str]:
    program = _harness_program(_src(MODELS))
    return {_TZ_EAST: _run_swift(program, _TZ_EAST), _TZ_WEST: _run_swift(program, _TZ_WEST)}


def _json_equal(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b and type(a) is type(b) if isinstance(a, int) and isinstance(b, int) else float(a) == float(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    return a == b


def roundtrip_problems(out: str) -> List[str]:
    got = _lines(out, "RT")
    problems = []
    for model in _response_models():
        dto = _dto_name(model)
        full = got.get(f"full.{dto}")
        expect = _sentinel(model).model_dump()
        if full is None or full.startswith("THROW"):
            problems.append(f"{dto}: full payload {full or 'missing'}")
        elif not _json_equal(json.loads(full), expect):
            back = json.loads(full)
            lost = sorted(k for k in expect if k not in back or not _json_equal(back[k], expect[k]))
            problems.append(f"{dto}: fields lost or changed on the way through Swift: {lost}")
        for kind in ("null", "wrong"):
            value = got.get(f"{kind}.{dto}")
            if value != "{}":
                problems.append(f"{dto}: {kind}-typed payload decoded to {value!r}, want every field nil")
    return problems


def missing_dtos(models_src: str) -> List[str]:
    code = _code(models_src)
    return [m.__name__ for m in _response_models() if not re.search(rf"\bstruct\s+{_dto_name(m)}\b", code)]


def test_every_response_model_has_a_dto():
    assert len(_response_models()) >= 9
    assert missing_dtos(_src(MODELS)) == []
    assert missing_dtos(_src(MODELS).replace("struct ClubHistoryPointDTO", "struct ClubHistoryDTO")) == [
        "ClubHistoryPointResponse"]


def test_every_pydantic_field_round_trips_through_the_swift_dtos(swift_runs):
    """For all 9 trillion_club response models: every field, set to a typed sentinel, decodes
    with the right key AND type (2^53+1 survives only as Int, 0.1234567890123 only as Double) and
    re-encodes unchanged; a payload of all-nulls or all-wrong-types decodes (optional) to nil."""
    for tz, out in swift_runs.items():
        assert roundtrip_problems(out) == [], tz


def test_roundtrip_checker_fires_on_a_mutated_dto(monkeypatch):
    """Non-vacuity: a renamed CodingKey loses its field, and a strict (throwing) decode makes the
    null payload throw — both are reported."""
    src = _src(MODELS)
    anchor_key = 'case sourceUrl = "source_url"'
    anchor_strict = "        isStale = ClubDecode.field(c, .isStale)\n"
    assert src.count(anchor_key) == 1 and src.count(anchor_strict) == 1
    mutated = src.replace(anchor_key, 'case sourceUrl = "sourceUrl"').replace(
        anchor_strict, "        isStale = try c.decode(Bool.self, forKey: .isStale)\n")
    problems = roundtrip_problems(_run_swift(_harness_program(mutated), "UTC"))
    assert any("ClubStakeDTO: fields lost" in p and "source_url" in p for p in problems), problems
    assert any("ClubStakeDTO: null-typed" in p for p in problems), problems


def enum_problems(out: str) -> List[str]:
    got = _lines(out, "E")
    return [f"{tag}: {got.get(tag)!r} != {expect!r}" for tag, _, _, expect in _enum_cases()
            if got.get(tag) != expect]


@pytest.mark.parametrize("case", _enum_cases(), ids=[c[0] for c in _enum_cases()])
def test_unknown_enum_strings_degrade_to_hidden(swift_runs, case):
    """Unknown, padded, upper-cased or hyphenated enum strings are never guessed at: the card /
    stake / change row is dropped, the figure loses only its value, the pill / line / notice is
    nil — and the sibling beside it survives. A known value (control) maps normally."""
    tag, _, _, expect = case
    for tz, out in swift_runs.items():
        assert _lines(out, "E").get(tag) == expect, f"{tz}: {tag}"


def date_problems(east: str, west: str) -> List[str]:
    out = []
    tz_e, tz_w = _lines(east, "TZ"), _lines(west, "TZ")
    if _TZ_EAST not in tz_e or _TZ_WEST not in tz_w or tz_e[_TZ_EAST] == tz_w[_TZ_WEST]:
        out.append("anti-vacuity: the TZ did not reach the Swift process")
    d_e, d_w = _lines(east, "D"), _lines(west, "D")
    out += [f"{k}: {d_e.get(k)!r} (UTC+14) vs {d_w.get(k)!r} (UTC-11)" for k in sorted(set(d_e) | set(d_w))
            if d_e.get(k) != d_w.get(k)]
    want = dict(_DATE_CASES, **{"company.filing": "Holdings on Jun 30, 2026 · filed Aug 14, 2026",
                                "company.cap": "Market value $1.5T as of Jan 1, 2026",
                                "stake.source": "per S, Jan 1, 2026"})
    out += [f"{k}: {d_e.get(k)!r} != {v!r}" for k, v in want.items() if d_e.get(k) != v]
    return out


def test_dates_do_not_move_with_the_device_time_zone(swift_runs):
    """Run in UTC+14 and UTC-11: every printed date is identical and is the wire's calendar date
    (a UTC-midnight Date formatted in the device zone would print Dec 31 for "2026-01-01" west
    of Greenwich)."""
    assert date_problems(swift_runs[_TZ_EAST], swift_runs[_TZ_WEST]) == []


def a11y_problems(out: str) -> List[str]:
    problems = []
    chips = _lines(out, "A")
    if len(chips) != 6:
        problems.append(f"{len(chips)} chip sentences, want 6")
    for label, sentence in chips.items():
        if not sentence.endswith(".") or not sentence.startswith(label.split(" ")[0]) or len(sentence) <= len(label):
            problems.append(f"chip {label!r}: {sentence!r} is not a sentence about it")
    for kind in ("P", "S"):
        rows = _lines(out, kind)
        if not rows:
            problems.append(f"anti-vacuity: no {kind} rows")
        problems += [f"{kind} {name!r}: {text!r}" for name, text in rows.items()
                     if not text.startswith(name) or len(text) <= len(name) + 5]
    return problems


_UTC_MIDNIGHT_LONG = """    var long: String {
        var parts = DateComponents(); parts.year = year; parts.month = month; parts.day = day
        var utc = Foundation.Calendar(identifier: .gregorian); utc.timeZone = Foundation.TimeZone(identifier: "UTC")!
        let f = DateFormatter(); f.dateFormat = "MMM d, yyyy"; f.locale = Locale(identifier: "en_US_POSIX")
        return f.string(from: utc.date(from: parts)!)
    }"""


def test_swift_checkers_fire_on_a_mutated_models_file():
    """Non-vacuity of the executed enum, date and VoiceOver checks, on an in-memory mutant: a card
    kind that is trimmed + lower-cased before matching (guessing), a date printed through a
    DateFormatter in the device zone, and a chip sentence cut to its bare label."""
    src = _src(MODELS)
    kind_anchor = "nonisolated enum ClubCardKind: String, CaseIterable, Sendable {"
    long_anchor = '    var long: String { "\\(Self.months[month - 1]) \\(day), \\(year)" }'
    chip_anchor = 'case .clubMember: return "Club member: this company is itself valued at $1 trillion or more."'
    for anchor in (kind_anchor, long_anchor, chip_anchor):
        assert src.count(anchor) == 1, anchor
    at = src.index(kind_anchor)
    wire = "    init(wire: String?) { self = wire.flatMap { Self(rawValue: $0) } ?? .unknown }"
    hit = src.index(wire, at)
    mutated = (src[:hit] + "    init(wire: String?) { self = wire.flatMap { Self(rawValue: "
               "$0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()) } ?? .unknown }"
               + src[hit + len(wire):])
    mutated = mutated.replace(long_anchor, _UTC_MIDNIGHT_LONG).replace(
        chip_anchor, 'case .clubMember: return "Club member"')
    program = _harness_program(mutated)
    east, west = _run_swift(program, _TZ_EAST), _run_swift(program, _TZ_WEST)
    assert any(p.startswith("card_kind:' thirteen_f'") for p in enum_problems(east))
    assert any(p.startswith("card_kind:'THIRTEEN_F'") for p in enum_problems(east))
    assert any("UTC-11" in p for p in date_problems(east, west))
    assert any("Club member" in p for p in a11y_problems(east))


_TZ_APIS = re.compile(r"\bDateFormatter\b|\bISO8601DateFormatter\b|\bCalendar\b|\bTimeZone\b|\bDate\(\)|"
                      r"\bDate\.now\b|\.formatted\(|\bLocale\b")


@pytest.mark.parametrize("path", CLUB_SWIFT_FILES, ids=lambda p: p.name)
def test_no_device_clock_or_zone_api_in_club_code(path):
    """The only date path is `ClubDate` (calendar components, printed verbatim). A DateFormatter,
    Calendar or `Date()` anywhere in the section would re-introduce the device's zone."""
    code = _code(_src(path))
    assert _TZ_APIS.findall(code) == []
    assert _TZ_APIS.findall(_code("let f = DateFormatter()\n// Calendar.current\n")) == ["DateFormatter"]


def _logo_fallback_glyph(name: str, logo_symbol: Optional[str], card_code: str, atom_code: str) -> str:
    """What the logo tile shows when no image is on screen (loading, or no logo on the CDN),
    derived from the CURRENT view code rather than assumed."""
    uses_atom = re.search(r"if\s+let\s+symbol\s*=\s*company\.logoSymbol\s*\{\s*CompanyLogoView\(ticker:\s*symbol",
                          card_code) is not None
    initials = type_body(atom_code, "CompanyLogoView")
    atom_uses_ticker = re.search(r"Text\(String\(ticker\.prefix\(1\)\)\)", initials) is not None
    if logo_symbol is not None and uses_atom:
        return logo_symbol[:1] if atom_uses_ticker else name[:1].upper()
    return name[:1].upper()


def test_regression_non_us_logo_symbol_never_falls_back_to_a_digit_tile(swift_runs):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (iOS card + detail header, fed by the seed): TrillionClubCard's own header says
    "The logo is CompanyLogoView only for a real U.S. ticker; anything else gets a letter tile".
    But `TrillionClubCompany.init` keeps ANY `ClubSanitize.symbol` (dots and leading digits are
    allowed), the seed ships logo_symbol '2222.SR' (Saudi Aramco) and '005930.KS' (Samsung), the
    service passes them through, and both views hand them to `CompanyLogoView(ticker:)` — whose
    fallback is `ticker.prefix(1)`. So while the image loads, and for good if the CDN has no
    such PNG, Aramco's card shows a "2" tile and Samsung's a "0". Fix: in
    `TrillionClubCompany.init`, keep logoSymbol only for a U.S. ticker (e.g.
    `^[A-Z]{1,5}(-[A-Z])?$`) — or null logo_symbol for non-U.S. rows in the seed and add that
    rule to `company_problems`."""
    swift_logo = _lines(swift_runs[_TZ_EAST], "L")
    card_code, atom_code = _code(_src(CARD)), _code(_src(LOGO_ATOM))
    detail_code = _code(_src(DETAIL))
    assert "CompanyLogoView(ticker: symbol" in detail_code, "detail header drifted — re-derive"
    bad = []
    for slug, name, _ in _seed_logo_cases():
        logo = swift_logo.get(slug)
        logo = None if logo in (None, "<nil>") else logo
        glyph = _logo_fallback_glyph(name, logo, card_code, atom_code)
        if not glyph.isalpha():
            bad.append(f"{slug}: logo_symbol {logo!r} → fallback tile {glyph!r} (name {name!r})")
    assert bad == [], "\n".join(bad)


@pytest.mark.parametrize("pct", ["99.95", "99.97", "99.999"])
def test_regression_ownership_never_rounds_a_stake_short_of_100_up_to_100_percent(swift_runs, pct):
    """REGRESSION (fixed 2026-09-24). Was: DEFECT (iOS `TrillionClubFormat.ownership`): 99.97 → `whole` = 100, |99.97-100| < 0.05 →
    "100%" — a stake the source states as short of full ownership prints as wholly owned
    (99.95 too: as a Double it is 99.9500000000000028, 0.04999… from 100). `weight()` guards exactly this with ">99%";
    ownership does not. Fix: below 100, never print 100 — cap at "99.9%" (or ">99.9%")."""
    got = _lines(swift_runs[_TZ_EAST], "F")[f"own.{pct}"]
    assert not got.startswith("100"), f"{pct}% printed as {got}"


def test_ownership_control_values(swift_runs):
    f = _lines(swift_runs[_TZ_EAST], "F")
    assert f["own.100.0"] == "100%" and f["own.99.94"] == "99.9%" and f["own.50.04"] == "50%"


def test_every_chip_and_row_has_a_voiceover_sentence(swift_runs):
    """Executed: every ClubChip case (including listedSince) has a full sentence that names the
    chip and ends in a period; every sample holding, change and stake has a label that starts
    with its own name and says more than the name."""
    assert a11y_problems(swift_runs[_TZ_EAST]) == []


# ══════════════════════════════════════════════════════════════════════════════════════
# E. Swift — source scans (views + view model)
# ══════════════════════════════════════════════════════════════════════════════════════

# ALLOW-list: the neutral tokens this section may ink with. The existing guard is a DENY-list of
# gain/loss names; this also catches caution / alertOrange / accentCyan / a raw system colour.
_NEUTRAL_TOKENS = {"textPrimary", "textSecondary", "textMuted", "primaryBlue", "primaryFill", "divider",
                   "background", "cardBackground", "cardBackgroundNested", "cardBackgroundLight",
                   "cardEdge", "textOnAccent", "mediaSurface"}
_SYSTEM_COLOUR = re.compile(r"(?<![\w])(?:Color|UIColor)?\.(?:green|red|orange|yellow|mint|pink|teal|"
                            r"systemGreen|systemRed|systemOrange|systemYellow)\b|Color\(\s*(?:red|hex|lightHex|\.system)")


def colour_violations(src: str) -> List[str]:
    code = _code(src)
    bad = [f"AppColors.{t}" for t in re.findall(r"AppColors\.(\w+)", code) if t not in _NEUTRAL_TOKENS]
    return bad + _SYSTEM_COLOUR.findall(code)


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_club_views_ink_only_with_neutral_tokens(path):
    src = _src(path)
    assert re.search(r"AppColors\.\w+", _code(src)), f"anti-vacuity: no colour in {path.name}"
    assert colour_violations(src) == []


@pytest.mark.parametrize("token", ["AppColors.caution", "AppColors.alertOrange", "AppColors.accentCyan",
                                   "AppColors.gainGraphic", "Color.green", "Color(red: 0, green: 1, blue: 0)"])
def test_colour_allow_list_fires(token):
    src = _src(ROW)
    anchor = "let ink = change == .newlyReported ? AppColors.primaryBlue : AppColors.textSecondary"
    assert anchor in src
    assert colour_violations(src.replace(anchor, f"let ink = change == .newlyReported ? {token} : AppColors.textSecondary"))


def _chain_after(code: str, end: int) -> str:
    """The `.modifier(...)` chain that follows position `end` (balanced parentheses)."""
    i, n, out = end, len(code), []
    while True:
        j = i
        while j < n and code[j] in " \t\r\n":
            j += 1
        m = re.match(r"\.\w+", code[j:])
        if not m:
            return "".join(out)
        k = j + m.end()
        if k < n and code[k] == "(":
            depth, k = 1, k + 1
            while k < n and depth:
                depth += {"(": 1, ")": -1}.get(code[k], 0)
                k += 1
        out.append(code[j:k])
        i = k


def unlabelled_icons(src: str) -> List[str]:
    """Every `Image(systemName:)` must be hidden from VoiceOver or be the label of a Button that
    carries an accessibilityLabel."""
    code = _code(src)
    bad = []
    for m in re.finditer(r"Image\(systemName:", code):
        close = m.end()
        depth = 1
        while depth:
            depth += {"(": 1, ")": -1}.get(code[close], 0)
            close += 1
        if ".accessibilityHidden(true)" in _chain_after(code, close):
            continue
        labelled = False
        for lm in re.finditer(r"\blabel:\s*\{", code[:m.start()]):
            open_at = lm.end()
            end = match_brace(code, open_at)
            if end > m.start() and ".accessibilityLabel(" in _chain_after(code, end + 1):
                labelled = True
        if not labelled:
            line = code.count("\n", 0, m.start()) + 1
            bad.append(f"line {line}: {code[m.start():close]}")
    return bad


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_every_icon_is_hidden_or_labelled(path):
    assert unlabelled_icons(_src(path)) == []


def _drop_hide_after(src: str, image: str) -> str:
    """`src` with the first `.accessibilityHidden(true)` after `image` removed (in memory)."""
    at = src.index(image)
    hide = src.index(".accessibilityHidden(true)", at)
    return src[:hide] + src[hide + len(".accessibilityHidden(true)"):]


def test_icon_guard_fires():
    src = _src(DETAIL)
    # The header's notice clock sits in no Button: un-hiding it must be reported.
    assert unlabelled_icons(_drop_hide_after(src, 'Image(systemName: "clock")'))
    # The locked row's lock glyph IS inside a labelled Button, so un-hiding it is fine.
    assert unlabelled_icons(_drop_hide_after(src, 'Image(systemName: "lock.fill")')) == []
    assert unlabelled_icons(_drop_hide_after(_src(CARD), 'Image(systemName: "chevron.right")'))
    section = _src(SECTION)
    label = '.accessibilityLabel("About \\(TrillionClubCopy.title)")'
    assert label in section
    assert unlabelled_icons(section.replace(label, "", 1))


def _prop_body(code: str, decl: str) -> str:
    at = code.find(decl)
    assert at != -1, f"{decl!r} not found"
    brace = code.index("{", at + len(decl) - 1)
    return code[brace:match_brace(code, brace + 1) + 1]


def row_label_violations(row_src: str, card_src: str, detail_src: str) -> List[str]:
    out = []
    row = _code(row_src)
    for prop in ("private var compactRow: some View", "private var fullRow: some View"):
        body = _prop_body(row, prop)
        if ".accessibilityElement(children: .ignore)" not in body or ".accessibilityLabel(position.accessibilityText)" not in body:
            out.append(f"ClubHoldingRow.{prop.split()[2]} has no single VoiceOver label")
    card = _code(card_src)
    main_button = _prop_body(card, "var body: some View")
    if not re.search(r"Button\(action:\s*onTap\)\s*\{.*?\}\s*\.buttonStyle\(\.plain\)\s*\.accessibilityLabel\(accessibilityLabel\)",
                     main_button, re.S):
        out.append("TrillionClubCard's main button lost its combined label")
    label = _prop_body(card, "private var accessibilityLabel: String")
    if "accessibilityText(onThirteenFCard:" not in label or "company.accessibilityText" not in label:
        out.append("TrillionClubCard's label no longer reads the stakes / company")
    detail = _code(detail_src)
    if ".accessibilityLabel(position.accessibilityText)" not in _prop_body(detail, "private func positionRow("):
        out.append("detail position button unlabelled")
    if ".accessibilityLabel(" not in _prop_body(detail, "private func lockedHoldingsRow("):
        out.append("locked-holdings row unlabelled")
    if ".accessibilityElement(children: .combine)" not in _prop_body(detail, "private func historySection("):
        out.append("history rows not combined")
    return out


def test_every_row_has_one_voiceover_label():
    assert row_label_violations(_src(ROW), _src(CARD), _src(DETAIL)) == []


def test_row_label_guard_fires():
    row = _src(ROW)
    anchor = "        .accessibilityElement(children: .ignore)\n        .accessibilityLabel(position.accessibilityText)\n    }\n\n    // MARK: - Full"
    assert anchor in row
    mutated = row.replace(anchor, "    }\n\n    // MARK: - Full")
    assert row_label_violations(mutated, _src(CARD), _src(DETAIL))


def vm_reload_violations(src: str) -> List[str]:
    """A reload (purchase landed) may overlap the first load: the stale answer must be dropped on
    BOTH paths, a failed reload must keep what is on screen, and cancellation reports nothing."""
    code = _code(src)
    body = _prop_body(code, "func load() async")
    out = []
    await_at = body.find("await apiClient.request(")
    bump_at = body.find("loadGeneration &+= 1")
    if await_at == -1 or bump_at == -1 or bump_at > await_at:
        out.append("generation not bumped before the request")
    guard = "guard generation == loadGeneration else { return }"
    do_part = body[await_at:body.find("} catch")]
    if guard not in do_part or do_part.find(guard) > do_part.find("detail = mapped"):
        out.append("success path assigns before checking the generation")
    generic_catch = body[body.rfind("} catch {"):]
    if guard not in generic_catch or generic_catch.find(guard) > generic_catch.find("errorMessage ="):
        out.append("failure path reports before checking the generation")
    cancel = re.search(r"catch is CancellationError \{(.*?)\}", body, re.S)
    if not cancel or "errorMessage" in cancel.group(1):
        out.append("a cancelled load reports an error")
    if re.search(r"\bdetail\s*=\s*nil\b", code):
        out.append("a failed reload clears the detail already on screen")
    return out


def test_detail_view_model_drops_stale_reloads_and_keeps_what_works():
    assert vm_reload_violations(_src(DETAIL_VM)) == []


@pytest.mark.parametrize("old,new", [
    ("            guard generation == loadGeneration else { return }\n            guard let mapped", "            guard let mapped"),
    ("            if detail == nil {\n                errorMessage = appError.message\n            }",
     "            detail = nil\n            errorMessage = appError.message"),
    ("        loadGeneration &+= 1\n", ""),
])
def test_vm_reload_guard_fires(old, new):
    src = _src(DETAIL_VM)
    assert old in src
    assert vm_reload_violations(src.replace(old, new, 1))


def ticker_route_violations(detail_src: str, home_card_srcs: Sequence[str]) -> List[str]:
    """The detail presents its OWN ticker cover: every ticker tap is a Button that calls
    `openTicker`, only `openTicker` sets `selectedTicker`, and nothing navigates through Home's
    stack. The Home card itself never presents a ticker (its row is not a Button)."""
    code = _code(detail_src)
    body = type_body(code, "TrillionClubDetailView")
    out = []
    sets = [m.start() for m in re.finditer(r"\bselectedTicker\s*=", body)]
    open_fn = _prop_body(body, "private func openTicker(")
    if len(sets) != 1 or "selectedTicker =" not in open_fn:
        out.append(f"selectedTicker assigned {len(sets)} time(s), not once inside openTicker")
    calls = [m.start() for m in re.finditer(r"\bopenTicker\(", body)
             if not body[max(0, m.start() - 13):m.start()].endswith("private func ")]
    if not calls:
        out.append("anti-vacuity: no openTicker call")
    for at in calls:
        inside = False
        for bm in re.finditer(r"\bButton\s*\{", body[:at]):
            end = match_brace(body, bm.end())
            if end > at:
                inside = True
        if not inside:
            out.append(f"openTicker called outside a Button action at offset {at}")
    for banned in ("NavigationLink(", ".navigationDestination(", "selectedTab", "appState.navigat"):
        if banned in body:
            out.append(f"detail routes through {banned}")
    cover = _prop_body(body, ".fullScreenCover(item: $selectedTicker)")
    if "TickerDetailView(tickerSymbol: ticker.symbol)" not in cover:
        out.append("the ticker cover does not present TickerDetailView for the tapped symbol")
    for src in home_card_srcs:
        c = _code(src)
        for banned in ("TickerDetailView", "selectedTicker", "MarketTicker("):
            if banned in c:
                out.append(f"a Home club view presents a ticker ({banned})")
    return out


def test_detail_presents_its_own_ticker_cover_from_buttons_only():
    assert ticker_route_violations(_src(DETAIL), [_src(CARD), _src(SECTION), _src(ROW)]) == []


def test_ticker_route_guard_fires():
    src = _src(DETAIL)
    anchor = "    private func openTicker(_ symbol: String, name: String) {"
    assert anchor in src
    stray = src.replace("            .task { await viewModel.load() }",
                        "            .task { await viewModel.load() }\n            .onAppear { selectedTicker = nil }", 1)
    assert ticker_route_violations(stray, [])
    nav = src.replace("        .buttonStyle(.plain)\n        .accessibilityHint(\"Opens \\(name)'s investor profile\")",
                      "        .buttonStyle(.plain)\n        .accessibilityHint(\"Opens \\(name)'s investor profile\")\n"
                      "        NavigationLink(\"x\") { EmptyView() }", 1)
    assert nav != src and ticker_route_violations(nav, [])
    card = _src(CARD).replace("struct TrillionClubCard: View {", "struct TrillionClubCard: View {\n    @State private var selectedTicker: MarketTicker?", 1)
    assert ticker_route_violations(src, [card])
