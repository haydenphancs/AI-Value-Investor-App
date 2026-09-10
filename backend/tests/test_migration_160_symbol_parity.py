"""Migration 160's symbol table must not drift from the code that is its authority.

The migration hardcodes a symbol map in SQL. `asset_class.canonical_stored_symbol` decides
the same thing in Python for every NEW row. If the two disagree, migrated rows and new rows
are spelled differently — which is the ambiguity the migration exists to remove, reappearing
by a different route.

An earlier draft enumerated 16 symbols in four IN-lists. `canonical_stored_symbol` converts
across all 111 keys of `SYMBOL_TO_COINGECKO_ID` whenever the caller DECLARES crypto, so a
pre-existing `('SUI', asset_type='crypto')` alert stayed bare — and SUI is Sun Communities on
FMP, so that rule would evaluate a REIT's share price against a threshold set for the coin.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
from app.services.asset_class import _BARE_CRYPTO_SYMBOLS, canonical_stored_symbol

_SQL = (Path(__file__).resolve().parents[1]
        / "database" / "migrations" / "160_canonicalise_bare_crypto_symbols.sql")

# ('BTC','BTCUSD','Bitcoin',true)  /  ('1INCH','1INCHUSD',NULL,false)
_ROW = re.compile(
    # `[,;]?` — the LAST row ends `);`, not `),`. Without it the guard silently drops one
    # symbol and still "passes" a set comparison that is one short.
    r"^\s*\('([A-Z0-9]+)','([A-Z0-9]+USD)',(NULL|'(?:[^']|'')*'),(true|false)\)[,;]?\s*$"
)


def _rows():
    """Parse the VALUES block. Comment-stripped — the header names these symbols in prose."""
    out = {}
    for line in _SQL.read_text(encoding="utf-8").splitlines():
        code = line.split("--", 1)[0]
        m = _ROW.match(code)
        if m:
            out[m.group(1)] = {
                "pair": m.group(2),
                "name": None if m.group(3) == "NULL" else m.group(3)[1:-1].replace("''", "'"),
                "guessable": m.group(4) == "true",
            }
    return out


def test_the_migration_parses_at_all():
    rows = _rows()
    assert rows, "no VALUES rows parsed — the guard is stale, not the migration"


def test_the_symbol_set_is_exactly_the_coingecko_map():
    """111 keys: the exact set `canonical_stored_symbol` will convert for a declared-crypto
    caller. Fewer leaves rows behind; more invents a pair the price router cannot resolve."""
    assert set(_rows()) == set(SYMBOL_TO_COINGECKO_ID)


def test_bare_guessable_is_exactly_the_bare_crypto_list():
    """The 16 where a bare ticker may be GUESSED to mean the coin with no declared type.
    Widening this is how a listed security gets silently converted."""
    guessable = {s for s, r in _rows().items() if r["guessable"]}
    assert guessable == set(_BARE_CRYPTO_SYMBOLS)


def test_every_pair_matches_what_the_write_path_would_store():
    """The migration and the code must agree character-for-character, or a migrated row and
    a newly-added one are different strings for the same asset."""
    mismatches = [
        (bare, r["pair"], canonical_stored_symbol(bare, "crypto"))
        for bare, r in _rows().items()
        if canonical_stored_symbol(bare, "crypto") != r["pair"]
    ]
    assert mismatches == [], mismatches


def test_a_guessable_symbol_canonicalises_with_no_declared_type():
    """That is what "guessable" MEANS — the migration makes this guess for watchlist rows,
    which carry no usable asset_type."""
    for bare in (s for s, r in _rows().items() if r["guessable"]):
        assert canonical_stored_symbol(bare, None) == bare + "USD", bare


def test_a_non_guessable_symbol_stays_bare_with_no_declared_type():
    """The other 95 must NOT be converted on a guess — several are real listed securities."""
    for bare in (s for s, r in _rows().items() if not r["guessable"]):
        assert canonical_stored_symbol(bare, None) == bare, bare


def test_the_watchlist_arms_are_bounded_to_guessable_and_pre_cutoff():
    """Both are load-bearing. Without `bare_guessable` the migration guesses for symbols the
    code will not; without the cutoff a re-run converts a security the user deliberately
    re-added bare — the file's own header invites exactly that."""
    sql = "\n".join(l.split("--", 1)[0] for l in _SQL.read_text(encoding="utf-8").splitlines())
    watchlist_stmts = [c for c in sql.split(";") if "watchlist_items" in c and
                       ("UPDATE watchlist_items" in c or "DELETE FROM watchlist_items" in c)]
    assert watchlist_stmts, "no watchlist statements found — guard is stale"
    for stmt in watchlist_stmts:
        assert "bare_guessable" in stmt, stmt.strip()[:120]
        assert "added_at <" in stmt, stmt.strip()[:120]


def test_the_price_alert_arms_are_gated_on_asset_type_not_on_guessable():
    """price_alerts carries a trustworthy asset_type, so it needs no guess and must cover all
    111 — restricting it to the 16 is what left the SUI alert priced off Sun Communities."""
    sql = "\n".join(l.split("--", 1)[0] for l in _SQL.read_text(encoding="utf-8").splitlines())
    stmts = [c for c in sql.split(";")
             if "price_alerts" in c and ("UPDATE price_alerts" in c or "DELETE FROM price_alerts" in c)]
    assert stmts, "no price_alerts statements found — guard is stale"
    for stmt in stmts:
        assert "asset_type" in stmt and "'crypto'" in stmt, stmt.strip()[:120]
        assert "bare_guessable" not in stmt, (
            "price_alerts must not be limited to the 16-symbol guess set"
        )


def test_portfolio_arms_follow_the_watchlist_rather_than_their_own_bound():
    """portfolio_items has its own added_at, and a position can join a group long after the
    watchlist row was created — bounding it by its own timestamp lets the two diverge, which
    is the orphan-then-purge data loss the section exists to prevent."""
    sql = "\n".join(l.split("--", 1)[0] for l in _SQL.read_text(encoding="utf-8").splitlines())
    stmts = [c for c in sql.split(";")
             if "portfolio_items" in c and ("UPDATE portfolio_items" in c
                                            or "DELETE FROM portfolio_items" in c)]
    assert stmts, "no portfolio_items statements found — guard is stale"
    for stmt in stmts:
        assert "watchlist_items" in stmt, "portfolio arm does not consult the watchlist"
        assert "NOT EXISTS" in stmt
        assert "p.added_at" not in stmt and "a.added_at" not in stmt


def test_the_curated_names_match_the_app():
    """A migrated row's company_name is what the user reads. It must not disagree with the
    name the app shows for the same coin."""
    from app.services.crypto_service import _CRYPTO_PROFILES

    for bare, r in _rows().items():
        curated = _CRYPTO_PROFILES.get(bare, {}).get("name")
        if curated and r["name"]:
            assert r["name"] == curated, (bare, r["name"], curated)
