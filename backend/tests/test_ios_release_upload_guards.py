"""Two things that only fail at `Distribute App`, hours after you think you are done.

WHY THIS FILE EXISTS. Both invariants below were BROKEN when it was written, both are
invisible to every other gate in this repo (the app compiles, the suite is green, the
simulator runs), and both cost a full App Store round-trip to discover.

  1. **ITMS-90473.** An app extension's `CFBundleVersion` must equal its container app's.
     Measured 2026-09-07: the app was at build **7** and `CaydexWidgets` at **4**, so the
     first upload of the release would have been rejected at validation. Nothing local can
     see this — the two targets build and run happily side by side, because the constraint
     is enforced by App Store Connect, not by Xcode. The same applies to `MARKETING_VERSION`.

  2. **The shipped base URL.** The production hostname exists in **three** independent
     copies (the app's `APIConfig`, the localhost-probe fallback in
     `ServerEnvironmentManager`, and `WidgetAPIConfig`, which cannot import the app target
     — see that file's header for why the duplication is deliberate). All three shipped
     `ai-value-investor-app-production.up.railway.app`, an incidental Railway hostname,
     while App Store Connect, the AASA and every legal document point at `caydexinvest.com`.
     A drifted widget copy is the worst case: the widget keeps calling a host the app has
     abandoned, and the only symptom is a widget that quietly stops updating.

Both are asserted structurally rather than against a hardcoded value, so a legitimate version
bump or hostname change passes as long as it is applied EVERYWHERE. The one literal this file
does pin is the negative: the Railway subdomain must not come back.

Per `.claude/rules/testing.md` §3 every scan is comment-stripped and bounded to the
declaration it means to check, and the file was mutation-tested by hand — see MUTATION_LOG.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS_ROOT = _REPO / "frontend" / "ios"
_PBXPROJ = _IOS_ROOT / "ios.xcodeproj" / "project.pbxproj"

_API_CONFIG = _IOS_ROOT / "ios" / "Core" / "Services" / "APIConfig.swift"
_SERVER_ENV = _IOS_ROOT / "ios" / "Core" / "Services" / "ServerEnvironmentManager.swift"
_WIDGET_CONFIG = _IOS_ROOT / "Shared" / "WidgetAPIConfig.swift"

# The hostname that must never ship again. Kept as the one hardcoded literal here because it
# is a NEGATIVE assertion — a positive one would have to be edited on every legitimate move.
_ABANDONED_HOST = "up.railway.app"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_swift_comments(src: str) -> str:
    r"""Drop `//` lines and trailing `//` tails, blanking rather than deleting.

    Testing.md rule 1, and it bites specifically here: the fix for each of these invariants
    left an explanatory comment NAMING the thing it fixed, so both `caydexinvest.com` and
    `up.railway.app` now appear in prose right next to the code. An un-stripped scan would
    stay green against a full revert, satisfied by the comment explaining the revert.

    `\s//` not `//`: a bare `//` mangles every `"https://…"` literal, which is the exact
    thing this file exists to read. Blanking preserves line numbers so failures stay quotable.
    """
    out = []
    for line in src.splitlines():
        out.append("" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _strip_pbx_comments(src: str) -> str:
    """`project.pbxproj` annotates nearly every reference with `/* Name */`."""
    return re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of the declaration starting at `header`.

    Testing.md rule 2. `APIConfig.swift` holds a localhost URL as well as the production one,
    so a file-wide scan for `https://` would read whichever literal happened to come first.
    """
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


# ── 1. ITMS-90473: the app and its widget must agree on version ──────────────────────


def _pbx_setting(name: str) -> list[str]:
    src = _strip_pbx_comments(_read(_PBXPROJ))
    return re.findall(rf"^\s*{re.escape(name)} = (.+?);\s*$", src, flags=re.MULTILINE)


def test_every_target_declares_the_same_build_number():
    """App build 7 + widget build 4 = ITMS-90473, rejected at upload validation."""
    values = _pbx_setting("CURRENT_PROJECT_VERSION")
    assert len(values) >= 4, (
        f"expected a CURRENT_PROJECT_VERSION per target/configuration, found {len(values)} — "
        "the scan has drifted from the project layout"
    )
    assert len(set(values)) == 1, (
        f"CURRENT_PROJECT_VERSION disagrees across targets: {sorted(set(values))}. "
        "An app extension's CFBundleVersion must equal the containing app's, or App Store "
        "Connect rejects the upload with ITMS-90473. Bump ALL slots together."
    )


