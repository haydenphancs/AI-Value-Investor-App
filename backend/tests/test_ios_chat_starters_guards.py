"""Source-scan guards for the rotating chat starters (iOS half).

There is no XCTest target, so the iOS invariants that must not regress are pinned from
Python by reading the Swift source. Per `.claude/rules/testing.md` §3 these go vacuous very
easily, so every scan here is comment-stripped, brace-bounded to the declaration it means,
and asserted with `== 1` rather than `>= 1` where a count is claimed. Each was
mutation-tested by hand when written: break the source, watch it fail, restore.

The comment-stripping is doing real work in this module, not ceremony: `MarqueeChipRow`'s
own header names `.scrollPosition`, `ScrollViewReader`, `.repeatForever` and `withAnimation`
in prose while explaining why none of them is used. An un-stripped scan would pass on that
explanation after the code was reverted — which is precisely the failure mode this rule
exists for.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_MARQUEE = _IOS / "Views/Molecules/MarqueeChipRow.swift"
_CHAT_BAR = _IOS / "Views/Molecules/CaydexAIChatBar.swift"
_STORE = _IOS / "Services/ChatStartersStore.swift"
_BACKDROP = _IOS / "Views/Molecules/ChatBackdropLogo.swift"
_CHAT_SCREEN = _IOS / "Views/Screens/AIChatScreen.swift"
_DETAIL_BARS = tuple(
    _IOS / "Views/Screens" / f"{name}DetailView.swift"
    for name in ("Ticker", "ETF", "Crypto", "Commodity", "Index")
)


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    # `[ \t]*`, NOT `\s*`: `\s` eats the preceding newline and collapses two lines into
    # one, silently shifting every line number this file could report.
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_block(src: str, prefix: str) -> str:
    """Brace-matched body of the declaration starting at ``prefix``."""
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {prefix!r}")


# ── the marquee mechanism ────────────────────────────────────────────────────


def test_the_marquee_uses_no_banned_scroll_api():
    """`.scrollPosition(id:)` writes its binding during layout and froze this app once;
    a `ScrollViewReader` nested in a `ScrollView` is a silent no-op."""
    code = _code(_MARQUEE)
    for banned in (".scrollPosition(", "ScrollViewReader", "ScrollView("):
        assert banned not in code, (
            f"{banned} in MarqueeChipRow — the row is a clipped, offset HStack precisely so "
            "it needs none of these; see the file header"
        )


def test_the_marquee_does_not_use_an_unpausable_animation():
    """`.repeatForever` cannot be paused, and pausing is the whole design here."""
    block = _decl_block(_code(_MARQUEE), "struct MarqueeChipRow")
    for banned in ("repeatForever", "withAnimation"):
        assert banned not in block, (
            f"{banned} in MarqueeChipRow: SwiftUI cannot pause a running animation and the "
            "presentation value is unreadable, so stop/resume would jump — and Reduce "
            "Motion would need a second code path"
        )


def test_there_is_exactly_one_timeline_view_and_it_is_pausable():
    code = _code(_MARQUEE)
    assert code.count("TimelineView(") == 1, "exactly one drift driver, or they can disagree"
    schedule = code[code.index("TimelineView(") : code.index("TimelineView(") + 120]
    assert ".animation(" in schedule, "the schedule must be .animation"
    assert "paused:" in schedule, (
        "the `paused:` argument IS the design: every still-row case collapses into it, so "
        "dropping it forces a second code path back into existence"
    )


def test_the_offset_is_computed_inside_the_timeline_closure():
    """Outside it, the offset stops tracking the tick and the row never moves."""
    code = _code(_MARQUEE)
    assert code.count(".offset(x:") == 1
    assert code.index("TimelineView(") < code.index(".offset(x:")


def test_the_pause_condition_covers_every_case_that_must_not_drift():
    block = _decl_block(_code(_MARQUEE), "private var isPaused")
    for term in ("drifts", "overflows", "reduceMotion", "voiceOverEnabled",
                 "isTouching", "isOnScreen", "scenePhase"):
        assert term in block, (
            f"`{term}` dropped from isPaused. Each is a state in which the row must be "
            "still; losing one means a drifting row where the user cannot use it."
        )


def test_reduce_motion_never_branches_the_layout():
    """The single-code-path rule, stated as an assertion.

    A `if reduceMotion { staticRow } else { marquee }` is two layouts to keep in sync, and
    the one that gets edited is never the one under review.
    """
    code = _code(_MARQUEE)
    body = _decl_block(code, "var body: some View")
    assert "reduceMotion" not in body, (
        "reduceMotion appears in `body` — it belongs only in `isPaused`, so that the still "
        "row and the drifting row are literally the same row"
    )
    assert code.count("reduceMotion") == 2, (
        "expected exactly the @Environment declaration and the isPaused term"
    )


def test_the_tiled_row_is_index_keyed():
    """Tiling repeats every string, and two equal ids in one ForEach collapse to one."""
    block = _decl_block(_code(_MARQUEE), "private var singleRow")
    assert "Array(chips.enumerated())" in block
    assert "id: \\.self" not in block, (
        "`id: \\.self` over repeated chips silently drops elements — this is why the old "
        "ScrollView row could not simply be wrapped"
    )


def test_voiceover_reads_each_question_once_and_can_reach_them_all():
    code = _code(_MARQUEE)
    assert "accessibilityHidden(tile != 0)" in code, (
        "without this VoiceOver reads every question 2-3 times — once per loop tile"
    )
    assert ".accessibilityScrollAction" in code, (
        "a custom clipped container has no native scroll for the rotor to drive"
    )


def test_the_tiled_row_cannot_widen_its_parent():
    """The regression that cost the most to find, pinned.

    The tiled row is `.fixedSize(horizontal: true)`, so it reports an intrinsic width
    several screens wide. `.frame(maxWidth: .infinity)` does NOT contain a fixedSize
    child and `.clipped()` only affects DRAWING, so that width escaped upward and
    widened the whole enclosing VStack — which pushed AIChatScreen's ✕ off the right
    edge and centred the empty-state view somewhere off-canvas. Two "missing views"
    that looked completely unrelated to a chip row.

    An `.overlay` is the containment: an overlay's child never sizes its parent.
    """
    code = _code(_MARQUEE)
    assert ".overlay(alignment: .leading)" in code, (
        "the tiled row must live in an overlay on a frame-owning base, or its intrinsic "
        "width escapes and silently breaks the ENCLOSING screen's layout"
    )
    base = code.index("return Color.clear")
    overlay = code.index(".overlay(alignment: .leading)")
    assert base < overlay, "the frame-owning base must come before the overlay"
    assert "Color.clear" in code[base:overlay]
    assert ".frame(maxWidth: .infinity)" in code[base:overlay], (
        "the BASE owns the width; that is what stops the child dictating it"
    )


def test_pausing_freezes_in_place_rather_than_snapping_back():
    """Without committing the position on the pause transition, `offset` returns
    `wrapped(base)` — the last DRAGGED position — so every pause yanked the row
    backwards. Observed as "it moves for a second, then jumps"."""
    code = _code(_MARQUEE)
    assert "onChange(of: isPaused)" in code, "the pause transition must be handled"
    block = _decl_block(code, "private func offset(at date: Date)")
    assert "rawOffset" in block, (
        "offset() must delegate to a raw, pause-blind position so the transition handler "
        "can ask where the row actually is"
    )


def test_the_row_only_stops_when_the_app_is_truly_backgrounded():
    """`scenePhase != .active` is too twitchy: `.inactive` covers the app switcher,
    Control Centre — and, on the Simulator, every moment the window is not key. The row
    looked permanently dead to anyone testing with a terminal focused, which is exactly
    how it was first reported."""
    block = _decl_block(_code(_MARQUEE), "private var isPaused")
    assert "scenePhase == .background" in block
    assert "scenePhase != .active" not in block


# ── the empty-state watermark ────────────────────────────────────────────────


def test_the_backdrop_uses_the_keyed_glyph_and_never_the_opaque_plate():
    """`CaydexLogo.png` is an OPAQUE #171B26 plate — alpha 255 across all 1024×1024.

    Faded, it is a dark SQUARE, not a faded mark, and on a light page it is a dark square
    on near-white. `CaydexGlyph` is the same art with the plate keyed out into the alpha
    channel, which is what makes it tintable. `CaydexLogoMark` exists for the same reason
    and documents the pixel-level verification.
    """
    code = _code(_BACKDROP)
    assert 'Image("CaydexGlyph")' in code
    assert "CaydexLogo" not in code, (
        "the opaque plate must never be used as a watermark — see CaydexLogoMark's header"
    )
    assert ".renderingMode(.template)" in code, "a template render is what accepts the tint"

    glyph = _IOS / "Assets.xcassets/CaydexGlyph.imageset/CaydexGlyph.png"
    assert glyph.exists(), f"{glyph} is missing — the empty state would render nothing"


def test_the_backdrop_is_decorative_and_adaptive():
    code = _code(_BACKDROP)
    assert "AppColors." in code, "the tint must come from a theme token, not a raw Color"
    assert "Color(hex:" not in code, "raw hexes are banned outside AppTheme"
    assert ".accessibilityHidden(true)" in code, "a watermark must not be read aloud"
    assert ".allowsHitTesting(false)" in code, "it must never eat a tap meant for the chips"


# ── the chip ─────────────────────────────────────────────────────────────────


def test_the_chip_reaches_the_minimum_tap_target_in_the_right_order():
    block = _decl_block(_code(_CHAT_BAR), "struct CaydexAISuggestionChip")
    assert "hitTargetHeight: CGFloat = 44" in block, "44pt is Apple's HIG minimum, not a knob"
    last_padding = block.rindex(".padding(")
    shape = block.index(".contentShape(")
    assert last_padding < shape, (
        "contentShape must come AFTER the padding that grows the target; applied before, it "
        "shrinks the hit area straight back to the glyphs"
    )
    assert ".hitSlop(" not in block, (
        ".hitSlop() is a documented no-op on a Button label — its negative padding hands the "
        "frame back"
    )


def test_the_chip_stays_on_one_line_at_its_intrinsic_width():
    """Not cosmetic: a wrapping chip makes the tile width depend on available width, and
    then the marquee's loop unit never settles."""
    block = _decl_block(_code(_CHAT_BAR), "struct CaydexAISuggestionChip")
    assert ".lineLimit(1)" in block
    assert ".fixedSize(horizontal: true, vertical: false)" in block


