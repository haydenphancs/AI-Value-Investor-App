"""What a valid ``trillion_club_stakes`` row is — ONE copy, pure.

Two writers put stake rows in front of users as SOURCED FACT, and both must refuse the same
rows:

* the owner-run seed, ``scripts/seed_trillion_club.py``, validates every JSON row with
  ``stake_problems`` before anything is written to production;
* the stake finder (gate 5) runs the same ``stake_problems`` over the 21-column
  ``STAKE_COLUMNS`` projection of a row it found in an SEC filing, before writing it.

These rules lived in the script until 2026-10-28. They moved here unchanged so the finder can
import them without loading a script; the script re-exports every moved name as the SAME
object (``tests/test_trillion_club_seed_integrity.py`` pins the identity, and that the script
never re-declares one). ``validate_seed`` stays in the script.

What is checked: migration 175's CHECK constraints, the seed policies and the copy rules
(``copy_rules``: banned wording, no forecasts, a 90-character past-tense background). The small
helpers (``_text_ok``, ``_finite_number``, ``_is_https`` …) also check the seed's COMPANY rows:
the script's ``company_problems`` imports them back, so a rule both tables share lives once.

Imports: the standard library, ``app.schemas.trillion_club`` and ``copy_rules`` only (an AST
test pins the set) — no Supabase, no FMP, no settings, so the seed validates without
credentials and the finder's check does no I/O.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from app.schemas.trillion_club import STAKE_KINDS, VALUE_BASES
# ONE copy of the copy rules, shared with the request path (trillion_club_service), so a
# Studio edit the seed would refuse can never be served.
from app.services.trillion_club.copy_rules import (
    STAKE_TEXT_FIELDS,
    contains_banned_copy,
    contains_forecast,
)

# ── The contract: exactly the hand-kept columns of migration 175 ────────────────────────
# tests/test_trillion_club_seed_integrity.py parses the migration and pins these against it.
# Migration 205's columns (origin, finding_id, owner_kept_at, withdrawn_at, investee_cik) are
# written by the stake finder and are never part of a seeded row.
STAKE_COLUMNS: Tuple[str, ...] = (
    "company_slug", "kind", "investee_name", "investee_cusip", "investee_us_symbol",
    "local_listing", "ownership_pct", "ownership_basis", "disclosed_value_usd", "value_basis",
    "as_of", "source_title", "source_url", "source_confidence", "material", "tied_to_deal",
    "listed_since", "background", "verified_on", "published", "sort_order",
)
STAKE_KEY: Tuple[str, str, str] = ("company_slug", "investee_name", "kind")

SOURCE_CONFIDENCES: Tuple[str, ...] = ("primary", "secondary")

INVESTEE_NAME_MAX = 60
SOURCE_TITLE_MAX = 120
BACKGROUND_MAX = 90
# sort_order is INTEGER (int4): a larger value validates but Postgres refuses it (22003)
# half-way through --apply, after earlier rows were written.
INT4_MAX = 2 ** 31 - 1

# `re.fullmatch` everywhere: `^...$` with re.match accepts a trailing "\n".
_CUSIP_RE = re.compile(r"[0-9A-Z]{9}")
# A US ticker or an exchange-suffixed one (BRK-B, 2222.SR, 005930.KS).
_SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# Text Postgres cannot store: NUL is refused in any TEXT (22P05) and a lone UTF-16 surrogate
# cannot be encoded as UTF-8 (22P02). json.load happily returns both from "\u0000" / "\ud800".
_UNSTORABLE_CHAR = re.compile(r"[\x00\ud800-\udfff]")
# One-line display text (names, titles, background): no control or format characters. A
# zero-width space (U+200B, category Cf) makes "Adv Holdings Ltd" a different key that
# renders identically; a tab or newline breaks the one-line layout. The list names the
# company columns too: the seed script's company_problems uses the same check.
_INVISIBLE_CATEGORIES = ("Cc", "Cf", "Cs")
_DISPLAY_TEXT_COLUMNS: Tuple[str, ...] = (
    "display_name", "manual_fx_source", *STAKE_TEXT_FIELDS,
)


# ── Small helpers (shared with the seed script's company_problems) ───────────────────────


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


# ── The stake validator ──────────────────────────────────────────────────────────────────


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


# ── The natural key ──────────────────────────────────────────────────────────────────────


def stake_key(row: Mapping[str, Any]) -> Tuple[Any, Any, Any]:
    return tuple(row.get(c) for c in STAKE_KEY)  # type: ignore[return-value]


def _folded_stake_key(row: Mapping[str, Any]) -> Optional[Tuple[str, str, str]]:
    """The stake's key as a reader sees it (see `_fold_name`); None when a key column is not
    a string (a hand-edit put a list or an object there — reported by stake_problems)."""
    slug, name, kind = stake_key(row)
    if not (isinstance(slug, str) and isinstance(name, str) and isinstance(kind, str)):
        return None
    return slug, _fold_name(name), kind
