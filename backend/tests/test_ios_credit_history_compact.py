"""Credit History: compact two-line rows in per-day groups, and an explicit "Load more".

Developer request (2026-09-24, screenshot of Account › Credit History): every movement was a
full `ActivityRow` card — 40pt glyph circle, three lines, 16pt padding, ~100pt tall, ~7 rows
a screen — on the screen a heavy user scrolls most. Asked for: much smaller rows with the
title and its detail on one line, how deep the history goes, and a "Load more" if it is long.

Answers this file pins:
* Rows are ~52pt two-line SEGMENTS of a rounded per-day group (`CreditHistoryRow`).
* History depth is the whole account lifetime — `credit_transactions` is never trimmed — so
  paging is an explicit button, 50 rows a page (≤ the backend's `MAX_PAGE`), and the end of the
  list says it is the start of the history.

Two halves, because there is no XCTest target:
A. `CreditHistoryRowFormat.swift` is EXECUTED via `xcrun swift -` (group positions, the
   second line's composition, the detail).
B. Source guards, comment-stripped (`//` AND `/* */`) and brace-bounded
   (.claude/rules/testing.md §3). The load-bearing ones: rows stay DIRECT children of the one
   `LazyVStack` (a per-day container would resize in place on every "Load more" — the
   documented 100%-CPU hang); a "Load more" that lands after a refresh or sign-out is
   discarded; the segment draws its own light-mode edge.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_FORMAT = _IOS / "Core/Utilities/CreditHistoryRowFormat.swift"
_ROW = _IOS / "Views/Molecules/CreditHistoryRow.swift"
_VIEW = _IOS / "Views/Screens/CreditHistoryView.swift"
_VM = _IOS / "ViewModels/CreditHistoryViewModel.swift"
_MODELS = _IOS / "Models/CreditHistoryModels.swift"
_SERVICE = _REPO / "backend/app/services/credit_history_service.py"


def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks, `//` lines and trailing `//` tails."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _code(path: Path) -> str:
    return _strip_comments(path.read_text())


def _no_preview(src: str) -> str:
    """Code before the first `#Preview` — previews legitimately build rows by hand."""
    i = src.find("#Preview")
    return src if i == -1 else src[:i]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _decl_block(src: str, header: str) -> str:
    """Brace-balanced body of the FIRST declaration matching `header`, comments stripped."""
    src = _strip_comments(src)
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


# ── A. The pure half, executed ───────────────────────────────────────

_HARNESS = r"""
var failures = 0
var cases = 0
func check<T: Equatable>(_ name: String, _ got: T, _ want: T) {
    cases += 1
    if got != want {
        failures += 1
        print("FAIL|\(name)|got=\(String(describing: got))|want=\(String(describing: want))")
    }
}
typealias P = RowGroupPosition
let F = CreditHistoryRowFormat.self
let none: String? = nil

// --- positions: defensive → .only --------------------------------------------------------
check("pos_single", P(index: 0, count: 1), .only)
check("pos_empty_count", P(index: 0, count: 0), .only)
check("pos_negative_index", P(index: -1, count: 3), .only)
check("pos_index_eq_count", P(index: 3, count: 3), .only)
check("pos_index_past_count", P(index: 5, count: 3), .only)
check("pos_negative_count", P(index: 0, count: -2), .only)
// --- positions: groups ----------------------------------------------------------------------
check("pos_2_first", P(index: 0, count: 2), .first)
check("pos_2_last", P(index: 1, count: 2), .last)
check("pos_3_first", P(index: 0, count: 3), .first)
check("pos_3_middle", P(index: 1, count: 3), .middle)
check("pos_3_last", P(index: 2, count: 3), .last)
check("pos_5_middle", P(index: 2, count: 5), .middle)
check("pos_5_last", P(index: 4, count: 5), .last)
// --- neighbours -----------------------------------------------------------------------------
check("only_above", P.only.hasRowAbove, false)
check("only_below", P.only.hasRowBelow, false)
check("first_above", P.first.hasRowAbove, false)
check("first_below", P.first.hasRowBelow, true)
check("middle_above", P.middle.hasRowAbove, true)
check("middle_below", P.middle.hasRowBelow, true)
check("last_above", P.last.hasRowAbove, true)
check("last_below", P.last.hasRowBelow, false)