# ── who drifts ───────────────────────────────────────────────────────────────


def test_only_the_global_chat_turns_the_marquee_on():
    assert "marquee: true" in _code(_CHAT_SCREEN), "the global chat is the surface that drifts"


@pytest.mark.parametrize("path", _DETAIL_BARS, ids=lambda p: p.stem)
def test_no_detail_screen_drifts(path):
    """The tester asked for changing questions on the detail bars, explicitly without motion."""
    assert "marquee: true" not in _code(path), f"{path.stem} must keep a still row"


@pytest.mark.parametrize("path", _DETAIL_BARS, ids=lambda p: p.stem)
def test_every_detail_screen_reads_the_rotating_set(path):
    code = _code(path)
    assert "rotatingAISuggestions" in code
    assert "suggestions: viewModel.aiSuggestions," not in code, (
        "the call site should pass the rotating set; the ViewModel's fixed set is the "
        "fallback inside it"
    )


# ── the store ────────────────────────────────────────────────────────────────


def test_the_store_is_observable():
    """A plain class publishes nothing, so the detail bars would show the bundled set for
    the whole screen visit and the rotation would look broken."""
    code = _code(_STORE)
    assert "@Observable" in code
    assert "final class ChatStartersStore" in code


def test_the_prefetch_latch_follows_its_await():
    """Setting a latch BEFORE the await is the bug that shipped in MoneyMovesContentStore:
    a second caller arriving mid-request saw the flag, returned early, and read an empty
    store."""
    block = _decl_block(_code(_STORE), "func prefetch()")
    assert "prefetchTask" in block, "concurrent callers must join, not start a second fetch"
    assign = block.index("prefetchTask = task")
    first_await = block.index("await")
    assert first_await < assign or "await existing.value" in block[:assign], (
        "the in-flight task must be joined before a new one is assigned"
    )


