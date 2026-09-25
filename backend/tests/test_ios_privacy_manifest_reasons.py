"""Each binary's PrivacyInfo.xcprivacy must declare every required-reason API it compiles.

WHY THIS FILE EXISTS. Since 1 May 2024 App Store Connect refuses an upload (ITMS-91053) whose
binary calls a "required reason" API its privacy manifest does not declare. Both manifests
here were hand-audited once and then left alone, and they drifted:

  * The app's header said file-timestamp APIs were "verified ABSENT". `LearnAudioCache` —
    added afterwards — touches `.modificationDate` and reads `.contentModificationDateKey`
    for LRU eviction. Found by the 2026-09-24 pre-resubmission audit, not by any test.
  * Both manifests declared the App Group suite as CA92.1, with Apple's two definitions
    reversed in the comment. CA92.1 = defaults "only accessible to the app itself";
    1C8F.1 = defaults shared by members of the same App Group.

The scan is per TARGET, and a target's sources are read from `fileSystemSynchronizedGroups`
in project.pbxproj rather than hard-coded: `Shared/` is compiled into BOTH binaries, and a
per-folder rule would miss that the app writes the App Group suite through it.

Token lists are Apple's published symbols for each category (a subset where the rest cannot
occur in Swift source). Comments are stripped first (testing.md §3 rule 1): the manifests'
own history is written into Swift comments next to these calls.
"""
from __future__ import annotations

import plistlib
import re
from pathlib import Path
from typing import Dict, List, Set

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios"
_PBXPROJ = _IOS / "ios.xcodeproj" / "project.pbxproj"

# target name -> folder holding that target's own PrivacyInfo.xcprivacy
_MANIFEST_FOLDER = {"ios": "ios", "CaydexWidgets": "CaydexWidgets"}

_CATEGORY_TOKENS: Dict[str, List[str]] = {
    "NSPrivacyAccessedAPICategoryFileTimestamp": [
        ".creationDate", ".modificationDate", "fileModificationDate",
        "contentModificationDateKey", "creationDateKey",
        "getattrlist(", "getattrlistbulk(", "fgetattrlist(", "fstatat(", "lstat(",
    ],
    "NSPrivacyAccessedAPICategorySystemBootTime": ["systemUptime", "mach_absolute_time("],
    "NSPrivacyAccessedAPICategoryDiskSpace": [
        "volumeAvailableCapacityKey", "volumeAvailableCapacityForImportantUsageKey",
        "volumeAvailableCapacityForOpportunisticUsageKey", "volumeTotalCapacityKey",
        ".systemFreeSize", ".systemSize", "statfs(", "statvfs(",
    ],
    "NSPrivacyAccessedAPICategoryActiveKeyboards": ["activeInputModes"],
}

# UserDefaults is split by WHICH defaults domain is touched, because the reason differs.
_OWN_DEFAULTS = ["UserDefaults.standard", "@AppStorage"]
_GROUP_DEFAULTS = ["UserDefaults(suiteName:"]


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.DOTALL)
    return "\n".join(
        "" if ln.strip().startswith("//") else re.sub(r"\s//.*$", "", ln)
        for ln in src.splitlines()
    )


def _target_folders() -> Dict[str, List[str]]:
    """target name -> synchronized source folders, read from project.pbxproj."""
    pbx = _PBXPROJ.read_text(encoding="utf-8")
    group_paths = dict(re.findall(
        r"(\w+) /\* [^*]+ \*/ = \{\s*isa = PBXFileSystemSynchronizedRootGroup;.*?path = ([^;]+);",
        pbx, flags=re.DOTALL,
    ))
    out: Dict[str, List[str]] = {}
    for groups, name in re.findall(
        r"fileSystemSynchronizedGroups = \((.*?)\);\s*name = ([^;]+);", pbx, flags=re.DOTALL
    ):
        ids = re.findall(r"(\w+) /\*", groups)
        out[name.strip()] = [group_paths[i].strip() for i in ids if i in group_paths]
    return out