// --- metaLine: `time · Refunded · pool note`, blanks skipped, nil when empty ----------------
check("meta_time_pool", F.metaLine(time: "6:48 PM", poolNote: "1 purchased", isReversed: false), "6:48 PM · 1 purchased")
check("meta_time_only", F.metaLine(time: "6:48 PM", poolNote: nil, isReversed: false), "6:48 PM")
check("meta_time_reversed", F.metaLine(time: "6:48 PM", poolNote: nil, isReversed: true), "6:48 PM · Refunded")
check("meta_all_three", F.metaLine(time: "6:48 PM", poolNote: "20 purchased", isReversed: true), "6:48 PM · Refunded · 20 purchased")
check("meta_nothing", F.metaLine(time: "", poolNote: nil, isReversed: false), none)
check("meta_whitespace_only", F.metaLine(time: "  ", poolNote: "   ", isReversed: false), none)
check("meta_reversed_only", F.metaLine(time: "", poolNote: nil, isReversed: true), "Refunded")
check("meta_pool_only", F.metaLine(time: "", poolNote: "Never expires", isReversed: false), "Never expires")
check("meta_trimmed", F.metaLine(time: " 6:48 PM ", poolNote: " 1 purchased ", isReversed: false), "6:48 PM · 1 purchased")
check("meta_empty_pool", F.metaLine(time: "6:48 PM", poolNote: "", isReversed: false), "6:48 PM")
check("meta_newline_time", F.metaLine(time: "\n", poolNote: "15 monthly + 5 purchased", isReversed: true), "Refunded · 15 monthly + 5 purchased")

// --- detail ---------------------------------------------------------------------------------
check("detail_nil", F.detail(nil), none)
check("detail_empty", F.detail(""), none)
check("detail_spaces", F.detail("  "), none)
check("detail_newline", F.detail("\n"), none)
check("detail_trimmed", F.detail(" AAPL "), "AAPL")
check("detail_dotted", F.detail("BRK.B"), "BRK.B")

