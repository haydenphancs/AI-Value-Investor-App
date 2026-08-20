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


# ── Bio copy: no magnitude claim beside a 13F-only tile ──────────────
#
# The Whale Profile renders `description` directly ABOVE two stat tiles that
# show a 13F-only portfolio value and a 13F-derived return. A bio quoting the
# firm's total AUM ("approximately $100B in total assets") or a headline fund
# return ("returned 132% in 2009") therefore sits inches from a number computed
# on a completely different basis — and readers understandably conclude the tile
# is wrong, or that it represents the person's whole fortune.
#
# The fix is the copy, not the tile: 13F genuinely cannot see bonds, cash,
# private holdings, foreign listings or shorts. This lint keeps the next bio
# edit from reintroducing the collision.

_MAGNITUDE_PATTERNS = (
    r"\bAUM\b",
    r"under management",
    r"in total assets",
    r"returned\s+(over\s+)?\d+%",
    r"\d+%\s+(average\s+)?annual",
    r"managing (approximately |roughly |over |about )?\$",
)

# Phrasings deliberately kept because they are SCOPE, not a performance or
# total-assets claim that competes with the tiles.
_ALLOWED = {
    # Ownership breadth of a sovereign fund — describes reach, not a return.
    "Norges Bank Investment Management",
    # Medallion's 66% is the single most famous number in the industry and the
    # bio would be odd without it. It is allowed ONLY because the sentence now
    # carries every qualifier the collision needs: the fund is named, stated as
    # closed to outside investors, stated as NOT the 13F book shown here,
    # attributed ("reported to"), bounded to 1988-2018, and marked before fees.
    # If that qualification is ever trimmed, remove this entry rather than
    # keeping the bare percent.
    "Renaissance Technologies",
}


def test_bios_do_not_put_a_magnitude_claim_next_to_the_13f_tiles():
    import re

    offenders = []
    for entry in _load():
        if entry.get("name") in _ALLOWED:
            continue
        description = entry.get("description") or ""
        for pattern in _MAGNITUDE_PATTERNS:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                start = max(0, match.start() - 50)
                offenders.append(
                    f"{entry.get('name')}: …{description[start:match.end() + 50]}…"
                )
                break

    assert not offenders, (
        "These bios quote total AUM or a headline fund return, which renders "
        "directly above a 13F-ONLY portfolio value and a 13F-derived return. "
        "Rewrite to describe scale or strategy without a competing number "
        "(e.g. 'one of the world's largest hedge funds by total assets'), or "
        "add the name to _ALLOWED with a reason:\n  " + "\n  ".join(offenders)
    )


def test_sync_reports_database_rows_missing_from_the_registry():
    """The sync is ADDITIVE and never deletes, so a row inserted by hand — or one whose
    registry entry was later removed — is served to users forever.

    This file lints the JSON only; it cannot see the database. Dan Crenshaw, Mark Kelly
    and Ted Cruz sat in production outside the registry until a manual audit compared the
    two, so the sync itself has to say something. Pinned here because a silent sync is
    exactly how the drift went unnoticed for months.

    Comments are stripped first: the explanation next to that code names every token
    this assertion looks for.
    """
    import re
    from pathlib import Path

    src = Path("scripts/sync_whale_registry.py").read_text(encoding="utf-8")
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"(?m)\s+#.*$", "", src)

    assert "registry_names" in src and "orphans" in src, (
        "sync_whale_registry.py must compare the database against the registry"
    )
    assert "logger.warning" in src, "drift must be reported at WARNING, not swallowed"
    # And it must NOT start deleting on its own: a registry typo would destroy a whale
    # and cascade its follows.
    assert '.delete()' not in src, (
        "the sync must never delete whales — report drift and let a human decide"
    )


# ── Curated lifecycle (migration 145) ────────────────────────────────────────


_ALLOWED_STATUS = {"active", "inactive"}


def test_lifecycle_status_uses_a_known_value():
    for whale in _load():
        st = whale.get("status")
        if st is None:
            continue
        assert isinstance(st, str) and st.strip().lower() in _ALLOWED_STATUS, (
            f"{whale['name']}: status={st!r} is not one of {sorted(_ALLOWED_STATUS)}"
        )


def test_a_non_active_whale_must_explain_why():
    """A bare "Inactive" badge invites exactly the question it fails to answer.

    The note is rendered VERBATIM to the user and overrides the derived label, so it has
    to carry the reason a human knows and the filing data cannot show.
    """
    for whale in _load():
        st = (whale.get("status") or "").strip().lower()
        if not st or st == "active":
            continue
        note = (whale.get("status_note") or "").strip()
        assert note, f"{whale['name']}: status={st!r} requires a status_note"
        assert len(note) > 25, (
            f"{whale['name']}: status_note is too short to be an explanation: {note!r}"
        )


def test_status_notes_carry_no_magnitude_claim():
    """Same lint as the bios: the note renders near the 13F stat tiles, so a return or
    AUM figure computed on a different basis would collide with them."""
    import re

    for whale in _load():
        note = (whale.get("status_note") or "").strip()
        if not note:
            continue
        for pattern in _MAGNITUDE_PATTERNS:
            assert not re.search(pattern, note, re.I), (
                f"{whale['name']}: status_note matches {pattern!r}: {note!r}"
            )


def test_an_active_whale_carries_no_stale_note():
    """A note left behind after a filer resumed would keep telling users it is dormant."""
    for whale in _load():
        st = (whale.get("status") or "").strip().lower()
        if st and st != "active":
            continue
        assert not (whale.get("status_note") or "").strip(), (
            f"{whale['name']}: is active but still carries a status_note"
        )