def test_the_store_drops_a_template_it_cannot_fill():
    """Rendering a raw brace to a user is worse than one fewer chip."""
    block = _decl_block(_code(_STORE), "func detailStarters(")
    assert 'replacingOccurrences(of: "{symbol}"' in block
    assert 'contains("{")' in block and 'contains("}")' in block, (
        "an unfilled placeholder must be filtered out, not shown"
    )


def test_the_store_keys_the_day_on_eastern_time():
    """The server composes on the market's day. Reading the device's own calendar rolls a
    user in Tokyo over a day early, against a server that still says yesterday."""
    code = _code(_STORE)
    assert '"America/New_York"' in code
    block = _decl_block(code, "static func currentETDate()")
    assert "Calendar.current" not in block, "the device locale is exactly what must not be used"


def test_the_offline_rotation_does_not_depend_on_hashValue():
    """Swift seeds String hashing per process launch, so a hashValue-derived offset
    reshuffles the row on every cold start and stops looking daily."""
    block = _decl_block(_code(_STORE), "static func rotate(")
    assert "hashValue" not in block


def test_the_store_is_cleared_when_a_session_ends():
    code = _code(_IOS / "Core/State/AppState.swift")
    block = _decl_block(code, "private func discardDataForEndedSession()")
    assert "ChatStartersStore.shared.clearForEndedSession()" in block


# ── anti-vacuity ─────────────────────────────────────────────────────────────


def test_these_scans_are_not_vacuous():
    """The whole module lives or dies here."""
    for path in (_MARQUEE, _CHAT_BAR, _STORE):
        raw = path.read_text(encoding="utf-8")
        stripped = _strip_comments(raw)
        assert len(stripped) > 800, f"{path.name}: scanned text implausibly small"
        assert stripped.count("\n") == raw.count("\n"), (
            f"{path.name}: the stripper changed the line count — it is eating newlines, "
            "which shifts every line number derived from it"
        )

    # The stripper is exercised by REAL prose: MarqueeChipRow's header names every banned
    # token while explaining why none is used. If stripping ever stops working, the bans
    # above go permanently green — so assert the tokens are present raw and absent stripped.
    raw = _MARQUEE.read_text(encoding="utf-8")
    stripped = _strip_comments(raw)
    for token in (".scrollPosition(", "ScrollViewReader", "repeatForever"):
        assert token in raw, (
            f"{token} should still be discussed in MarqueeChipRow's header; if the header "
            "was rewritten, this control needs a new token"
        )
        assert token not in stripped, f"the comment stripper failed to remove {token}"

    # And prove the brace bounder returns a real subset, not the whole file.
    block = _decl_block(stripped, "private var isPaused")
    assert 0 < len(block) < len(stripped)
    assert "var body" not in block, "the bounder over-ran into the next declaration"
