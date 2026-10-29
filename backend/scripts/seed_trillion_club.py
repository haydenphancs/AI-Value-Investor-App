#!/usr/bin/env python3
"""
Trillion-Dollar Club seed
=========================

⚠️  THIS SCRIPT WRITES TO PRODUCTION. ``backend/.env`` points at the production Supabase
project and this script uses the service-role client (``app.database.get_supabase``). Only
the owner runs it with ``--apply``, after reviewing the JSON and a dry run. Never run it
with ``--apply`` from an agent, a test or CI.

Loads ``backend/data/trillion_club_seed.json`` (hand-kept, every stake re-verified against
its primary source) into the migration-175 tables:

- ``trillion_club_companies`` — upserted on ``slug``;
- ``trillion_club_stakes``    — upserted on ``(company_slug, investee_name, kind)``.

Behaviour, mirroring ``sync_whale_registry.py``:

- **Validates first.** Every row is checked against migration 175's CHECK constraints plus
  the copy rules (banned wording, no forecasts, 90-character past-tense background) and the
  seed policies below. A seed with any problem is never written.
- **Dry run by default.** Reads the two tables and prints what would be inserted and every
  column that has drifted from the JSON (e.g. an edit made in Studio). Writes nothing.
- **``--apply``** inserts only the rows that are missing. Existing rows are left alone.
- **``--apply --update``** also overwrites drifted columns with the JSON's values. The JSON
  wins, so review the dry run first: a deliberate Studio edit is reverted by ``--update``.
- **Additive, never deletes.** A row in the database that is not in the JSON is reported
  as an orphan and left for a human to remove on purpose.
- The job-written membership columns (``is_member``, ``member_since``, ``last_market_cap``,
  ``last_cap_date``, the streak counters, ``membership_checked_at``) are never seeded and
  never touched.
- **Rows the stake finder made** (migration 205's ``origin`` 'auto' / 'proposal', found in
  SEC filings) are never orphans and never drift: the dry run lists them as "found
  automatically". A JSON stake with the SAME natural key adopts that row under
  ``--apply --update``, even when nothing drifted: it becomes a seed row (``origin`` 'seed',
  its finding, Keep and withdrawal cleared). A JSON stake that names the same investee as a
  row found automatically under a DIFFERENT key is a WARNING (the card would show it twice).
  Before 205 is applied the column does not exist: the read falls back to the 175 columns
  (one WARNING) and every row is a seed row.

The stake validator (``stake_problems`` and the helpers it shares with ``company_problems``)
lives in ``app/services/trillion_club/stake_rules.py``, shared with the stake finder; this
script re-exports every one of those names unchanged.

Usage:
    cd backend
    python -m scripts.seed_trillion_club --validate-only    # JSON only; no database at all
    python -m scripts.seed_trillion_club                    # dry run (reads PRODUCTION)
    python -m scripts.seed_trillion_club --apply            # insert missing rows (PRODUCTION)
    python -m scripts.seed_trillion_club --apply --update   # + overwrite drift (PRODUCTION)

Exit codes: 0 clean · 1 a database read/write failed · 2 the seed failed validation.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.schemas.trillion_club import CARD_KINDS  # noqa: E402
# ONE copy of the copy rules, shared with the request path (trillion_club_service), so a
# Studio edit this script would refuse can never be served. Re-exported for the tests.
from app.services.trillion_club.copy_rules import (  # noqa: E402,F401
    BANNED_COPY,
    FORECAST_COPY,
    STAKE_TEXT_FIELDS,
    contains_banned_copy,
    contains_forecast,
)
from app.services.trillion_club.stake_contracts import (  # noqa: E402
    MACHINE_ORIGINS,
    ORIGIN_SEED,
    ORIGINS,
)
from app.services.trillion_club.stake_names import IdentityIndex  # noqa: E402
# ONE copy of the stake validator, shared with the stake finder (its gate 5), plus the helpers
# company_problems shares with it. Every name below used to be defined here; each is
# re-exported as the SAME object (tests/test_trillion_club_seed_integrity.py pins it). Keep
# this `from <module> import` form: the mutation tests swap the module in sys.modules.
from app.services.trillion_club.stake_rules import (  # noqa: E402,F401
    BACKGROUND_MAX,
    INT4_MAX,
    INVESTEE_NAME_MAX,
    SOURCE_CONFIDENCES,
    SOURCE_TITLE_MAX,
    STAKE_COLUMNS,
    STAKE_KEY,
    _CUSIP_RE,
    _DATE_RE,
    _DISPLAY_TEXT_COLUMNS,
    _INVISIBLE_CATEGORIES,
    _SYMBOL_RE,
    _UNSTORABLE_CHAR,
    _column_problems,
    _finite_number,
    _fold_name,
    _fold_symbol,
    _folded_stake_key,
    _invisible_char_columns,
    _is_bool,
    _is_https,
    _is_int,
    _iso_date,
    _text_ok,
    _unstorable_columns,
    stake_key,
    stake_problems,
)

logger = logging.getLogger("seed_trillion_club")

SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trillion_club_seed.json")

COMPANIES_TABLE = "trillion_club_companies"
STAKES_TABLE = "trillion_club_stakes"

# ── The contract: exactly the hand-kept columns of migration 175 ────────────────────────
# tests/test_trillion_club_seed_integrity.py parses the migration and pins these against it.
# The stake columns (STAKE_COLUMNS, STAKE_KEY) are imported above from stake_rules.
COMPANY_COLUMNS: Tuple[str, ...] = (
    "slug", "display_name", "ciks", "card_kind", "use_13f", "cap_symbol", "symbol_aliases",
    "detail_symbol", "logo_symbol", "home_country", "cap_source", "manual_cap_usd",
    "manual_cap_as_of", "manual_cap_source_url", "manual_fx_rate", "manual_fx_source",
    "membership_mode", "link_whale", "published", "reviewed_on",
)
# Written by the daily membership job; a seed that carried them would overwrite live state.
JOB_COLUMNS: Tuple[str, ...] = (
    "is_member", "member_since", "last_market_cap", "last_cap_date", "closes_at_or_above",
    "closes_below", "membership_checked_at",
)

CAP_SOURCES: Tuple[str, ...] = ("fmp_us", "fmp_adr", "manual")
MEMBERSHIP_MODES: Tuple[str, ...] = ("auto", "force_in", "force_out")

DISPLAY_NAME_MAX = 60

# `re.fullmatch` everywhere: `^...$` with re.match accepts a trailing "\n".
_SLUG_RE = re.compile(r"[a-z0-9-]{1,40}")
_CIK_RE = re.compile(r"[0-9]{10}")
_COUNTRY_RE = re.compile(r"[A-Z]{2}")

# ── Copy rules (plan §Copy) ───────────────────────────────────────────────────────────────
# BANNED_COPY / FORECAST_COPY / STAKE_TEXT_FIELDS / contains_banned_copy / contains_forecast
# are imported above from app/services/trillion_club/copy_rules.py — never re-declared here.

# ── Migration 205: rows the stake finder made ─────────────────────────────────────────────
# 205 adds `origin` to trillion_club_stakes: 'seed' (this script's rows, the column default),
# 'auto' / 'proposal' (rows the stake finder found in SEC filings). It is read in the SAME
# select as the seeded columns; a row without it (a database before 205, the test fakes) or
# with NULL reads as 'seed'. It is never written except by an adoption (ADOPTION_RESET).
ORIGIN_COLUMN = "origin"
_STAKE_READ_COLUMNS: Tuple[str, ...] = (*STAKE_COLUMNS, ORIGIN_COLUMN)
#: What --apply --update writes, beside the drifted columns, when a JSON stake adopts a row the
#: stake finder made: a seed row carries no finding, no Keep and no withdrawal (205 pairs
#: finding_id with a machine origin and owner_kept_at with 'auto').
ADOPTION_RESET: Mapping[str, Any] = MappingProxyType({
    "origin": ORIGIN_SEED, "finding_id": None, "owner_kept_at": None, "withdrawn_at": None,
})
# PostgREST's answer to a SELECT naming a column the table does not have: Postgres 42703
# ("column trillion_club_stakes.origin does not exist"); PGRST204 is the schema-cache variant
# ("Could not find the 'origin' column of 'trillion_club_stakes' in the schema cache").
_MISSING_COLUMN_CODES = frozenset({"42703", "PGRST204"})
_ORIGIN_WORD_RE = re.compile(r"\borigin\b", re.IGNORECASE)
_MISSING_COLUMN_TEXT_RE = re.compile(
    r"\bcolumn\b[^\n]{0,120}?\bdoes not exist\b|\bcould not find the\b[^\n]{0,80}?\bcolumn\b",
    re.IGNORECASE,
)


# ── Loading ───────────────────────────────────────────────────────────────────────────────


class SeedError(ValueError):
    """The seed file is unreadable or has the wrong shape (not a row-level problem)."""


def _reject_constant(token: str) -> Any:
    # json.load accepts NaN / Infinity by default; Postgres rejects them, and a NaN cap
    # would compare False against every threshold. Refuse at load time.
    raise SeedError(f"non-finite JSON number {token!r} in the seed")


def _reject_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    # json.load keeps the LAST of two identical keys, so a hand-edited row reading
    # `"published": false, ... "published": true` would be reviewed as unpublished and
    # written as published. Refuse the file instead.
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise SeedError(f"duplicate key {key!r} in one JSON object (json keeps only the last)")
        out[key] = value
    return out


def load_seed(path: str = SEED_PATH) -> Dict[str, List[Dict[str, Any]]]:
    """Read and shape-check the seed file. Raises SeedError on anything but
    ``{"companies": [dict, ...], "stakes": [dict, ...]}`` with no repeated key in any object."""
    try:
        with open(path, encoding="utf-8") as f:
            seed = json.load(f, parse_constant=_reject_constant,
                             object_pairs_hook=_reject_duplicate_keys)
    except SeedError:
        raise
    # ValueError covers JSONDecodeError, a non-UTF-8 file (UnicodeDecodeError) and an integer
    # literal past Python's 4,300-digit conversion limit; RecursionError a pathologically
    # nested file. Each is "unreadable" (exit 2), never a traceback.
    except (OSError, ValueError, RecursionError) as e:
        raise SeedError(f"cannot read {path}: {type(e).__name__}: {e}") from e
    if not isinstance(seed, dict) or set(seed) != {"companies", "stakes"}:
        got = sorted(seed) if isinstance(seed, dict) else type(seed).__name__
        raise SeedError(f"seed must be an object with exactly 'companies' and 'stakes', got {got}")
    for key in ("companies", "stakes"):
        rows = seed[key]
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise SeedError(f"seed[{key!r}] must be a list of objects")
    return seed


# ── Validation (pure) ─────────────────────────────────────────────────────────────────────
# The stake validator (stake_problems) and the small helpers both validators use (_text_ok,
# _finite_number, _is_https, _iso_date, …) are imported above from stake_rules.


def company_problems(row: Mapping[str, Any]) -> List[str]:
    """Pure: everything wrong with one company row (empty list = valid)."""
    out = _column_problems(row, COMPANY_COLUMNS, JOB_COLUMNS)
    out += _unstorable_columns(row) + _invisible_char_columns(row)
    g = row.get

    slug = g("slug")
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        out.append(f"slug {slug!r} does not match [a-z0-9-]{{1,40}}")
    if not _text_ok(g("display_name"), 1, DISPLAY_NAME_MAX):
        out.append(f"display_name must be 1-{DISPLAY_NAME_MAX} characters with no outer spaces")
    elif contains_banned_copy(g("display_name")):
        out.append("display_name contains banned wording")

    ciks = g("ciks")
    if not isinstance(ciks, list) or not all(isinstance(c, str) and _CIK_RE.fullmatch(c) for c in ciks):
        out.append(f"ciks must be a list of 10-digit zero-padded strings, got {ciks!r}")
    elif len(set(ciks)) != len(ciks):
        out.append("ciks has duplicates")

    card_kind, cap_source, mode = g("card_kind"), g("cap_source"), g("membership_mode")
    if card_kind not in CARD_KINDS:
        out.append(f"card_kind {card_kind!r} not in {CARD_KINDS}")
    if cap_source not in CAP_SOURCES:
        out.append(f"cap_source {cap_source!r} not in {CAP_SOURCES}")
    if mode not in MEMBERSHIP_MODES:
        out.append(f"membership_mode {mode!r} not in {MEMBERSHIP_MODES}")
    for col in ("use_13f", "link_whale", "published"):
        if not _is_bool(g(col)):
            out.append(f"{col} must be true/false, got {g(col)!r}")

    country = g("home_country")
    if not isinstance(country, str) or not _COUNTRY_RE.fullmatch(country):
        out.append(f"home_country {country!r} is not a 2-letter upper-case code")

    for col in ("cap_symbol", "detail_symbol", "logo_symbol"):
        v = g(col)
        if v is not None and (not isinstance(v, str) or not _SYMBOL_RE.fullmatch(v)):
            out.append(f"{col} {v!r} is not a ticker")
    aliases = g("symbol_aliases")
    if not isinstance(aliases, list) or not all(isinstance(a, str) and _SYMBOL_RE.fullmatch(a) for a in aliases):
        out.append(f"symbol_aliases must be a list of tickers, got {aliases!r}")
    elif len(set(aliases)) != len(aliases) or g("cap_symbol") in aliases:
        out.append("symbol_aliases repeats a symbol (or the cap_symbol)")

    for col in ("manual_cap_usd", "manual_fx_rate"):
        v = g(col)
        if v is not None and (_finite_number(v) is None or _finite_number(v) <= 0):
            out.append(f"{col} must be a positive finite number or null, got {v!r}")
    for col in ("manual_cap_as_of", "reviewed_on"):
        v = g(col)
        if v is not None and _iso_date(v) is None:
            out.append(f"{col} {v!r} is not an ISO date")
    url = g("manual_cap_source_url")
    if url is not None and not _is_https(url):
        out.append(f"manual_cap_source_url {url!r} is not an https URL")
    fx_source = g("manual_fx_source")
    if fx_source is not None and not _text_ok(fx_source, 1, 300):
        out.append("manual_fx_source must be 1-300 characters or null")

    # Migration 175's table constraints ------------------------------------------------
    if cap_source == "manual":
        if g("manual_cap_usd") is None or g("manual_cap_as_of") is None or url is None:
            out.append("a manual cap needs manual_cap_usd, manual_cap_as_of and manual_cap_source_url")
        if mode not in ("force_in", "force_out"):
            out.append("a manual cap must be an explicit call: membership_mode force_in or force_out")
        # Seed policy (critic §10): record the currency rate the figure rests on.
        if g("manual_fx_rate") is None or fx_source is None:
            out.append("a manual cap must record manual_fx_rate and manual_fx_source")
    elif cap_source in CAP_SOURCES:
        if g("cap_symbol") is None:
            out.append(f"cap_source {cap_source!r} needs a cap_symbol")
        leftovers = [c for c in ("manual_cap_usd", "manual_cap_as_of", "manual_cap_source_url",
                                 "manual_fx_rate", "manual_fx_source") if g(c) is not None]
        if leftovers:
            out.append(f"manual cap columns set on an FMP-sized company: {leftovers}")
    if g("use_13f") is True and (card_kind != "thirteen_f" or not isinstance(ciks, list) or not ciks):
        out.append("use_13f needs card_kind thirteen_f and at least one CIK")
    if _is_bool(g("link_whale")) and g("link_whale") != (card_kind == "whale_link"):
        out.append("link_whale must be true exactly when card_kind is whale_link")

    # Seed policies ------------------------------------------------------------------------
    if card_kind == "non_us" and country == "US":
        out.append("a non_us card needs a non-US home_country")
    if card_kind in CARD_KINDS and card_kind != "non_us" and isinstance(country, str) and country != "US":
        out.append(f"home_country {country!r} needs card_kind non_us")
    if g("published") is True and g("reviewed_on") is None:
        out.append("a published company needs reviewed_on")
    return out


def _identity_claims(row: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """What names ONE company: its listings (cap / detail symbol and share-class aliases,
    BRK.B == BRK-B) and its SEC CIKs. Malformed values are skipped (reported elsewhere)."""
    symbols = [row.get("cap_symbol"), row.get("detail_symbol")]
    aliases = row.get("symbol_aliases")
    if isinstance(aliases, list):
        symbols += aliases
    claims = {("symbol", _fold_symbol(s)) for s in symbols if isinstance(s, str) and s.strip()}
    ciks = row.get("ciks")
    if isinstance(ciks, list):
        claims |= {("CIK", c) for c in ciks if isinstance(c, str)}
    return sorted(claims)


def _row_problems(check: Any, row: Mapping[str, Any], *args: Any, **kwargs: Any) -> List[str]:
    """Run one row check, turning a crash on a malformed value into a problem: the promise is
    "exit 2 with the row named", never a traceback. Fail-closed — the seed is not written."""
    try:
        return check(row, *args, **kwargs)
    except Exception as e:  # noqa: BLE001 — reported as a validation problem, never swallowed
        return [f"could not be validated ({type(e).__name__}: {e})"]


def validate_seed(seed: Mapping[str, Sequence[Mapping[str, Any]]], *,
                  today: Optional[date] = None) -> List[str]:
    """Pure: every problem in the seed, each prefixed with the row it belongs to."""
    problems: List[str] = []
    companies: Dict[str, Mapping[str, Any]] = {}
    # (kind, value) → the first slug claiming it. Nothing in Postgres is UNIQUE on a ticker
    # or a CIK, so a second slug for one company ("nvda" beside "nvidia") would give Home two
    # cards and the daily job two rows tracking one ticker.
    claimed: Dict[Tuple[str, str], str] = {}
    for i, row in enumerate(seed.get("companies", [])):
        label = f"companies[{i}:{row.get('slug')!r}]"
        problems += [f"{label}: {p}" for p in _row_problems(company_problems, row)]
        slug = row.get("slug")
        if isinstance(slug, str):
            if slug in companies:
                problems.append(f"{label}: duplicate slug")
            companies.setdefault(slug, row)
            for claim in _identity_claims(row):
                owner = claimed.setdefault(claim, slug)
                if owner != slug:
                    problems.append(f"{label}: {claim[0]} {claim[1]!r} is already claimed by "
                                    f"company {owner!r} (one company under two slugs)")
    stakes = seed.get("stakes", [])
    seen_keys: Dict[Tuple[str, str, str], int] = {}
    for i, row in enumerate(stakes):
        key = stake_key(row)
        label = f"stakes[{i}:{key[0]}/{key[1]!r}/{key[2]}]"
        problems += [f"{label}: {p}" for p in _row_problems(stake_problems, row, companies, today=today)]
        folded = _folded_stake_key(row)
        if folded is None:
            continue
        if folded not in seen_keys:
            seen_keys[folded] = i
            continue
        j = seen_keys[folded]
        if stake_key(stakes[j]) == key:
            problems.append(f"{label}: duplicate (company_slug, investee_name, kind) of stakes[{j}]")
        else:
            # The UNIQUE constraint compares bytes, so Postgres would store both and the card
            # would show the same stake twice.
            problems.append(f"{label}: near-duplicate of stakes[{j}] ({stakes[j].get('investee_name')!r}): "
                            "the same name once case, Unicode form, spacing and invisible "
                            "characters are ignored")
    return problems


# ── Planning (pure) ───────────────────────────────────────────────────────────────────────


_MISSING = object()


def _same(a: Any, b: Any) -> bool:
    """Seed value vs database value, allowing for PostgREST's representations: a DOUBLE comes
    back as int or float, a DATE as 'YYYY-MM-DD', a TEXT[] as a list."""
    if a is _MISSING or b is _MISSING:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    fa, fb = _finite_number(a), _finite_number(b)
    if fa is not None and fb is not None:
        return math.isclose(fa, fb, rel_tol=1e-12, abs_tol=0.0)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def diff_row(seed_row: Mapping[str, Any], db_row: Mapping[str, Any],
             columns: Sequence[str]) -> Dict[str, Tuple[Any, Any]]:
    """{column: (database value, seed value)} for every seeded column that differs."""
    out: Dict[str, Tuple[Any, Any]] = {}
    for col in columns:
        want = seed_row.get(col, _MISSING)
        have = db_row.get(col, _MISSING)
        if not _same(want, have):
            out[col] = ("<absent>" if have is _MISSING else have, want)
    return out


def _stake_origin(row: Mapping[str, Any]) -> Any:
    """A database stake row's migration-205 ``origin``. Absent (a database before 205, the
    test fakes, the 175-only Postgres harness) or NULL reads as 'seed'; any other value is
    returned as it is, for the caller to classify (``plan_sync`` never writes one it does not
    know)."""
    value = row.get(ORIGIN_COLUMN)
    return ORIGIN_SEED if value is None else value


class StakeAdoption(NamedTuple):
    """A JSON stake whose database row the stake finder made (origin 'auto' / 'proposal')."""

    key: Tuple[Any, Any, Any]
    diffs: Dict[str, Tuple[Any, Any]]   # the seeded columns that differ, as in stake_updates
    origin: str                         # the row's origin before the adoption


# What an identity match was made on (stake_names.IdentityMatch.by), as the warning says it.
_MATCHED_BY = {
    "cik": "the same SEC CIK",
    "cusip": "the same CUSIP issuer (its first 6 characters)",
    "symbol": "the same U.S. symbol (share-class aliases included)",
    "name": ("the same name only — POSSIBLY the same company (neither row carries an "
             "identifier the other has)"),
}


def identity_duplicates(seed: Mapping[str, Sequence[Mapping[str, Any]]],
                        db_stakes: Sequence[Mapping[str, Any]],
                        ) -> List[Tuple[Tuple[Any, Any, Any], Tuple[Any, Any, Any], str]]:
    """Pure: ``(JSON stake key, database row key, matched by)`` for every JSON stake that names
    the same investee as a row the stake finder made (origin 'auto' / 'proposal') for the SAME
    company under a DIFFERENT natural key. ``--apply`` would insert the JSON row beside it and
    the card would show one investee twice. Matching is the finder's own
    (``stake_names.IdentityIndex``): CUSIP-6, then the normalised symbol with the club's
    share-class aliases (GOOG == GOOGL), then — only when neither row carries an identifier the
    other has — every significant name token. A pair with the same key is an adoption, never a
    duplicate."""
    aliases: Dict[str, List[str]] = {}
    for c in seed.get("companies", []):
        cap, extra = c.get("cap_symbol"), c.get("symbol_aliases")
        if isinstance(cap, str) and isinstance(extra, list):
            aliases[cap] = [a for a in extra if isinstance(a, str)]
    machine_by_slug: Dict[str, List[Mapping[str, Any]]] = {}
    for r in db_stakes:
        slug = r.get("company_slug")
        if isinstance(slug, str) and _stake_origin(r) in MACHINE_ORIGINS:
            machine_by_slug.setdefault(slug, []).append(r)
    out: List[Tuple[Tuple[Any, Any, Any], Tuple[Any, Any, Any], str]] = []
    for s in seed.get("stakes", []):
        key, slug = stake_key(s), s.get("company_slug")
        if not isinstance(slug, str):   # an unhashable hand-edit; validate_seed reports it
            continue
        for r in machine_by_slug.get(slug, ()):
            if stake_key(r) == key:
                continue
            match = IdentityIndex([r], symbol_aliases=aliases).match(
                cusip=s.get("investee_cusip"), symbol=s.get("investee_us_symbol"),
                name=s.get("investee_name"))
            if match is not None:
                out.append((key, stake_key(r), match.by))
    return out


@dataclass
class SyncPlan:
    company_inserts: List[Dict[str, Any]] = field(default_factory=list)
    company_updates: List[Tuple[str, Dict[str, Tuple[Any, Any]]]] = field(default_factory=list)
    stake_inserts: List[Dict[str, Any]] = field(default_factory=list)
    stake_updates: List[Tuple[Tuple[Any, Any, Any], Dict[str, Tuple[Any, Any]]]] = field(default_factory=list)
    orphan_companies: List[str] = field(default_factory=list)
    orphan_stakes: List[Tuple[Any, Any, Any]] = field(default_factory=list)
    # Migration 205 — rows the stake finder made (origin 'auto' / 'proposal') ---------------
    # JSON stakes whose row the finder made: --apply --update makes each a seed row.
    stake_adoptions: List[StakeAdoption] = field(default_factory=list)
    # The finder's rows the JSON does not list: never orphans, never drift, never written.
    machine_stakes: List[Dict[str, Any]] = field(default_factory=list)
    # (key, origin) of rows whose origin this script does not know: never written.
    unknown_origin_stakes: List[Tuple[Tuple[Any, Any, Any], Any]] = field(default_factory=list)
    # (JSON stake key, finder row key, matched by): see identity_duplicates.
    identity_duplicates: List[Tuple[Tuple[Any, Any, Any], Tuple[Any, Any, Any], str]] = field(
        default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.company_inserts or self.company_updates
                    or self.stake_inserts or self.stake_updates or self.stake_adoptions)


def plan_sync(seed: Mapping[str, Sequence[Mapping[str, Any]]],
              db_companies: Sequence[Mapping[str, Any]],
              db_stakes: Sequence[Mapping[str, Any]]) -> SyncPlan:
    """Pure: what an additive sync would insert, which rows drifted, and what is orphaned.
    Never plans a delete.

    A database stake row the stake finder made (origin 'auto' / 'proposal') is never an
    orphan and never drift: the JSON listing its exact key ADOPTS it (``stake_adoptions``,
    planned even with nothing drifted), otherwise it is listed apart (``machine_stakes``). A
    row with an origin this script does not know is never written at all."""
    plan = SyncPlan()
    existing_c = {r.get("slug"): r for r in db_companies}
    for row in seed["companies"]:
        have = existing_c.get(row["slug"])
        if have is None:
            plan.company_inserts.append({c: row[c] for c in COMPANY_COLUMNS})
        else:
            diffs = diff_row(row, have, COMPANY_COLUMNS)
            if diffs:
                plan.company_updates.append((row["slug"], diffs))
    seeded_slugs = {r["slug"] for r in seed["companies"]}
    plan.orphan_companies = sorted(s for s in existing_c if s not in seeded_slugs)

    existing_s = {stake_key(r): r for r in db_stakes}
    for row in seed["stakes"]:
        key = stake_key(row)
        have = existing_s.get(key)
        if have is None:
            plan.stake_inserts.append({c: row[c] for c in STAKE_COLUMNS})
            continue
        origin = _stake_origin(have)
        diffs = diff_row(row, have, STAKE_COLUMNS)
        if origin == ORIGIN_SEED:
            if diffs:
                plan.stake_updates.append((key, diffs))
        elif origin in MACHINE_ORIGINS:
            plan.stake_adoptions.append(StakeAdoption(key, diffs, origin))
        # Any other origin: left exactly as it is (listed in unknown_origin_stakes below).
    seeded_keys = {stake_key(r) for r in seed["stakes"]}
    orphans: List[Tuple[Any, Any, Any]] = []
    for key, have in existing_s.items():
        origin = _stake_origin(have)
        if origin == ORIGIN_SEED:
            if key not in seeded_keys:
                orphans.append(key)
        elif origin in MACHINE_ORIGINS:
            if key not in seeded_keys:
                plan.machine_stakes.append(dict(have))
        else:
            plan.unknown_origin_stakes.append((key, origin))
    plan.orphan_stakes = sorted(orphans, key=repr)
    plan.machine_stakes.sort(key=lambda r: repr(stake_key(r)))
    plan.unknown_origin_stakes.sort(key=lambda item: repr(item[0]))
    plan.identity_duplicates = identity_duplicates(seed, db_stakes)
    return plan


def report_plan(plan: SyncPlan) -> None:
    for row in plan.company_inserts:
        logger.info("  + company %s (%s)", row["slug"], row["display_name"])
    for slug, diffs in plan.company_updates:
        for col, (have, want) in sorted(diffs.items()):
            logger.info("  ~ company %s.%s: %r -> %r", slug, col, have, want)
    for row in plan.stake_inserts:
        logger.info("  + stake %s / %s (%s)", row["company_slug"], row["investee_name"], row["kind"])
    for key, diffs in plan.stake_updates:
        for col, (have, want) in sorted(diffs.items()):
            logger.info("  ~ stake %s / %s (%s).%s: %r -> %r", key[0], key[1], key[2], col, have, want)
    for key, diffs, origin in plan.stake_adoptions:
        logger.info("  * adopt stake %s / %s (%s): found automatically (origin %s); --apply "
                    "--update makes it a seed row (origin seed; its finding, Keep and withdrawal "
                    "cleared) with %d drifted column(s)", key[0], key[1], key[2], origin, len(diffs))
        for col, (have, want) in sorted(diffs.items()):
            logger.info("  ~ stake %s / %s (%s).%s: %r -> %r", key[0], key[1], key[2], col, have, want)
    logger.info("  found automatically: %d rows (origin auto / proposal, not in the JSON): the "
                "stake finder's own, never orphans or drift, never written by this script",
                len(plan.machine_stakes))
    for row in plan.machine_stakes:
        logger.info("    = %s / %s (%s): origin %s, %s", row.get("company_slug"),
                    row.get("investee_name"), row.get("kind"), _stake_origin(row),
                    "published" if row.get("published") is True else "unpublished")
    if plan.unknown_origin_stakes:
        logger.warning(
            "UNKNOWN ORIGIN: %d stake row(s) carry an origin this script does not know (it knows "
            "%s), so they are left exactly as they are — never updated, adopted or reported as "
            "orphans: %s. Update scripts/seed_trillion_club.py before seeding over them.",
            len(plan.unknown_origin_stakes), list(ORIGINS),
            [f"{k[0]}/{k[1]}/{k[2]} (origin {o!r})" for k, o in plan.unknown_origin_stakes],
        )
    for json_key, db_key, by in plan.identity_duplicates:
        logger.warning(
            "IDENTITY: the JSON's stake %s/%s/%s and the row found automatically %s/%s/%s share "
            "%s. With both published the card shows that investee twice: give the JSON row that "
            "row's exact (company_slug, investee_name, kind) so --apply --update adopts it, or "
            "leave the JSON row out.",
            json_key[0], json_key[1], json_key[2], db_key[0], db_key[1], db_key[2],
            _MATCHED_BY.get(by, f"the same investee ({by})"),
        )
    if plan.orphan_companies or plan.orphan_stakes:
        logger.warning(
            "DRIFT: %d company row(s) and %d stake row(s) are in the database but NOT in the "
            "seed, and will keep being served if published: companies=%s stakes=%s. Add them "
            "to trillion_club_seed.json, or delete them deliberately in Studio (stakes cascade "
            "from their company).",
            len(plan.orphan_companies), len(plan.orphan_stakes),
            plan.orphan_companies, [f"{k[0]}/{k[1]}/{k[2]}" for k in plan.orphan_stakes],
        )


# ── Database I/O ──────────────────────────────────────────────────────────────────────────


def _error_text(exc: BaseException) -> str:
    """The structured parts of a PostgREST error (message / details / hint), else str(exc).
    Never str(exc) first: postgrest's APIError renders differently across versions."""
    parts = [getattr(exc, attr, None) for attr in ("message", "details", "hint")]
    return " ".join(str(p) for p in parts if p) or str(exc)


