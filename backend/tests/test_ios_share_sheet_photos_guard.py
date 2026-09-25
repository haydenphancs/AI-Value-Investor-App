"""The share sheet must never offer "Save Image" to an app that cannot write to Photos.

WHY. `UIActivityViewController` offers "Save Image" whenever an item is an image, and iOS
terminates the app when that writes to Photos without `NSPhotoLibraryAddUsageDescription`.
Caydex declares no such key, and the Help Us Improve bug-report path (MailUnavailableCard →
ShareSheet) passes the user's attached `UIImage` — a one-tap crash on a device without Mail.
Found by the 2026-09-24 pre-resubmission audit.

The fix lives in the `ShareSheet` atom (every share site in the app goes through it), gated
on the plist key so that declaring it later re-enables the action. Comment-stripped and
brace-bound per testing.md §3.
"""
from __future__ import annotations

import plistlib
import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios"
_ATOM = _IOS / "ios" / "Views" / "Atoms" / "ShareSheet.swift"
_PLIST = _IOS / "ios" / "Info.plist"
_PBX = _IOS / "ios.xcodeproj" / "project.pbxproj"


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return "\n".join(
        "" if ln.strip().startswith("//") or ln.strip().startswith("///")
        else re.sub(r"\s//.*$", "", ln)
        for ln in src.splitlines()
    )


def _block(src: str, header: str) -> str:
    start = src.index(header)
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[i : j + 1]
    raise AssertionError(f"unbalanced after {header!r}")


def _app_declares_photos_add() -> bool:
    with _PLIST.open("rb") as fh:
        if "NSPhotoLibraryAddUsageDescription" in plistlib.load(fh):
            return True
    return "INFOPLIST_KEY_NSPhotoLibraryAddUsageDescription" in _PBX.read_text(encoding="utf-8")


def test_share_sheet_excludes_save_image_when_photos_add_is_undeclared():
    if _app_declares_photos_add():
        return  # Save Image is safe; nothing to guard.
    atom = _strip(_ATOM.read_text(encoding="utf-8"))
    make = _block(atom, "func makeUIViewController")
    assert "effectiveExclusions(" in make, (
        "ShareSheet.makeUIViewController no longer routes through effectiveExclusions — "
        "'Save Image' would crash the app (no NSPhotoLibraryAddUsageDescription)."
    )
    assert "NSPhotoLibraryAddUsageDescription" in make, "the gate must read the real plist key"
    helper = _block(atom, "static func effectiveExclusions")
    assert ".saveToCameraRoll" in helper, "effectiveExclusions must add .saveToCameraRoll"


def test_every_uiactivityviewcontroller_is_the_atom():
    """A second, hand-rolled share sheet would bypass the guard."""
    offenders = []
    for p in (_IOS / "ios").rglob("*.swift"):
        if p == _ATOM:
            continue
        if "UIActivityViewController(" in _strip(p.read_text(encoding="utf-8")):
            offenders.append(str(p.relative_to(_IOS)))
    assert not offenders, f"share sheets outside the ShareSheet atom: {offenders}"


# MUTATION_LOG (hand-run 2026-09-24): replaced the effectiveExclusions(...) call with the old
# `controller.excludedActivityTypes = excludedActivityTypes` -> first test FAILED ✅; removed
# `.saveToCameraRoll` from the helper -> FAILED ✅; restored.
