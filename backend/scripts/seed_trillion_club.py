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
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.schemas.trillion_club import CARD_KINDS, STAKE_KINDS, VALUE_BASES  # noqa: E402
# ONE copy of the copy rules, shared with the request path (trillion_club_service), so a
# Studio edit this script would refuse can never be served. Re-exported for the tests.
from app.services.trillion_club.copy_rules import (  # noqa: E402,F401
    BANNED_COPY,
    FORECAST_COPY,
    STAKE_TEXT_FIELDS,
    contains_banned_copy,
    contains_forecast,
)

logger = logging.getLogger("seed_trillion_club")

SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trillion_club_seed.json")

COMPANIES_TABLE = "trillion_club_companies"
STAKES_TABLE = "trillion_club_stakes"

# ── The contract: exactly the hand-kept columns of migration 175 ────────────────────────
# tests/test_trillion_club_seed_integrity.py parses the migration and pins these against it.
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
STAKE_COLUMNS: Tuple[str, ...] = (
    "company_slug", "kind", "investee_name", "investee_cusip", "investee_us_symbol",
    "local_listing", "ownership_pct", "ownership_basis", "disclosed_value_usd", "value_basis",
    "as_of", "source_title", "source_url", "source_confidence", "material", "tied_to_deal",
    "listed_since", "background", "verified_on", "published", "sort_order",
)
STAKE_KEY: Tuple[str, str, str] = ("company_slug", "investee_name", "kind")

CAP_SOURCES: Tuple[str, ...] = ("fmp_us", "fmp_adr", "manual")
MEMBERSHIP_MODES: Tuple[str, ...] = ("auto", "force_in", "force_out")
SOURCE_CONFIDENCES: Tuple[str, ...] = ("primary", "secondary")

DISPLAY_NAME_MAX = 60
INVESTEE_NAME_MAX = 60
SOURCE_TITLE_MAX = 120
BACKGROUND_MAX = 90
# sort_order is INTEGER (int4): a larger value validates but Postgres refuses it (22003)
# half-way through --apply, after earlier rows were written.
INT4_MAX = 2 ** 31 - 1

# `re.fullmatch` everywhere: `^...$` with re.match accepts a trailing "\n".
_SLUG_RE = re.compile(r"[a-z0-9-]{1,40}")
_CIK_RE = re.compile(r"[0-9]{10}")
_COUNTRY_RE = re.compile(r"[A-Z]{2}")
_CUSIP_RE = re.compile(r"[0-9A-Z]{9}")
# A US ticker or an exchange-suffixed one (BRK-B, 2222.SR, 005930.KS).
_SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# ── Copy rules (plan §Copy) ───────────────────────────────────────────────────────────────
# BANNED_COPY / FORECAST_COPY / STAKE_TEXT_FIELDS / contains_banned_copy / contains_forecast
# are imported above from app/services/trillion_club/copy_rules.py — never re-declared here.