print("DONE|\(failures)|\(cases)")
"""

# A LITERAL, not a count of the harness — counting would shrink with it.
_EXPECTED_CASES = 38
_CORE_CASES = ("pos_3_middle", "pos_2_last", "last_below", "first_above", "meta_all_three",
               "meta_nothing", "detail_spaces")


def _run_swift() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    src = _FORMAT.read_text() + "\n" + _HARNESS
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=src, text=True,
                              capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(
            "the Swift harness did not run to completion — CreditHistoryRowFormat probably "
            "stopped compiling standalone (a SwiftUI import will do it).\n"
            f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-4000:]}")
    return proc.stdout


@pytest.fixture(scope="module")
def swift_output() -> str:
    return _run_swift()


def test_every_format_case(swift_output: str):
    failures = [line for line in swift_output.splitlines() if line.startswith("FAIL|")]
    assert not failures, "CreditHistoryRowFormat mismatches:\n  " + "\n  ".join(failures)


def test_the_harness_ran_every_case(swift_output: str):
    m = re.search(r"DONE\|(\d+)\|(\d+)", swift_output)
    assert m, swift_output[-2000:]
    assert int(m.group(1)) == 0
    assert int(m.group(2)) == _EXPECTED_CASES, (
        f"the harness ran {m.group(2)} cases, expected {_EXPECTED_CASES}")
    assert _HARNESS.count("\ncheck(") == _EXPECTED_CASES
    for name in _CORE_CASES:
        assert f'check("{name}",' in _HARNESS, f"core case {name} was removed"


def test_the_format_file_is_foundation_only():
    raw = _FORMAT.read_text()
    code = _strip_comments(raw)
    assert "import Foundation" in code
    for banned in ("import SwiftUI", "import UIKit", "import Combine"):
        assert banned not in code, f"{banned} makes the file unrunnable under `xcrun swift -`"
    assert "import SwiftUI" in raw, "the header explaining the rule is gone (anti-vacuity)"


# ── B. The screen ────────────────────────────────────────────────────


def test_rows_stay_direct_children_of_the_one_lazy_stack():
    code = _no_preview(_code(_VIEW))
    # Word-matched, not `LazyVStack(`: a trailing-closure `LazyVStack { … }` is just as lazy —
    # and a LazyVGrid / List would nest a second lazy context.
    assert len(re.findall(r"\bLazy[VH](?:Stack|Grid)\b|\bList\b", code)) == 1, (
        "exactly one lazy container on this screen")
    # The WHOLE path from the lazy stack to the rows, not just `rows`: wrapping `content` in
    # the body, or `rows` in the `.loaded` arm, makes every row ONE lazy child that grows by 50
    # rows per "Load more" — the hang — while `rows` itself still looks clean.
    body = _flat(_decl_block(_VIEW.read_text(), "var body: some View"))
    assert "LazyVStack(alignment: .leading, spacing: 0) { content }" in body, (
        "`content` must be the lazy stack's ONLY, unwrapped child")
    content = _flat(_decl_block(_VIEW.read_text(), "private var content: some View"))
    assert re.search(r"case \.loaded: rows \} \}$", content), (
        "`rows` must be the bare `.loaded` arm, unwrapped")
    rows = _decl_block(_VIEW.read_text(), "private var rows: some View")
    flat = _flat(rows)
    assert "ForEach(Array(day.items.enumerated()), id: \\.element.id) { index, item in" in flat
    assert ("CreditHistoryRow( item: item, position: RowGroupPosition(index: index, "
            "count: day.items.count) )") in flat
    # No per-day container: rows wrapped in a VStack card would be ONE lazy child that grows
    # in place on every "Load more" — the documented hang.
    for container in (r"\b(?:VStack|HStack|ZStack|Group|Section|Grid|List|Form|ScrollView)\b",
                      r"\bLazy[VH](?:Stack|Grid)\b", r"\b[VHZ]StackLayout\b"):
        assert not re.search(container, rows), (
            f"`{container}` in the rows — they must stay direct lazy children")
    assert "ActivityRow(" not in code


def test_headers_never_resize_in_place():
    """A "first header gets no top padding" conditional changes the OLD first header's height
    in place when a refresh adds a newer day above it."""
    rows = _decl_block(_VIEW.read_text(), "private var rows: some View")
    assert "days.first" not in rows and "?" not in rows.split("CreditHistoryRow(")[0], (
        "the day header's layout must not depend on its position")
    assert ".padding(.top, AppSpacing.md)" in rows


def test_paging_is_a_button_not_scroll_triggered():
    code = _no_preview(_code(_VIEW))
    assert "loadMoreIfNeeded" not in code
    assert code.count(".task") == 1 and ".task { await viewModel.loadAndWait() }" in code, (
        "the only .task is the screen's first load — a per-row .task is scroll-triggered paging")
    assert code.count("viewModel.loadMore()") == 1, (
        "exactly one call site — the button. A second one (`.onAppear`, a row modifier) is "
        "scroll-triggered paging again")
    footer = _flat(_decl_block(_VIEW.read_text(), "private var footer: some View"))
    i_load = footer.index("if viewModel.isLoadingMore {")
    i_more = footer.index("} else if viewModel.hasMore {")
    i_end = footer.rindex("} else {")
    assert i_load < i_more < i_end
    loading, more, end = footer[i_load:i_more], footer[i_more:i_end], footer[i_end:]
    # Each branch holds ITS content. Swapped, a heavy user with older history would read
    # "You've reached the start" and a user at the real end would get a dead button.
    assert "ProgressView()" in loading and "loadMore" not in loading and "Load more" not in loading
    assert "viewModel.loadMore()" in more and '"Load more"' in more
    assert "viewModel.loadMoreFailed" in more
    assert ".contentShape(Rectangle())" in more, "the whole 44pt bar must be the tap target"
    assert "You've reached the start of your credit history." in end
    assert "loadMore" not in end and "Load more" not in end and "ProgressView" not in end
    assert "reached the start" not in loading + more
    for seg in (loading, more, end):
        assert seg.count("minHeight: 44") == 1, "all three footer states share one height"


# ── C. The ViewModel ─────────────────────────────────────────────────


def test_page_size_is_fifty_and_within_the_backend_cap():
    code = _code(_VM)
    m = re.search(r"private static let defaultPageSize = (\d+)", code)
    assert m and int(m.group(1)) == 50
    cap = re.search(r"^MAX_PAGE = (\d+)", _SERVICE.read_text(), re.MULTILINE)
    assert cap, "MAX_PAGE not found in credit_history_service.py — parity check drifted"
    assert int(m.group(1)) <= int(cap.group(1)), (
        "iOS asks for more than the backend serves — the page is silently clamped, and the "
        "button would appear to load fewer rows than it says")


def test_load_more_guards_and_fences():
    more = _flat(_decl_block(_VM.read_text(), "func loadMore()"))
    assert "guard let cursor = nextCursor, !isLoadingMore else { return }" in more
    assert "let started = generation" in more
    perform = _decl_block(_VM.read_text(), "private func performLoadMore(")
    assert perform.count("started == generation") == 3, (
        "the spinner reset, the success path AND the failure path must all check the "
        "generation — a page that lands after a refresh/sign-out must be discarded")
    assert "known.contains($0.id)" in perform, "dedup by id"
    stuck = _flat(perform)
    assert "items.append(contentsOf: page.items.filter { !known.contains($0.id) })" in stuck
    assert stuck.index("items.append(") < stuck.index("regroup()")
    assert "if let next = page.nextCursor, next == cursor {" in stuck
    assert stuck.index("next == cursor") < stuck.index("nextCursor = nil")
    assert "} else { nextCursor = page.nextCursor }" in stuck, (
        "the cursor must advance to the page's own next_cursor")
    catch = perform[perform.index("} catch {"):]
    assert "loadMoreFailed = true" in catch and "reportMutationFailure(" in catch
    assert "log.error(" in catch


def test_refresh_and_reset_discard_an_in_flight_load_more():
    inval = _decl_block(_VM.read_text(), "private func invalidateLoadMore()")
    for token in ("generation += 1", "loadMoreTask?.cancel()", "isLoadingMore = false",
                  "loadMoreFailed = false"):
        assert token in inval, f"invalidateLoadMore lost `{token}`"
    assert "invalidateLoadMore()" in _decl_block(_VM.read_text(), "func load()")
    assert "invalidateLoadMore()" in _decl_block(_VM.read_text(), "func reset()")
    perform_load = _decl_block(_VM.read_text(), "private func performLoad()")
    assert perform_load.count("invalidateLoadMore()") == 3, (
        "signed-out, success and failure must each drop a Load more started on the old list")
    success = perform_load[perform_load.index("do {"):perform_load.index("} catch {")]
    assert success.index("invalidateLoadMore()") < success.index("items = page.items")


def test_the_page_size_override_cannot_ship():
    code = _code(_VM)
    hits = [m.start() for m in re.finditer(r"CAYDEX_CREDIT_PAGE_SIZE", code)]
    assert hits
    for pos in hits:
        opened = code.rfind("#if DEBUG", 0, pos)
        between = code[opened:pos] if opened != -1 else ""
        assert opened != -1 and "#endif" not in between and "#else" not in between
    assert "min(max(size, 1), defaultPageSize)" in code, "the override must stay clamped"


# ── D. The row molecule ──────────────────────────────────────────────


def test_the_row_is_a_pure_molecule():
    code = _no_preview(_code(_ROW))
    for banned in ("AppState", "APIClient", "\\.appState", "URLSession", "Task {"):
        assert banned not in code, f"a molecule may not use {banned}"


def test_the_segment_draws_its_own_light_mode_edge():
    """`test_ios_theme_parity.py` exempts `UnevenRoundedRectangle` rows because "the group
    carries the edge" — here there is no group view, so each segment must stroke it."""
    body = _flat(_decl_block(_ROW.read_text(), "var body: some View"))
    assert ".strokeBorder(AppColors.cardEdge, lineWidth: 1)" in body
    assert ".padding(.top, position.hasRowAbove ? -1 : 0)" in body
    assert ".padding(.bottom, position.hasRowBelow ? -1 : 0)" in body
    assert body.index(".strokeBorder(AppColors.cardEdge") < body.index(".clipped()"), (
        "without the clip the shared top/bottom edges draw as double rules between rows")
    assert body.count(".clipped()") == 1
    assert ".clipped() .accessibilityElement(children: .ignore)" in body, (
        "the clip must be on the ROW — inside the overlay it would not trim the extended edges")
    assert "if position.hasRowAbove { AppColors.divider" in body
    shape = _flat(_decl_block(_ROW.read_text(), "private var segmentShape: UnevenRoundedRectangle"))
    assert "let top: CGFloat = position.hasRowAbove ? 0 : r" in shape
    assert "let bottom: CGFloat = position.hasRowBelow ? 0 : r" in shape


