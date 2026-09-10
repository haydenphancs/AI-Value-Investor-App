"""`asc_apply_metadata.py` may only publish copy that `app-store-listing.md` actually contains.

Two files describe the App Store description and only one of them reaches Apple:

  * `documents/legal/app-store-listing.md` — the source of truth. It carries the reasoning for
    every paragraph and a MEASURED character count against the 4000 limit with ~106 characters
    of headroom.
  * `backend/scripts/asc_apply_metadata.py` — the delivery mechanism. It patches the LIVE
    description in App Store Connect by verbatim string replacement.

They diverged silently once. The doc's "FREE TO BROWSE" section was rewritten on 2026-09-07
when the app went account-only; the script was drafted separately and would have published a
heading (`FREE TO START`) and a paragraph that appear nowhere in the doc — so the listing the
team reviews and the listing Apple shows would have been different texts. Nothing caught it,
because the script cannot run without `ASC_ISSUER_ID` and the doc is never executed.

This test is the thing that catches it. It reads the script with `ast` rather than importing
it: importing executes `asc_audit.py`, which builds an authenticated client.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "backend" / "scripts" / "asc_apply_metadata.py"
_DOC = _REPO / "documents" / "legal" / "app-store-listing.md"

_LIMIT = 4000


def _script_constants() -> dict[str, str]:
    """The module-level string constants, without importing the module."""
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            continue
        if isinstance(value, str):
            out[target.id] = value
    return out


def _described_text() -> str:
    """The fenced App Store description from the listing doc."""
    doc = _DOC.read_text(encoding="utf-8")
    match = re.search(r"^## Description \(4000 max\)\s*\n+```\n(.*?)\n```", doc, re.S | re.M)
    assert match, "the '## Description (4000 max)' fenced block is gone from app-store-listing.md"
    return match.group(1)


def _unwrap(paragraph_block: str) -> str:
    """Undo the doc's ~95-column hard wrapping; keep paragraph breaks."""
    paragraphs = re.split(r"\n\s*\n", paragraph_block.strip())
    return "\n\n".join(" ".join(p.split()) for p in paragraphs)


def test_the_script_publishes_the_heading_the_doc_documents():
    consts = _script_constants()
    heading = consts["_NEW_DESC_HEADING"]
    assert heading in _described_text(), (
        f"asc_apply_metadata.py would publish the heading {heading!r}, which does not appear in "
        f"app-store-listing.md. The doc is the source of truth — change the script, or change "
        f"both."
    )


def test_the_script_publishes_the_body_the_doc_documents():
    consts = _script_constants()
    heading = consts["_NEW_DESC_HEADING"]
    body = consts["_NEW_DESC_BODY"]

    text = _described_text()
    after_heading = text.split(heading, 1)[1]
    # The section runs to the next ALL-CAPS heading line.
    section = re.split(r"\n\s*\n(?=[A-Z][A-Z' ]{3,}\s*\n)", after_heading, maxsplit=1)[0]

    assert _unwrap(section) == _unwrap(body), (
        "asc_apply_metadata.py's _NEW_DESC_BODY is not the text under that heading in "
        "app-store-listing.md.\n\n"
        f"--- script ---\n{_unwrap(body)}\n\n--- doc ---\n{_unwrap(section)}"
    )


def test_free_to_browse_never_comes_back():
    """The account-only wall makes that sentence a reviewer-visible contradiction."""
    text = _described_text()
    assert "FREE TO BROWSE" not in text
    assert "free to browse" not in text.lower(), (
        "app-store-listing.md promises browsing without an account, but every market-data "
        "route answers 401 — see .claude/rules/auth.md §1a. This is rejection cause #1."
    )


def test_the_script_still_targets_the_live_text_it_expects_to_find():
    """`_OLD_*` describe what is LIVE in ASC, not what the doc says — they must not be
    'helpfully' updated to match the doc, or the replacement silently becomes a no-op."""
    consts = _script_constants()
    assert consts["_OLD_DESC_HEADING"] == "FREE TO BROWSE"
    assert "need no account" in consts["_OLD_DESC_BODY"]
    assert consts["_OLD_DESC_HEADING"] != consts["_NEW_DESC_HEADING"]


def test_the_described_text_fits_the_store_limit():
    text = _described_text()
    assert len(text) <= _LIMIT, f"{len(text)} characters, limit {_LIMIT}"


@pytest.mark.parametrize(
    "phrase",
    [
        "not a broker-dealer",
        "executes no trades",
        "nothing in the app is financial advice",
    ],
)
def test_the_disclaimer_survives_any_rewrite(phrase: str):
    """The IMPORTANT block pre-empts the 5.1.1(ix) hook and is not trimmable filler."""
    assert phrase in _described_text().lower().replace("\n", " ")
