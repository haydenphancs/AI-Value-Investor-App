"""One locked "Listen" tap shows ONE plan sheet — not one per screen stacked under it.

`learnAudioPaywall()` turns `LearnAudioEntitlement.upgradeRequested` into the plan sheet, and EVERY
mounted instance hears that subject. Wiser → Library → Book → Chapter had four mounted (Learn's
root, the Library push, and the two covers), so one refused tap set four sheets: the chapter's
presented, and SwiftUI QUEUED the other three behind the covers on top
(`ModalPresentationProbe` documents the queueing) — each popping up in turn as the user closed
the screens. The modifier's own doc claimed "only the innermost presenting view will actually show
a sheet", which is what made the gap invisible in review.

Now each presenter asks UIKit, when a request arrives, whether it is the frontmost: in a window
(a full-screen cover takes the screens under it out of the window) and with nothing presented over
its host. And the two PUSHES drop their presenter: LearnContentView's sits outside Learn's
NavigationStack and already covers them, so with both, two presenters with nothing over them would
both answer.

Source scans with comments stripped and declarations brace-bounded; every guard is shown to fail on
the mutation it exists for. Not run on a device or simulator when written (no Swift toolchain where
this was authored).
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend/ios/ios"
PAYWALL = IOS / "Views/Modifiers/LearnAudioPaywall.swift"
PROBE = IOS / "Core/Utilities/ModalPresentationProbe.swift"
OVERLAY = IOS / "Views/Modifiers/GlobalAudioOverlay.swift"
LEARN = IOS / "Views/Screens/LearnView.swift"

# Screens that present the plan sheet themselves: the tab root and the three covers that can start
# narration. The pushes are covered by the tab root's presenter.
_PRESENTERS = (
    "Views/Screens/LearnView.swift",
    "Views/Screens/MoneyMoveArticleDetailView.swift",
    "Views/Screens/BookDetailView.swift",
    "Views/Screens/BookCoreDetailView.swift",
)
_PUSHES = {
    "Views/Screens/MoneyMovesDetailView.swift": "$showingMoneyMovesDetail",
    "Views/Screens/BookLibraryView.swift": "$showingBookLibrary",
}


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} is missing — every assertion below would be vacuous"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _block_after(src: str, anchor: str) -> str:
    """The brace-bounded block after `anchor`. AssertionError (never `pytest.fail`) so the mutation
    test can tell a guard that fired from one that crashed."""
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    raise AssertionError(f"unbalanced braces after `{anchor}`")


# ── The guards, as functions of the sources so each can be shown to fire ──────────────────────


def _assert_only_the_frontmost_presents(paywall: str) -> None:
    modifier = _block_after(paywall, "private struct LearnAudioPaywallModifier: ViewModifier")
    on_request = _block_after(modifier, ".onReceive(entitlement.upgradeRequested)")
    gate = on_request.find("guard anchor.isFrontmost else { return }")
    show = on_request.find("showPaywall = true")
    assert 0 <= gate < show, (
        "the request handler must ask whether this presenter is frontmost BEFORE setting its "
        "sheet — every mounted presenter hears the same request")
    assert "PaywallAnchorView(anchor: anchor)" in modifier, "the anchor is never attached"


def _assert_frontmost_means_visible(paywall: str) -> None:
    anchor = _block_after(paywall, "private final class PaywallAnchor")
    assert re.search(r"weak\s+var\s+view:\s*UIView\?", anchor), "the anchor must hold its view weakly"
    frontmost = _block_after(anchor, "var isFrontmost: Bool")
    assert "guard let view else { return true }" in frontmost, (
        "unknown must answer true: a presenter that cannot be located still shows the plan")
    assert "guard view.window != nil else { return false }" in frontmost, (
        "a screen a full-screen cover took out of the window is not frontmost")
    assert "view.hostViewController" in frontmost
    assert re.search(
        r"sequence\(first: host, next: \{ \$0\.parent \}\)\.contains \{ \$0\.presentedViewController != nil \}",
        frontmost,
    ), "anything presented over the host chain (a cover, a sheet) means not frontmost"


def _assert_pushes_carry_no_presenter(sources: dict[str, str]) -> None:
    learn = sources[str(LEARN)]
    for rel in _PRESENTERS:
        assert ".learnAudioPaywall()" in sources[str(IOS / rel)], (
            f"{rel}: lost its plan-sheet presenter — a refused tap there shows nothing")
    for rel, binding in _PUSHES.items():
        assert ".learnAudioPaywall()" not in sources[str(IOS / rel)], (
            f"{rel} presents the plan sheet again. It is a PUSH; LearnContentView's presenter "
            "already covers it, and two presenters with nothing over them both answer.")
        push = _block_after(learn, f".navigationDestination(isPresented: {binding})")
        assert rel.split("/")[-1].removesuffix(".swift") + "()" in push, (
            f"{rel} is no longer pushed from Learn — if it became a cover it needs its own "
            "presenter back, and this guard needs rewriting")


def _sources() -> dict[str, str]:
    paths = [PAYWALL, PROBE, OVERLAY, LEARN] + [IOS / rel for rel in _PRESENTERS + tuple(_PUSHES)]
    return {str(p): _code(p) for p in paths}


_GUARDS = {
    "only-the-frontmost-presents": lambda s: _assert_only_the_frontmost_presents(s[str(PAYWALL)]),
    "frontmost-means-visible": lambda s: _assert_frontmost_means_visible(s[str(PAYWALL)]),
    "pushes-carry-no-presenter": _assert_pushes_carry_no_presenter,
}


@pytest.mark.parametrize("guard", sorted(_GUARDS))
def test_guard_holds(guard):
    _GUARDS[guard](_sources())


def test_the_host_lookup_is_shared():
    """One responder walk, in the presentation probe file, used by both UIKit readers."""
    probe = _block_after(_code(PROBE), "extension UIView")
    assert "var hostViewController: UIViewController?" in probe and ".next" in probe
    reader = _block_after(_code(OVERLAY), "private struct ModalPresentationReader: UIViewRepresentable")
    assert "hostViewController" in reader and "responder" not in reader, (
        "GlobalAudioOverlay's probe grew its own responder walk again")


# (label, guard, source, old, new) — `old` must still be found, so a mutation that stopped applying
# fails loudly instead of passing as a vacuous "the guard fired".
_MUTATIONS = [
    ("every-presenter-answers", "only-the-frontmost-presents", PAYWALL,
     "guard anchor.isFrontmost else { return }\n", ""),
    ("anchor-detached", "only-the-frontmost-presents", PAYWALL,
     "PaywallAnchorView(anchor: anchor)", "EmptyView()"),
    ("covered-screen-answers", "frontmost-means-visible", PAYWALL,
     "guard view.window != nil else { return false }", "guard view.window != nil else { return true }"),
    ("unknown-stays-silent", "frontmost-means-visible", PAYWALL,
     "guard let view else { return true }", "guard let view else { return false }"),
    ("sheet-over-host-ignored", "frontmost-means-visible", PAYWALL,
     "$0.presentedViewController != nil", "$0.isBeingPresented"),
    ("push-presents-again", "pushes-carry-no-presenter", IOS / "Views/Screens/MoneyMovesDetailView.swift",
     "        .interactiveDismissDisabled(false)\n",
     "        .interactiveDismissDisabled(false)\n        .learnAudioPaywall()\n"),
    ("cover-loses-presenter", "pushes-carry-no-presenter", IOS / "Views/Screens/BookCoreDetailView.swift",
     "        .learnAudioPaywall()\n", "\n"),
]


@pytest.mark.parametrize("label,guard,path,old,new", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_every_guard_kills_its_mutation(label, guard, path, old, new):
    sources = _sources()
    key = str(path)
    assert old in sources[key], f"{label}: the mutation no longer applies — re-point it"
    sources[key] = sources[key].replace(old, new, 1)
    with pytest.raises(AssertionError):
        _GUARDS[guard](sources)


def test_the_comment_stripper_strips():
    raw = PAYWALL.read_text(encoding="utf-8")
    assert "four plan sheets" in raw, "the paywall no longer explains the stacked sheets it prevents"
    assert "four plan sheets" not in _strip_comments(raw), "comment stripping regressed"