def test_the_rows_text_is_never_truncated():
    """The ticker sits at the END of the headline, so any line cap puts the ellipsis on it —
    measured "Refund · report didn't finish · G…" at the 1.4x cap on a 320pt screen."""
    body = _decl_block(_ROW.read_text(), "var body: some View")
    text_stack = body[body.index("VStack(alignment: .leading"):body.index("Spacer(minLength")]
    assert "lineLimit" not in text_stack and "truncationMode" not in text_stack
    assert text_stack.count(".fixedSize(horizontal: false, vertical: true)") == 2


def test_the_amount_is_never_squeezed_and_is_spoken():
    body = _flat(_decl_block(_ROW.read_text(), "var body: some View"))
    badge = body[body.index("TintedTagBadge("):]
    assert badge.index(".fixedSize()") < badge.index(".padding(")
    assert ".layoutPriority(1)" in badge
    assert ".accessibilityElement(children: .ignore)" in body
    speech = _flat(_decl_block(_ROW.read_text(), "private var accessibilityText: String"))
    assert "item.accessibilityAmount" in speech, "VoiceOver must hear the amount spelled out"


def test_the_model_delegates_to_the_pure_helpers():
    code = _flat(_code(_MODELS))
    assert "var detail: String? { CreditHistoryRowFormat.detail(subtitle) }" in code
    assert ("CreditHistoryRowFormat.metaLine(time: timeOfDay, poolNote: poolNote, "
            "isReversed: isReversed)") in code
    assert "rowSubtitle" not in code and "var footnote" not in code, "the ActivityRow shims are dead"


# ── E. Anti-vacuity ──────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    decoy = (
        "private var rows: some View {\n"
        "    // VStack( ActivityRow( loadMoreIfNeeded\n"
        "    /* LazyVStack(\n  Section */\n"
        "    ForEach(x) { CreditHistoryRow(item: item) }\n"
        "}\n"
        "func neighbour() { VStack(spacing: 0) {} }\n"
    )
    block = _decl_block(decoy, "private var rows: some View")
    for token in ("VStack(", "ActivityRow(", "loadMoreIfNeeded", "Section"):
        assert token not in block, f"{token!r} leaked — comment stripping or bounding is broken"
    assert "CreditHistoryRow(" in block
    assert _no_preview("a\n#Preview { b }") == "a\n"