def _swift_sources(folders: List[str]) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for folder in folders:
        for p in (_IOS / folder).rglob("*.swift"):
            files[str(p.relative_to(_IOS))] = _strip_swift_comments(p.read_text(encoding="utf-8"))
    return files


def _declared(target: str) -> Dict[str, Set[str]]:
    path = _IOS / _MANIFEST_FOLDER[target] / "PrivacyInfo.xcprivacy"
    assert path.exists(), f"{path} is missing — every binary needs its own manifest"
    with path.open("rb") as fh:
        data = plistlib.load(fh)
    return {
        e["NSPrivacyAccessedAPIType"]: set(e.get("NSPrivacyAccessedAPITypeReasons", []))
        for e in data.get("NSPrivacyAccessedAPITypes", [])
    }


def _uses(sources: Dict[str, str], tokens: List[str]) -> List[str]:
    return sorted({f"{f}: {t}" for f, s in sources.items() for t in tokens if t in s})


def test_both_targets_are_found_with_their_folders():
    """Drift check — the rest of this file is vacuous if the pbxproj parse finds nothing."""
    folders = _target_folders()
    assert set(_MANIFEST_FOLDER) <= set(folders), folders
    assert "Shared" in folders["ios"] and "Shared" in folders["CaydexWidgets"], folders


@pytest.mark.parametrize("target", sorted(_MANIFEST_FOLDER))
@pytest.mark.parametrize("category", sorted(_CATEGORY_TOKENS))
def test_every_used_required_reason_category_is_declared(target: str, category: str):
    sources = _swift_sources(_target_folders()[target])
    hits = _uses(sources, _CATEGORY_TOKENS[category])
    if hits:
        declared = _declared(target)
        assert declared.get(category), (
            f"{target} uses {category} but its PrivacyInfo.xcprivacy does not declare it "
            f"(ITMS-91053 rejects the upload):\n  " + "\n  ".join(hits[:10])
        )


@pytest.mark.parametrize("target", sorted(_MANIFEST_FOLDER))
def test_user_defaults_reasons_match_the_domains_touched(target: str):
    sources = _swift_sources(_target_folders()[target])
    reasons = _declared(target).get("NSPrivacyAccessedAPICategoryUserDefaults", set())
    own, group = _uses(sources, _OWN_DEFAULTS), _uses(sources, _GROUP_DEFAULTS)
    if own:
        assert "CA92.1" in reasons, f"{target} uses its own defaults but lacks CA92.1:\n  {own[:5]}"
    if group:
        assert "1C8F.1" in reasons, (
            f"{target} uses the App Group suite but lacks 1C8F.1 (CA92.1 covers only the "
            f"app's OWN defaults):\n  {group[:5]}"
        )
    if not own:
        assert "CA92.1" not in reasons, f"{target} declares CA92.1 but never touches its own defaults"


def test_learn_audio_cache_is_what_needs_the_file_timestamp_declaration():
    """Pins the specific finding, so a refactor that moves the cache is noticed here."""
    hits = _uses(_swift_sources(_target_folders()["ios"]),
                 _CATEGORY_TOKENS["NSPrivacyAccessedAPICategoryFileTimestamp"])
    assert any("LearnAudioCache.swift" in h for h in hits), hits
    assert "C617.1" in _declared("ios")["NSPrivacyAccessedAPICategoryFileTimestamp"]


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────────────
#
# Hand-run 2026-09-24 (each applied, suite run, reverted):
#  1. ios/PrivacyInfo.xcprivacy: removed the FileTimestamp entry
#       -> test_every_used_...[FileTimestamp-ios] and the LearnAudioCache pin FAILED ✅
#  2. ios/PrivacyInfo.xcprivacy: removed 1C8F.1
#       -> test_user_defaults_reasons_match_the_domains_touched[ios] FAILED ✅
#  3. CaydexWidgets manifest: 1C8F.1 back to CA92.1 (the original, reversed state)
#       -> test_user_defaults_reasons_match_the_domains_touched[CaydexWidgets] FAILED ✅