def _is_missing_origin_column(exc: BaseException) -> bool:
    """True only for "trillion_club_stakes.origin does not exist" — a database before
    migration 205. A structured code is the answer when there is one (42703 / PGRST204, and the
    text must name ``origin``: another missing column is a real fault). An int code is an edge
    or gateway status (supabase_errors' invariant), never schema drift. With no code, the text
    must say a column named origin does not exist."""
    code = getattr(exc, "code", None)
    if isinstance(code, (bool, int)):
        return False
    text = _error_text(exc)
    if not _ORIGIN_WORD_RE.search(text):
        return False
    if isinstance(code, str):
        return code.strip().upper() in _MISSING_COLUMN_CODES
    if code is not None:
        return False
    return _MISSING_COLUMN_TEXT_RE.search(text) is not None


def fetch_existing(sb: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Read both tables (every seeded column, plus the stakes' migration-205 ``origin`` in the
    SAME select). Raises on a read failure — an unreadable table must never look like an empty
    one, or every row would be planned as an insert.

    Before migration 205 is applied ``origin`` does not exist and PostgREST refuses the whole
    select. That answer, and only that one, is retried ONCE with the 175 columns: no row can
    have been found automatically yet, so every row reads as seed (one WARNING)."""
    companies = sb.table(COMPANIES_TABLE).select(",".join(COMPANY_COLUMNS)).execute().data
    try:
        stakes = sb.table(STAKES_TABLE).select(",".join(_STAKE_READ_COLUMNS)).execute().data
    except Exception as e:
        if not _is_missing_origin_column(e):
            raise
        logger.warning(
            "%s has no %s column (%s: %s): migration 205 is not applied, so no stake can have "
            "been found automatically yet. Reading the migration-175 columns only; every row is "
            "a seed row.", STAKES_TABLE, ORIGIN_COLUMN, type(e).__name__, _error_text(e)[:300])
        stakes = sb.table(STAKES_TABLE).select(",".join(STAKE_COLUMNS)).execute().data
    for table, rows in ((COMPANIES_TABLE, companies), (STAKES_TABLE, stakes)):
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise RuntimeError(f"unexpected response shape reading {table}: "
                               f"{type(rows).__name__}, not a list of rows")
    return companies, stakes


def apply_plan(sb: Any, plan: SyncPlan, seed: Mapping[str, Sequence[Mapping[str, Any]]], *,
               update: bool, existing_slugs: Sequence[str]) -> Tuple[int, int]:
    """Write the plan: inserts always, drifted columns only when ``update``. Companies go
    first (stakes reference them). Each row is isolated: one failure is logged with its
    identifiers and the rest carry on. Returns (rows written, errors).

    An adoption (a JSON stake whose row the stake finder made) is written only when
    ``update``, through the SAME full-key UPDATE as drift and even with nothing drifted: its
    drifted columns plus ``ADOPTION_RESET``."""
    written = errors = 0
    now = datetime.now(timezone.utc).isoformat()
    present = set(existing_slugs)

    for row in plan.company_inserts:
        try:
            sb.table(COMPANIES_TABLE).insert(row).execute()
            present.add(row["slug"])
            written += 1
            logger.info("  inserted company %s", row["slug"])
        except Exception as e:
            errors += 1
            logger.error("  FAILED to insert company %s: %s: %s", row["slug"], type(e).__name__, e)
    if update:
        seed_c = {r["slug"]: r for r in seed["companies"]}
        for slug, diffs in plan.company_updates:
            payload = {c: seed_c[slug][c] for c in diffs}
            payload["updated_at"] = now
            try:
                sb.table(COMPANIES_TABLE).update(payload).eq("slug", slug).execute()
                written += 1
                logger.info("  updated company %s: %s", slug, sorted(diffs))
            except Exception as e:
                errors += 1
                logger.error("  FAILED to update company %s: %s: %s", slug, type(e).__name__, e)

    for row in plan.stake_inserts:
        label = f"{row['company_slug']} / {row['investee_name']} ({row['kind']})"
        if row["company_slug"] not in present:
            errors += 1
            logger.error("  SKIPPED stake %s: its company row is not in the database", label)
            continue
        try:
            sb.table(STAKES_TABLE).insert(row).execute()
            written += 1
            logger.info("  inserted stake %s", label)
        except Exception as e:
            errors += 1
            logger.error("  FAILED to insert stake %s: %s: %s", label, type(e).__name__, e)
    if update:
        seed_s = {stake_key(r): r for r in seed["stakes"]}
        adopted = {a.key: a.origin for a in plan.stake_adoptions}
        for key, diffs in [*plan.stake_updates, *((a.key, a.diffs) for a in plan.stake_adoptions)]:
            payload = {c: seed_s[key][c] for c in diffs}
            if key in adopted:
                payload.update(ADOPTION_RESET)
            payload["updated_at"] = now
            label = f"{key[0]} / {key[1]} ({key[2]})"
            try:
                (sb.table(STAKES_TABLE).update(payload)
                   .eq("company_slug", key[0]).eq("investee_name", key[1]).eq("kind", key[2])
                   .execute())
                written += 1
                if key in adopted:
                    logger.info("  adopted stake %s (origin %s -> seed): %s", label, adopted[key],
                                sorted(diffs))
                else:
                    logger.info("  updated stake %s: %s", label, sorted(diffs))
            except Exception as e:
                errors += 1
                logger.error("  FAILED to %s stake %s: %s: %s", "adopt" if key in adopted else "update",
                             label, type(e).__name__, e)
    return written, errors


# ── CLI ───────────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Seed the Trillion-Dollar Club tables from trillion_club_seed.json. "
                    "WRITES TO PRODUCTION with --apply.")
    p.add_argument("--seed", default=SEED_PATH, help="path to the seed JSON")
    p.add_argument("--validate-only", action="store_true",
                   help="check the JSON and exit; never touches the database")
    p.add_argument("--apply", action="store_true",
                   help="write to PRODUCTION: insert missing rows")
    p.add_argument("--update", action="store_true",
                   help="with --apply: also overwrite drifted columns with the JSON's values")
    return p


def main(argv: Optional[Sequence[str]] = None, *, sb: Any = None) -> int:
    args = build_parser().parse_args(argv)
    if args.update and not args.apply:
        logger.error("--update only makes sense with --apply (a dry run already shows drift)")
        return 2
    if args.validate_only and args.apply:
        logger.error("--validate-only and --apply are mutually exclusive")
        return 2

    try:
        seed = load_seed(args.seed)
    except SeedError as e:
        logger.error("Seed unreadable: %s", e)
        return 2
    problems = validate_seed(seed)
    logger.info("Loaded %d companies and %d stakes from %s",
                len(seed["companies"]), len(seed["stakes"]), args.seed)
    for p in problems:
        logger.error("  INVALID %s", p)
    if problems:
        logger.error("Seed failed validation (%d problem(s)); nothing was written.", len(problems))
        return 2
    logger.info("Seed is valid.")
    if args.validate_only:
        return 0

    if sb is None:
        from app.database import get_supabase  # lazy: validation must not need credentials

        sb = get_supabase()
    mode = "APPLY+UPDATE" if args.update else ("APPLY" if args.apply else "DRY RUN")
    logger.warning("Target database is the one in backend/.env — PRODUCTION. Mode: %s", mode)

    try:
        db_companies, db_stakes = fetch_existing(sb)
    except Exception as e:
        logger.error("Could not read the trillion club tables (%s: %s). Is migration 175 applied? "
                     "(Migration 205 adds trillion_club_stakes.origin; before it is applied the 175 "
                     "columns are read instead, so a failure here is not a missing 205.)",
                     type(e).__name__, e)
        return 1
    plan = plan_sync(seed, db_companies, db_stakes)
    report_plan(plan)
    logger.info(
        "Plan: +%d companies, ~%d drifted companies, +%d stakes, ~%d drifted stakes, "
        "%d stakes to adopt from the stake finder, %d orphan companies, %d orphan stakes, "
        "%d stakes found automatically",
        len(plan.company_inserts), len(plan.company_updates), len(plan.stake_inserts),
        len(plan.stake_updates), len(plan.stake_adoptions), len(plan.orphan_companies),
        len(plan.orphan_stakes), len(plan.machine_stakes))

    if not args.apply:
        logger.info("DRY RUN — nothing written. Re-run with --apply (and --update to overwrite drift).")
        return 0
    if plan.company_updates or plan.stake_updates:
        if args.update:
            logger.warning("--update: the JSON's values overwrite the drifted columns listed above.")
        else:
            logger.warning("Drifted rows were left as they are (pass --update to overwrite them).")
    if plan.stake_adoptions:
        if args.update:
            logger.warning("--update: %d stake row(s) found automatically become seed rows (the "
                           "'adopt' lines above).", len(plan.stake_adoptions))
        else:
            logger.warning("%d stake row(s) found automatically that the JSON lists were left as "
                           "they are (pass --update to adopt them as seed rows).",
                           len(plan.stake_adoptions))
    written, errors = apply_plan(sb, plan, seed, update=args.update,
                                 existing_slugs=[r.get("slug") for r in db_companies])
    logger.info("Done. written=%d errors=%d", written, errors)
    return 1 if errors else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    sys.exit(main())
