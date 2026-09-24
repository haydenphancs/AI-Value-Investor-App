"""
`backend/data/weekly_investor_quotes.json` — the vendored copy of the iOS bundle's weekly quotes.

The marketing compliance scan reads it for two things: every quote AUTHOR joins the person
lexicon (a real investor may never be named in a public post), and every quote TEXT feeds the
famous-quote n-gram check. Both only work if the copy is the same file the app ships:

* **Byte parity** with `frontend/ios/ios/Resources/InvestorQuotes/weekly_investor_quotes.json`
  (skipped when the iOS tree is not checked out, e.g. a backend-only deploy image).
* **Every author is caught by the scan** — by full name AND by the name without middle initials
  or a generational suffix ("John C. Bogle" → "John Bogle", "Thomas Rowe Price Jr." → "Thomas
  Rowe Price"), which is how a model would actually write it. Asserted through `scan_text`, not
  only by lexicon membership, so the word-bounded regex is what is tested.
* **Every quote is caught verbatim** by the famous-quote check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.services.marketing import compliance

_BACKEND = Path(__file__).resolve().parents[1]
_VENDORED = _BACKEND / "data" / "weekly_investor_quotes.json"
_IOS = _BACKEND.parent / "frontend" / "ios" / "ios" / "Resources" / "InvestorQuotes" / "weekly_investor_quotes.json"

_SUFFIXES = {"jr.", "jr", "sr.", "sr", "ii", "iii", "iv"}


def _quotes() -> List[Dict[str, Any]]:
    data = json.loads(_VENDORED.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and isinstance(data.get("quotes"), list)
    return data["quotes"]


def _authors() -> List[str]:
    return sorted({str(q.get("author") or "").strip() for q in _quotes()} - {""})


def _plain_name(author: str) -> str:
    """Drop middle initials ("C.", "G.") and generational suffixes ("Jr.")."""
    parts = [p for p in author.split()
             if not re.fullmatch(r"[A-Z]\.", p) and p.lower() not in _SUFFIXES]
    return " ".join(parts)


def test_the_vendored_file_is_byte_identical_to_the_ios_bundle():
    if not _IOS.exists():
        pytest.skip("iOS bundle not present in this checkout")
    assert _VENDORED.read_bytes() == _IOS.read_bytes(), (
        "backend/data/weekly_investor_quotes.json drifted from the iOS bundle — re-copy it"
    )


def test_the_vendored_file_is_well_formed_and_not_trivially_small():
    quotes = _quotes()
    assert len(quotes) >= 20, len(quotes)
    for q in quotes:
        assert isinstance(q, dict)
        assert str(q.get("text") or "").strip(), q
        assert str(q.get("author") or "").strip(), q
    assert len(_authors()) >= 10
    # The compliance module reads the same rows.
    assert len(compliance.investor_quotes()) == len(quotes)


def test_every_author_is_in_the_person_lexicon():
    lexicon = set(compliance.person_lexicon())
    missing = [a for a in _authors() if compliance.fold(a) not in lexicon]
    assert not missing, missing


@pytest.mark.parametrize("author", _authors())
def test_every_author_is_caught_by_the_scan_by_full_name(author):
    codes = {v.code for v in compliance.scan_text("caption", f"A lesson {author} liked to teach.")}
    assert "person_named" in codes, author


@pytest.mark.parametrize("author", sorted({_plain_name(a) for a in _authors()}))
def test_every_author_is_caught_by_the_scan_without_initials_or_suffix(author):
    codes = {v.code for v in compliance.scan_text("caption", f"A lesson {author} liked to teach.")}
    assert "person_named" in codes, author


def test_every_quote_is_caught_verbatim_by_the_famous_quote_check():
    missed = []
    for q in _quotes():
        text = str(q["text"])
        if len(re.findall(r"[A-Za-z0-9']+", text)) < 6:
            continue  # shorter than the n-gram; cannot be matched by design
        codes = {v.code for v in compliance.scan_text("caption", compliance.clean(text), allow_emoji=True)}
        if "famous_quote" not in codes:
            missed.append(text[:80])
    assert not missed, missed
