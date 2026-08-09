"""Every linked Swift Package must be attributed.

`AcknowledgementsView` listed only sentry-cocoa while the app also links GoogleSignIn-iOS and
its seven transitive dependencies — all Apache-2.0, whose §4(a) requires the licence to travel
with an object-form redistribution. A shipped binary is exactly that, so the eight missing
entries were a compliance gap rather than a courtesy one. (§4(d), the NOTICE file, does not
apply: none of these checkouts ships one.)

"Keep this list in sync when you add a dependency" is what failed: a TRANSITIVE package arrives
without anyone editing that file at all. So this test DERIVES the expected set from
`Package.resolved` instead of restating it — per `project_source_scan_guard_vacuity`, prefer
enumeration over an enumerated list, or the guard drifts with the thing it guards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_IOS = _ROOT / "frontend" / "ios"
_ACK = _IOS / "ios" / "Views" / "Screens" / "AcknowledgementsView.swift"


def _resolved_packages() -> dict[str, str]:
    """{identity: location} for every pinned Swift Package."""
    matches = list(_IOS.rglob("Package.resolved"))
    if not matches:
        pytest.skip("no Package.resolved checked in")
    pins: dict[str, str] = {}
    for path in matches:
        data = json.loads(path.read_text(encoding="utf-8"))
        for pin in data.get("pins", []):
            pins[pin["identity"]] = pin.get("location", "")
    return pins


def _ack_source() -> str:
    """The file's CODE, with whole-line comments blanked.

    ⚠️ Load-bearing, and it caught itself: the first version scanned the raw file, and the
    header comment explaining WHY the list must be complete names "GoogleSignIn-iOS" in prose.
    Deleting the actual `AcknowledgementItem` therefore left the assertion passing — the guard
    was satisfied by its own rationale. Verified by mutation.
    """
    if not _ACK.exists():
        pytest.skip(f"{_ACK} not present")
    return "\n".join(
        "" if line.strip().startswith("//") else line
        for line in _ACK.read_text(encoding="utf-8").splitlines()
    )


def test_there_is_something_to_check():
    """Anti-vacuity. If `Package.resolved` parsed to nothing, every assertion below is free."""
    pins = _resolved_packages()
    assert len(pins) >= 2, f"only {len(pins)} package(s) resolved — the parse is probably wrong"


def test_every_resolved_package_is_acknowledged():
    src = _ack_source().lower()
    missing = []
    for identity, location in _resolved_packages().items():
        # Match on the repo SLUG rather than the identity: SPM lowercases and normalises
        # identities ("googlesignin-ios"), while the display name a human writes is the
        # repository's own casing ("GoogleSignIn-iOS").
        slug = location.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1].lower()
        if slug not in src and identity.lower() not in src:
            missing.append(f"{identity} ({location})")
    assert not missing, (
        "linked Swift Packages with no acknowledgement entry: "
        + ", ".join(sorted(missing))
        + ". They ship inside the binary, so their licences ship with it."
    )


def test_every_acknowledgement_names_a_licence():
    """An entry with no licence is worse than no entry: it looks like compliance."""
    src = _ack_source()
    # One `license:` per `name:`; the struct has exactly those two fields plus a url.
    assert src.count("license:") == src.count("name:"), (
        "an AcknowledgementItem is missing its licence"
    )


def test_no_acknowledgement_is_a_placeholder():
    src = _ack_source().lower()
    for bad in ("tbd", "todo", "unknown license", "fixme"):
        assert bad not in src, f"placeholder {bad!r} left in the acknowledgements"