def test_every_target_declares_the_same_marketing_version():
    values = _pbx_setting("MARKETING_VERSION")
    assert len(values) >= 4, f"found only {len(values)} MARKETING_VERSION slots"
    assert len(set(values)) == 1, (
        f"MARKETING_VERSION disagrees across targets: {sorted(set(values))}. "
        "The extension's CFBundleShortVersionString must match the app's."
    )


def test_every_target_declares_the_encryption_answer():
    """Absent, App Store Connect blocks the build on a compliance question at every upload.

    Bounded to the count of version slots rather than a hardcoded 4, so adding a target keeps
    the invariant honest instead of silently exempting the new one.
    """
    slots = len(_pbx_setting("CURRENT_PROJECT_VERSION"))
    answers = _pbx_setting("INFOPLIST_KEY_ITSAppUsesNonExemptEncryption")
    assert len(answers) == slots, (
        f"{slots} build-setting slots declare a version but only {len(answers)} answer "
        "ITSAppUsesNonExemptEncryption. Every target needs it or ASC asks at each upload."
    )
    assert set(answers) == {"NO"}, f"unexpected encryption answer: {sorted(set(answers))}"


# ── 2. All three shipped copies of the base URL agree ────────────────────────────────


def _production_host(path: Path, header: str) -> str:
    """The single https literal inside one declaration."""
    block = _decl_block(_strip_swift_comments(_read(path)), header)
    found = re.findall(r'"(https://[^"]+)"', block)
    assert len(found) == 1, (
        f"expected exactly one https literal in {path.name} → {header!r}, found {found}"
    )
    return found[0]


def test_the_three_shipped_base_urls_are_the_same_host():
    """`WidgetAPIConfig` cannot import the app target, so the duplication is permanent.

    That makes drift the default failure mode rather than an unlikely one: the widget's copy
    is edited by nobody, and a widget calling an abandoned host just stops updating.
    """
    app = _production_host(_API_CONFIG, "static var baseURL: URL")
    probe = _production_host(_SERVER_ENV, "let railwayURL = URL")
    widget = _production_host(_WIDGET_CONFIG, "static let productionBaseURL = URL")

    assert app == probe == widget, (
        "the shipped base URL has drifted between its three copies:\n"
        f"  APIConfig.baseURL                    = {app}\n"
        f"  ServerEnvironmentManager.railwayURL  = {probe}\n"
        f"  WidgetAPIConfig.productionBaseURL    = {widget}\n"
        "All three ship. Change them together."
    )


def test_the_abandoned_railway_subdomain_is_gone_from_shipped_swift():
    """Positive-and-negative pair: equality alone passes if all three revert together."""
    offenders = []
    for path in sorted(_IOS_ROOT.rglob("*.swift")):
        if "DerivedData" in path.parts or ".build" in path.parts:
            continue
        if _ABANDONED_HOST in _strip_swift_comments(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(_REPO)))
    assert not offenders, (
        f"{_ABANDONED_HOST} is an incidental Railway hostname, not the product's domain. "
        f"App Store Connect, the AASA and every legal document use caydexinvest.com. "
        f"Still referenced by: {offenders}"
    )


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────────────
#
# Hand-run 2026-09-07. Each mutation applied, the suite run, then reverted. A guard that
# survives its own mutation proves nothing (testing.md §3 rule 3).
#
#  1. pbxproj: one widget CURRENT_PROJECT_VERSION back to 4
#       -> test_every_target_declares_the_same_build_number FAILED  ✅
#  2. pbxproj: dropped one INFOPLIST_KEY_ITSAppUsesNonExemptEncryption line
#       -> test_every_target_declares_the_encryption_answer FAILED  ✅
#  3. WidgetAPIConfig: host back to the Railway subdomain (the realistic drift — one copy)
#       -> BOTH url tests FAILED  ✅
#  4. All three hosts reverted together (the case equality alone cannot catch)
#       -> test_the_abandoned_railway_subdomain_is_gone_from_shipped_swift FAILED  ✅
#  5. Comment-stripping disabled, then mutation 4 applied
#       -> still FAILED, i.e. the scan reads code and not the explanatory comments  ✅
