"""
Whale registry (data/whale_registry.json) integrity — the seed data's contract.

The sync script is NAME-keyed and additive: a duplicate name silently merges
two whales into one row (last write wins), and a duplicate CIK now violates
the uq_whales_cik unique index (migration 080) and aborts the sync loudly.
These tests make both impossible to commit, plus pin the post-merge shape
(person-fronted investors ALWAYS carry a firm_name).

Run via `python -m pytest` from backend/.
"""

import json
import os
import re
from collections import Counter

_REGISTRY = os.path.join(os.path.dirname(__file__), "..", "data", "whale_registry.json")


def _load():
    with open(_REGISTRY) as f:
        return json.load(f)


def test_no_duplicate_names():
    # Sync upserts by exact name — duplicates silently collapse into one row.
    names = [e["name"] for e in _load()]
    dupes = {n: c for n, c in Counter(names).items() if c > 1}
    assert not dupes, f"duplicate names would corrupt the name-keyed sync: {dupes}"


def test_no_duplicate_ciks():
    # One whale per CIK since migration 080 (uq_whales_cik) — a duplicate here
    # would abort the sync mid-run on the unique index.
    ciks = [e["cik"] for e in _load() if e.get("cik")]
    dupes = {c: n for c, n in Counter(ciks).items() if n > 1}
    assert not dupes, f"duplicate CIKs violate uq_whales_cik: {dupes}"


def test_13f_entries_have_valid_cik_and_no_fmp_name():
    for e in _load():
        if e["data_source"] == "13f":
            assert re.fullmatch(r"\d{10}", e.get("cik") or ""), (
                f"{e['name']}: 13F entry needs a 10-digit zero-padded CIK"
            )
            assert not e.get("fmp_name"), (
                f"{e['name']}: fmp_name is congress-only routing metadata"
            )


def test_politicians_have_fmp_name_and_no_cik():
    for e in _load():
        if e["category"] == "politicians":
            assert e.get("cik") is None, f"{e['name']}: politicians have no CIK"
            assert (e.get("fmp_name") or "").strip(), (
                f"{e['name']}: politicians need fmp_name for FMP congressional lookups"
            )
            assert e["data_source"] in ("congressional_house", "congressional_senate")


def test_every_investor_is_person_fronted_with_firm():
    # Post-merge display contract: the person's name NEVER appears without
    # their firm. A blank/whitespace firm_name would render an empty line.
    for e in _load():
        if e["category"] == "investors":
            firm = (e.get("firm_name") or "").strip()
            assert firm, f"{e['name']}: investor entry missing firm_name"


def test_institutions_have_no_firm_name():
    # Firm-branded entries: the name IS the firm; a firm_name would render
    # a duplicated subtitle under itself.
    for e in _load():
        if e["category"] == "institutions":
            assert not (e.get("firm_name") or "").strip(), (
                f"{e['name']}: institutions must not carry firm_name"
            )


def test_categories_and_sources_are_known_values():
    for e in _load():
        assert e["category"] in ("investors", "institutions", "politicians"), e["name"]
        assert e["data_source"] in (
            "13f", "congressional_house", "congressional_senate"
        ), e["name"]
        assert (e.get("name") or "").strip() and (e.get("title") or "").strip()