# Text Postgres cannot store: NUL is refused in any TEXT (22P05) and a lone UTF-16 surrogate
# cannot be encoded as UTF-8 (22P02). json.load happily returns both from "\u0000" / "\ud800".
_UNSTORABLE_CHAR = re.compile(r"[\x00\ud800-\udfff]")
# One-line display text (names, titles, background): no control or format characters. A
# zero-width space (U+200B, category Cf) makes "Adv Holdings Ltd" a different key that
# renders identically; a tab or newline breaks the one-line layout.
_INVISIBLE_CATEGORIES = ("Cc", "Cf", "Cs")
_DISPLAY_TEXT_COLUMNS: Tuple[str, ...] = (
    "display_name", "manual_fx_source", *STAKE_TEXT_FIELDS,
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


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _finite_number(v: Any) -> Optional[float]:
    """A real, finite number (bools are not numbers here), else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:  # a JSON integer past a double (10**400) is valid JSON, not a number here
        return None
    return f if math.isfinite(f) else None


def _iso_date(v: Any) -> Optional[date]:
    if not isinstance(v, str) or not _DATE_RE.fullmatch(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _is_https(v: Any) -> bool:
    return (isinstance(v, str) and v.startswith("https://") and len(v) > len("https://")
            and not any(ch.isspace() for ch in v))


def _text_ok(v: Any, lo: int, hi: int) -> bool:
    return (isinstance(v, str) and lo <= len(v) <= hi and v == v.strip()
            and _UNSTORABLE_CHAR.search(v) is None)


def _unstorable_columns(row: Mapping[str, Any]) -> List[str]:
    """Every column holding a string (or a list element) Postgres would refuse at --apply."""
    out: List[str] = []
    for col, v in row.items():
        values = v if isinstance(v, list) else [v]
        if any(isinstance(x, str) and _UNSTORABLE_CHAR.search(x) for x in values):
            out.append(f"{col} holds text Postgres cannot store (a NUL or a lone surrogate)")
    return out


def _invisible_char_columns(row: Mapping[str, Any]) -> List[str]:
    """Display text carrying a control / format character (U+200B, a tab, a newline)."""
    out: List[str] = []
    for col in _DISPLAY_TEXT_COLUMNS:
        v = row.get(col)
        if isinstance(v, str) and any(unicodedata.category(ch) in _INVISIBLE_CATEGORIES for ch in v):
            out.append(f"{col} holds an invisible control/format character")
    return out


def _fold_name(name: str) -> str:
    """The identity a reader sees: NFKC, case-folded, format characters dropped, whitespace
    collapsed. "Adv Holdings Ltd" / "ADV HOLDINGS LTD" / an NFD "Société" / a name with a
    zero-width space all fold to one key (the service sorts by casefold() too)."""
    folded = unicodedata.normalize("NFKC", name)
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    return " ".join(folded.casefold().split())


def _fold_symbol(symbol: str) -> str:
    """BRK.B and BRK-B are one listing."""
    return symbol.strip().upper().replace(".", "-")


def _column_problems(row: Mapping[str, Any], expected: Sequence[str], forbidden: Sequence[str] = ()) -> List[str]:
    out: List[str] = []
    keys = set(row)
    for col in sorted(keys & set(forbidden)):
        out.append(f"job-written column {col!r} must not be seeded")
    missing = [c for c in expected if c not in keys]
    extra = sorted(keys - set(expected) - set(forbidden))
    if missing:
        out.append(f"missing columns {missing}")
    if extra:
        out.append(f"unknown columns {extra}")
    return out


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


def stake_problems(row: Mapping[str, Any], companies: Mapping[str, Mapping[str, Any]],
                   *, today: Optional[date] = None) -> List[str]:
    """Pure: everything wrong with one stake row (empty list = valid). ``companies`` maps
    slug → company row, for the foreign key and the per-card-kind policies."""
    today = today or date.today()
    out = _column_problems(row, STAKE_COLUMNS)
    out += _unstorable_columns(row) + _invisible_char_columns(row)
    g = row.get

    slug = g("company_slug")
    company = companies.get(slug) if isinstance(slug, str) else None
    if company is None:
        out.append(f"company_slug {slug!r} is not a seeded company")
    kind = g("kind")
    if kind not in STAKE_KINDS:
        out.append(f"kind {kind!r} not in {STAKE_KINDS}")
    if not _text_ok(g("investee_name"), 1, INVESTEE_NAME_MAX):
        out.append(f"investee_name must be 1-{INVESTEE_NAME_MAX} characters with no outer spaces")
    cusip = g("investee_cusip")
    if cusip is not None and (not isinstance(cusip, str) or not _CUSIP_RE.fullmatch(cusip)):
        out.append(f"investee_cusip {cusip!r} is not 9 upper-case letters/digits")
    symbol = g("investee_us_symbol")
    if symbol is not None and (not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol)):
        out.append(f"investee_us_symbol {symbol!r} is not a ticker")
    local = g("local_listing")
    if local is not None and not _text_ok(local, 1, 60):
        out.append("local_listing must be 1-60 characters or null")

    pct = g("ownership_pct")
    pct_f = _finite_number(pct) if pct is not None else None
    if pct is not None and (pct_f is None or not 0 < pct_f <= 100):
        out.append(f"ownership_pct must be in (0, 100], got {pct!r}")
    basis_text = g("ownership_basis")
    if basis_text is not None and not _text_ok(basis_text, 1, 60):
        out.append("ownership_basis must be 1-60 characters or null")
    if (pct is None) != (basis_text is None):
        out.append("ownership_pct and ownership_basis go together (a % needs its basis)")

    value = g("disclosed_value_usd")
    value_f = _finite_number(value) if value is not None else None
    if value is not None and (value_f is None or value_f <= 0):
        out.append(f"disclosed_value_usd must be a positive finite number, got {value!r}")
    value_basis = g("value_basis")
    if value_basis is not None and value_basis not in VALUE_BASES:
        out.append(f"value_basis {value_basis!r} not in {VALUE_BASES}")
    if value is not None and value_basis is None:
        out.append("disclosed_value_usd needs a value_basis")
    if value is None and value_basis is not None:
        out.append("value_basis without a disclosed_value_usd")
    if kind == "commitment" and value_basis not in (None, "committed_up_to"):
        out.append("a commitment's value_basis must be committed_up_to")
    if value_basis == "committed_up_to" and kind != "commitment":
        out.append("committed_up_to is only for kind commitment")

    as_of, verified_on = _iso_date(g("as_of")), _iso_date(g("verified_on"))
    if as_of is None:
        out.append(f"as_of {g('as_of')!r} is not an ISO date")
    if verified_on is None:
        out.append(f"verified_on {g('verified_on')!r} is not an ISO date")
    elif verified_on > today:
        out.append(f"verified_on {verified_on} is in the future")
    if as_of and verified_on and as_of > verified_on:
        out.append("as_of is after verified_on (a figure cannot describe a date not yet checked)")
    listed_since = g("listed_since")
    if listed_since is not None:
        ls = _iso_date(listed_since)
        if ls is None:
            out.append(f"listed_since {listed_since!r} is not an ISO date")
        elif verified_on and ls > verified_on:
            out.append("listed_since is after verified_on")

    if not _text_ok(g("source_title"), 1, SOURCE_TITLE_MAX):
        out.append(f"source_title must be 1-{SOURCE_TITLE_MAX} characters")
    if not _is_https(g("source_url")):
        out.append(f"source_url {g('source_url')!r} is not an https URL")
    confidence = g("source_confidence")
    if confidence not in SOURCE_CONFIDENCES:
        out.append(f"source_confidence {confidence!r} not in {SOURCE_CONFIDENCES}")
    for col in ("material", "tied_to_deal", "published"):
        if not _is_bool(g(col)):
            out.append(f"{col} must be true/false, got {g(col)!r}")
    if g("published") is True and confidence == "secondary":
        out.append("a secondary-sourced stake can never be published")
    sort_order = g("sort_order")
    if not _is_int(sort_order) or not 0 <= sort_order <= INT4_MAX:
        out.append(f"sort_order must be an integer in [0, {INT4_MAX}] (INTEGER), got {sort_order!r}")

    background = g("background")
    if background is not None:
        if not _text_ok(background, 1, BACKGROUND_MAX):
            out.append(f"background must be 1-{BACKGROUND_MAX} characters or null")
        elif contains_forecast(background):
            out.append("background states a forecast or motive; keep it to what happened")
    for col in STAKE_TEXT_FIELDS:
        if contains_banned_copy(g(col)):
            out.append(f"{col} contains banned wording")

    # Seed policies ------------------------------------------------------------------------
    if g("material") is True and pct is None and value is None:
        out.append("material needs a disclosed ownership_pct or disclosed_value_usd")
    if company is not None:
        if kind == "on_13f_note" and company.get("card_kind") != "thirteen_f":
            out.append("on_13f_note belongs only on a thirteen_f card")
    if kind == "us_listed_off_13f" and symbol is None:
        out.append("us_listed_off_13f needs the investee_us_symbol")
    if kind == "non_us_listed" and local is None:
        out.append("non_us_listed needs a local_listing")
    if kind == "private" and (symbol is not None or local is not None):
        out.append("a private stake has no listing (investee_us_symbol / local_listing)")
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


def _folded_stake_key(row: Mapping[str, Any]) -> Optional[Tuple[str, str, str]]:
    """The stake's key as a reader sees it (see `_fold_name`); None when a key column is not
    a string (a hand-edit put a list or an object there — reported by stake_problems)."""
    slug, name, kind = stake_key(row)
    if not (isinstance(slug, str) and isinstance(name, str) and isinstance(kind, str)):
        return None
    return slug, _fold_name(name), kind


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


def stake_key(row: Mapping[str, Any]) -> Tuple[Any, Any, Any]:
    return tuple(row.get(c) for c in STAKE_KEY)  # type: ignore[return-value]


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


@dataclass
class SyncPlan:
    company_inserts: List[Dict[str, Any]] = field(default_factory=list)
    company_updates: List[Tuple[str, Dict[str, Tuple[Any, Any]]]] = field(default_factory=list)
    stake_inserts: List[Dict[str, Any]] = field(default_factory=list)
    stake_updates: List[Tuple[Tuple[Any, Any, Any], Dict[str, Tuple[Any, Any]]]] = field(default_factory=list)
    orphan_companies: List[str] = field(default_factory=list)
    orphan_stakes: List[Tuple[Any, Any, Any]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.company_inserts or self.company_updates
                    or self.stake_inserts or self.stake_updates)


def plan_sync(seed: Mapping[str, Sequence[Mapping[str, Any]]],
              db_companies: Sequence[Mapping[str, Any]],
              db_stakes: Sequence[Mapping[str, Any]]) -> SyncPlan:
    """Pure: what an additive sync would insert, which rows drifted, and what is orphaned.
    Never plans a delete."""
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
        else:
            diffs = diff_row(row, have, STAKE_COLUMNS)
            if diffs:
                plan.stake_updates.append((key, diffs))
    seeded_keys = {stake_key(r) for r in seed["stakes"]}
    plan.orphan_stakes = sorted((k for k in existing_s if k not in seeded_keys), key=repr)
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


def fetch_existing(sb: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Read both tables (every seeded column). Raises on a read failure — an unreadable
    table must never look like an empty one, or every row would be planned as an insert."""
    companies = sb.table(COMPANIES_TABLE).select(",".join(COMPANY_COLUMNS)).execute().data
    stakes = sb.table(STAKES_TABLE).select(",".join(STAKE_COLUMNS)).execute().data
    if not isinstance(companies, list) or not isinstance(stakes, list):
        raise RuntimeError("unexpected response shape reading the trillion club tables")
    return companies, stakes


def apply_plan(sb: Any, plan: SyncPlan, seed: Mapping[str, Sequence[Mapping[str, Any]]], *,
               update: bool, existing_slugs: Sequence[str]) -> Tuple[int, int]:
    """Write the plan: inserts always, drifted columns only when ``update``. Companies go
    first (stakes reference them). Each row is isolated: one failure is logged with its
    identifiers and the rest carry on. Returns (rows written, errors)."""
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
        for key, diffs in plan.stake_updates:
            payload = {c: seed_s[key][c] for c in diffs}
            payload["updated_at"] = now
            label = f"{key[0]} / {key[1]} ({key[2]})"
            try:
                (sb.table(STAKES_TABLE).update(payload)
                   .eq("company_slug", key[0]).eq("investee_name", key[1]).eq("kind", key[2])
                   .execute())
                written += 1
                logger.info("  updated stake %s: %s", label, sorted(diffs))
            except Exception as e:
                errors += 1
                logger.error("  FAILED to update stake %s: %s: %s", label, type(e).__name__, e)
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
        logger.error("Could not read the trillion club tables (%s: %s). Is migration 175 applied?",
                     type(e).__name__, e)
        return 1
    plan = plan_sync(seed, db_companies, db_stakes)
    report_plan(plan)
    logger.info(
        "Plan: +%d companies, ~%d drifted companies, +%d stakes, ~%d drifted stakes, "
        "%d orphan companies, %d orphan stakes",
        len(plan.company_inserts), len(plan.company_updates), len(plan.stake_inserts),
        len(plan.stake_updates), len(plan.orphan_companies), len(plan.orphan_stakes))

    if not args.apply:
        logger.info("DRY RUN — nothing written. Re-run with --apply (and --update to overwrite drift).")
        return 0
    if plan.company_updates or plan.stake_updates:
        if args.update:
            logger.warning("--update: the JSON's values overwrite the drifted columns listed above.")
        else:
            logger.warning("Drifted rows were left as they are (pass --update to overwrite them).")
    written, errors = apply_plan(sb, plan, seed, update=args.update,
                                 existing_slugs=[r.get("slug") for r in db_companies])
    logger.info("Done. written=%d errors=%d", written, errors)
    return 1 if errors else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    sys.exit(main())
