"""`documents/legal/asc-iap-metadata.md` is typed into App Store Connect BY HAND. Pin it.

WHY THIS FILE EXISTS
--------------------
That page is the only artifact in the repo whose consumer is a human copying strings into a web
form. Nothing downstream validates what they type: if the page says a pack sells 90 credits for
$1.99 and the product actually grants 130 for $2.99, ASC accepts it, the purchase succeeds, and
the user is charged one thing and granted another. `LAUNCH_CHECKLIST.md` names that failure
("ASC and the table must agree or the user is charged one price and credited another") and until
now nothing enforced it for the DOCUMENT.

It is not hypothetical. The first draft of that page was written from migration **117** — which
had been superseded by **138** and then **141** — so all four packs were wrong: 90/$1.99 instead
of 130/$2.99, and so on down the ladder. `test_iap_product_and_privacy_parity.py` did not catch
it because that file pins the storekit config against the DB seed, and the doc is neither.

`frontend/ios/Caydex.storekit` is the source of truth on the other side of this assertion: it is
what Xcode simulates purchases against, and the sibling test already pins IT against the
effective `credit_packs` seed and against ASC's field limits. So doc == storekit == DB, and
every link in that chain is now a test.

No network, no Supabase.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "documents" / "legal" / "asc-iap-metadata.md"
_STOREKIT = _REPO / "frontend" / "ios" / "Caydex.storekit"


def _storekit() -> dict:
    # The file carries `//` comments, which are legal in Xcode's format and not in JSON.
    return json.loads(re.sub(r"//.*", "", _STOREKIT.read_text(encoding="utf-8")))


def _entries() -> dict[str, dict]:
    """product id -> {referenceName, displayName, description, displayPrice} for all six."""
    out: dict[str, dict] = {}
    d = _storekit()
    groups = d.get("subscriptionGroups") or []
    products = list(d.get("products") or [])
    for g in groups:
        products.extend(g.get("subscriptions") or [])
    for p in products:
        loc = (p.get("localizations") or [{}])[0]
        out[p["productID"]] = {
            "referenceName": p.get("referenceName", ""),
            "displayName": loc.get("displayName", ""),
            "description": loc.get("description", ""),
            "displayPrice": str(p.get("displayPrice", "")),
        }
    return out


@pytest.fixture(scope="module")
def doc() -> str:
    assert _DOC.exists(), f"{_DOC} is missing — it is the ASC submission checklist"
    return _DOC.read_text(encoding="utf-8")


def test_the_sources_are_readable_and_populated(doc):
    """Guard against the guard. An emptied storekit file or a moved doc would make every
    assertion below iterate nothing and pass."""
    entries = _entries()
    assert len(entries) == 6, f"expected 2 subscriptions + 4 packs, found {len(entries)}"
    assert len(doc) > 2000, "the doc is suspiciously short — did it get truncated?"


@pytest.mark.parametrize("product_id", sorted(_entries()))
def test_every_product_appears_with_its_exact_copy(doc, product_id):
    """Each of the six, with the display name, description and price a person must type."""
    e = _entries()[product_id]
    assert f"`{product_id}`" in doc, f"{product_id} is not in the ASC page at all"
    for field in ("displayName", "description"):
        value = e[field]
        assert f"`{value}`" in doc, (
            f"{product_id}: the page does not carry the exact {field} "
            f"{value!r} from Caydex.storekit — someone typing from this page would enter "
            f"something the app does not sell"
        )
    assert f"${e['displayPrice']}" in doc, (
        f"{product_id}: price ${e['displayPrice']} is missing from the page"
    )


def test_the_page_carries_no_superseded_pack_ladder(doc):
    """The specific way this went wrong: the page was written from a superseded migration.

    117's ladder (90/280… at $1.99–$19.99) and 138's must not reappear. Asserting on the OLD
    values rather than only on the new ones is what makes the test fail loudly if someone
    re-derives the page from the wrong migration, instead of it merely lacking the right ones.
    """
    # TABLE ROWS ONLY, not the whole page. The prose above the tables deliberately quotes the
    # superseded $1.99 to explain what went wrong, and a whole-file scan flags that as the bug
    # it is describing. What matters is what a person would COPY, which is only ever a table
    # cell — so that is what gets scanned.
    rows = "\n".join(l for l in doc.splitlines() if l.lstrip().startswith("|"))
    assert rows.count("\n") >= 8, "no product tables found — the scan would pass vacuously"

    current_prices = {e["displayPrice"] for e in _entries().values()}
    superseded = {"1.99", "4.99", "9.99", "19.99"}
    for price in sorted(superseded - current_prices):
        assert f"${price}" not in rows, (
            f"a table still quotes ${price}, which is migration 117's ladder — 141 is live. "
            "Regenerate the tables from Caydex.storekit."
        )


def test_the_group_display_name_matches(doc):
    """§1's whole point. A group name that disagrees with the storekit config means the
    blocker gets 'fixed' with the wrong string."""
    name = (_storekit().get("subscriptionGroups") or [{}])[0].get("name")
    assert name, "Caydex.storekit has no subscription group name"
    assert f"`{name}`" in doc, f"the page does not tell the reader to enter {name!r}"


def test_the_never_expire_claim_survives_in_every_pack_description(doc):
    """Guideline 3.1.1. `PaywallView.swift` promises it and `purchased_total` implements it;
    if ASC omits it, a reviewer reads the consumable as expiring."""
    for pid, e in _entries().items():
        if ".credits." not in pid:
            continue
        assert "never expire" in e["description"].lower(), (
            f"{pid}: Caydex.storekit itself dropped the claim — fix that first"
        )
    assert "Never expire" in doc


def test_the_page_still_names_the_hard_blocker(doc):
    """The group localization is the one item that makes submission impossible rather than
    merely wrong, and it is easy to lose in a checklist edit."""
    lowered = doc.lower()
    assert "subscription group display name" in lowered
    assert "not a subscription inside it" in lowered, (
        "the page no longer warns that the localization goes on the GROUP — doing the two "
        "subscriptions instead is the single most common way this blocker survives"
    )


# ── MUTATION_LOG ────────────────────────────────────────────────────────────
#
# Broken by hand, observed to fail, restored (`.claude/rules/testing.md` §3). 2026-09-07.
#
#  1. A pack price in the doc changed $2.99 -> $1.99 (the actual historical error)
#       -> test_every_product_appears_with_its_exact_copy FAILED ✅
#          and test_the_page_carries_no_superseded_pack_ladder FAILED ✅
#  2. A pack's credit count changed 130 -> 90 in the doc
#       -> test_every_product_appears_with_its_exact_copy FAILED ✅
#  3. Group name changed to `Caydex Plus`
#       -> test_the_group_display_name_matches FAILED ✅
#  4. The "(not a subscription inside it)" warning removed
#       -> test_the_page_still_names_the_hard_blocker FAILED ✅
#  5. `Caydex.storekit` itself dropped "Never expire." from a pack description
#       -> test_the_never_expire_claim_survives_in_every_pack_description FAILED ✅
#          (i.e. the guard checks the SOURCE, not just the doc's copy of it)
#  6. A subscription description shortened in the doc only
#       -> test_every_product_appears_with_its_exact_copy FAILED ✅
#
# Note on scope: `test_the_page_carries_no_superseded_pack_ladder` scans TABLE ROWS only.
# Its first draft scanned the whole page and failed on the prose that quotes $1.99 while
# explaining the original mistake — a false positive on the documentation of the very bug
# it guards. Only a table cell is ever copied into ASC, so only tables are scanned.
