"""The iOS deployment target must stay reachable, and nothing may silently raise it.

`IPHONEOS_DEPLOYMENT_TARGET` was **26.2** until 2026-08-07. Every device not on the newest OS
could not install the app at all — it would not even appear in their App Store. Nothing required
it: the only iOS 26-exclusive API in the entire codebase was `.glassEffect(_:in:)`, at four call
sites, all decorative chrome on a floating panel. There was not a single `@available(iOS 26…)`
anywhere, which is the signature of an Xcode default nobody revisited rather than a decision.

The floor is now 18.0, verified by a clean build with zero errors — the compiler, not a keyword
search, is what proves no other 26-only API is in use.

The danger now is re-raising it by accident: a bare `.glassEffect` compiles fine on a machine
whose *SDK* is 26 as long as the deployment target allows it, so the mistake would surface only
as a build failure later, or as a runtime crash on a real iOS 18 device if written inside an
already-guarded branch. `.glassPanel(cornerRadius:)` wraps the availability check once.

Source-level on both sides, like `test_ios_auth_policy_parity.py` — no app build, no network.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PBXPROJ = _REPO / "frontend/ios/ios.xcodeproj/project.pbxproj"
_IOS_SRC = _REPO / "frontend/ios/ios"
_GLASS_PANEL = _IOS_SRC / "Views/Modifiers/GlassPanel.swift"

# The floor we committed to. Raising it is a product decision that costs install base, so it
# must be a deliberate edit to this constant, not a side effect of an Xcode upgrade.
_MAX_ALLOWED_TARGET = 18.0


def _swift_files():
    if not _IOS_SRC.exists():
        pytest.skip(f"{_IOS_SRC} not present")
    return [p for p in _IOS_SRC.rglob("*.swift")]


def _code_only(src: str) -> str:
    """Strip `//` comments and doc comments.

    Necessary, not fussy: these files DOCUMENT the very APIs being searched for — GlassPanel's
    own header explains what `.glassEffect` is and why `@available(iOS 26…)` appears nowhere —
    so a naive scan flags the explanation as the violation. Line comments only; the codebase
    uses no `/* */` blocks in Swift.
    """
    out = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        # Trailing comment on a code line. Safe here: no Swift string literal in this codebase
        # contains "//" (checked), and a false strip could only ever HIDE a match, which the
        # anti-vacuity assertions below would catch.
        if "//" in line:
            line = line[: line.index("//")]
        out.append(line)
    return "\n".join(out)


def test_the_deployment_target_is_not_raised_back():
    if not _PBXPROJ.exists():
        pytest.skip("project.pbxproj not present")
    targets = re.findall(r"IPHONEOS_DEPLOYMENT_TARGET = ([\d.]+);", _PBXPROJ.read_text())
    assert targets, "no IPHONEOS_DEPLOYMENT_TARGET found — regex drifted"

    for raw in targets:
        assert float(raw) <= _MAX_ALLOWED_TARGET, (
            f"deployment target is {raw}; every device below that cannot install the app at "
            f"all. If this is deliberate, raise _MAX_ALLOWED_TARGET here and say why."
        )

    # Debug and Release must agree, or the thing you test is not the thing you ship.
    assert len(set(targets)) == 1, f"configurations disagree: {sorted(set(targets))}"


def test_no_bare_glass_effect_call_outside_the_wrapper():
    """`.glassEffect` is iOS 26-only. One wrapper owns the availability check; a direct call
    anywhere else re-raises the floor for the whole app the next time someone builds."""
    offenders = []
    for path in _swift_files():
        if path.resolve() == _GLASS_PANEL.resolve():
            continue
        for i, line in enumerate(_code_only(path.read_text()).splitlines(), 1):
            if ".glassEffect(" in line or "GlassEffectContainer" in line:
                offenders.append(f"{path.relative_to(_REPO)} (code line {i})")

    assert not offenders, (
        "iOS 26-only glass API called outside Views/Modifiers/GlassPanel.swift: "
        + ", ".join(offenders)
        + ". Use `.glassPanel(cornerRadius:)`, which falls back to `.ultraThinMaterial`."
    )

    # Anti-vacuity: the wrapper itself must still contain the call, or this test passes because
    # the API is gone entirely rather than because it is correctly centralised.
    assert ".glassEffect(" in _code_only(_GLASS_PANEL.read_text()), (
        "GlassPanel no longer calls .glassEffect — this test is now vacuous"
    )


def test_the_wrapper_actually_guards_the_call():
    """A wrapper that forgot its `#available` is worse than no wrapper — it centralises the
    crash instead of the check."""
    if not _GLASS_PANEL.exists():
        pytest.skip("GlassPanel.swift not present")
    # Code only — the header comment names both the API and the annotation, so an unfiltered
    # scan finds the prose before the implementation and reads the order backwards.
    src = _code_only(_GLASS_PANEL.read_text())

    assert "#available(iOS 26.0, *)" in src
    guard_at = src.index("#available(iOS 26.0, *)")
    call_at = src.index(".glassEffect(")
    assert guard_at < call_at, "the availability check must precede the iOS 26 call"

    # And the else-branch must actually render something, not silently drop the background.
    else_branch = src[src.index("} else {", call_at):]
    assert "ultraThinMaterial" in else_branch, (
        "the pre-26 branch must supply a real material, or the panel renders unreadable "
        "against whatever is behind it"
    )


def test_any_ios_26_availability_annotation_is_deliberate():
    """Anti-vacuity for the two tests above: if someone adds `@available(iOS 26…)` to ship a
    new 26-only feature, that is a real decision — but it must not be the *only* thing keeping
    the app building, and it must not appear without the deployment floor being reconsidered.

    Today the expected count is zero. A non-zero count is not automatically wrong; it means
    read the diff.
    """
    annotated = []
    for path in _swift_files():
        for i, line in enumerate(_code_only(path.read_text()).splitlines(), 1):
            if re.search(r"@available\(iOS 2[6-9]", line):
                annotated.append(f"{path.relative_to(_REPO)} (code line {i})")

    assert not annotated, (
        "new iOS 26+ availability annotations: " + ", ".join(annotated)
        + ". Confirm the feature degrades on iOS 18 before landing this."
    )
