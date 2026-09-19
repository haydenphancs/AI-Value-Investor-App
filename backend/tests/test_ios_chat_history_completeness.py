"""The history panel lists EVERY session and says so when it cannot (TestFlight 2026-09-16, E6).

Two invisible regressions, both pinned from the Swift source (no XCTest target):

  1. `ChatViewModel.loadHistory()` fetched ONE `limit: 50` page. The tester's account had
     57 sessions, so the oldest seven were silently absent — no error, no "load more".
     It now walks pages on the server's `has_more` (falling back to the page-length
     heuristic for an older backend), bounded, deduped by id.
  2. A refresh that failed AFTER a list had loaded was invisible: `ChatHistoryView`
     rendered the failed state only when the list was EMPTY, so a stale list stood in
     for the truth. The list is kept (stale beats blank) under a retry notice.

Comment-stripped and brace-bound — the explanatory comments beside each fix contain
every token asserted below.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_VM = _IOS / "ViewModels/ChatViewModel.swift"
_VIEW = _IOS / "Views/Screens/ChatHistoryView.swift"
_MODELS = _IOS / "Models/ChatConversationModels.swift"
_SCREEN = _IOS / "Views/Screens/AIChatScreen.swift"
_ROW = _IOS / "Views/Molecules/ChatHistoryItemRow.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _decl_block(src: str, prefix: str) -> str:
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


# ── 1. every page ────────────────────────────────────────────────────────────

def test_load_history_walks_pages_on_has_more():
    body = _decl_block(_code(_VM), "func loadHistory()")
    assert "while pages < Self.historyMaxPages" in body, "the walk is gone or unbounded"
    assert re.search(r"\.listChatSessions\(limit:\s*Self\.historyPageSize,\s*offset:\s*offset\)", body), \
        "the fetch must page with a moving offset, not a fixed `offset: 0`"
    assert "response.hasMore ??" in body, "must prefer the server's has_more and fall back"
    assert re.search(r"offset \+= response\.sessions\.count", body)


def test_load_history_dedupes_by_id_and_publishes_whole_lists_only():
    body = _decl_block(_code(_VM), "func loadHistory()")
    assert "seen.contains(session.id)" in body
    # Exactly two publishes: the whole list on success, the pages-so-far on failure —
    # never a per-page publish that flashes a partial list mid-walk.
    assert body.count("historyGroups = groupSessionsByDate(all)") == 2
    assert "historyGroups = groupSessionsByDate(response.sessions)" not in body
    loop = body[body.index("while pages < Self.historyMaxPages"):body.index("if pages > 1 {")]
    assert "historyGroups =" not in loop, "no publish inside the page loop"


def test_the_walk_is_bounded():
    vm = _code(_VM)
    m = re.search(r"static let historyMaxPages = (\d+)", vm)
    assert m and 2 <= int(m.group(1)) <= 100
    m = re.search(r"static let historyPageSize = (\d+)", vm)
    assert m and 1 <= int(m.group(1)) <= 100, "the server caps limit at 100"


def test_the_list_dto_decodes_has_more_as_optional():
    dto = _decl_block(_code(_MODELS), "struct ChatSessionListDTO")
    assert re.search(r"\blet hasMore: Bool\?", dto)
    assert 'case hasMore = "has_more"' in dto


# ── 2. a failed refresh is visible ───────────────────────────────────────────

def test_a_failed_refresh_keeps_the_stale_list_but_flags_it():
    body = _decl_block(_code(_VM), "func loadHistory()")
    catch = body[body.index("} catch {"):]
    assert "historyLoadFailed = true" in catch
    assert "historyGroups = []" not in catch and "historyGroups.removeAll" not in catch, \
        "stale beats blank — the last good list must stay on screen"


def test_a_failure_on_a_later_page_publishes_the_pages_already_fetched():
    """Review finding: the walk was all-or-nothing — a 520 on page 2 threw away page 1
    and rendered the failed/blank state where one page used to render."""
    body = _decl_block(_code(_VM), "func loadHistory()")
    catch = body[body.index("} catch {"):]
    assert re.search(r"if !all\.isEmpty \{\s*historySessions = all\s*historyGroups = groupSessionsByDate\(all\)", catch)


def test_a_successful_walk_clears_the_failure_flag_and_is_single_flight():
    """Review finding: the flag was cleared only at entry, and two overlapping walks
    (onAppear + the history tap) could leave a false "out of date" notice over a fresh
    list. The newer walk cancels the older; every publish checks for cancellation."""
    vm = _code(_VM)
    assert re.search(r"private var historyLoadTask: Task<Void, Never>\?", vm)
    body = _decl_block(vm, "func loadHistory()")
    assert "historyLoadTask?.cancel()" in body
    assert "historyLoadTask = Task" in body
    success = body[:body.index("} catch {")]
    assert "historyLoadFailed = false" in success[success.index("historySessions = all"):], \
        "a fresh list must clear the stale flag"
    assert body.count("if Task.isCancelled { return }") >= 3, "each publish/page checks cancellation"


def test_a_multi_page_walk_rereads_the_head():
    """A session bumped to the top mid-walk slips past an offset walk; one more read of
    page 0 after a multi-page walk closes the gap (id dedup covers the other direction)."""
    body = _decl_block(_code(_VM), "func loadHistory()")
    assert "if pages > 1 {" in body
    tail = body[body.index("if pages > 1 {"):]
    assert re.search(r"\.listChatSessions\(limit:\s*Self\.historyPageSize,\s*offset:\s*0\)", tail)
    assert "absorb(head.sessions)" in tail


def test_the_history_view_shows_a_retry_notice_above_a_stale_list():
    body = _decl_block(_code(_VIEW), "var body: some View")
    # The notice sits ABOVE the branch switch (review finding: inside the list arm it
    # never rendered when a search hid every row), gated on loadFailed and not on the
    # list being non-empty.
    notice_at = body.index("InlineRetryNotice(")
    first_branch = body.index("if isLoading && historyGroups.isEmpty {")
    assert notice_at < first_branch, "the notice must precede the branch switch"
    gate = body[:notice_at]
    assert "if loadFailed && !isLoading && !showsFailedState {" in gate
    notice = body[notice_at:]
    assert "out of date" in notice
    assert "onRetry: onRetry" in notice


def test_a_no_match_search_during_a_failed_refresh_is_not_the_failed_state():
    view = _code(_VIEW)
    shows = _decl_block(view, "private var showsFailedState: Bool")
    assert "historyGroups.isEmpty && loadFailed && !isSearching" in shows
    body = _decl_block(view, "var body: some View")
    assert "} else if showsFailedState {" in body


def test_the_screen_wires_the_failure_flag_and_the_retry():
    screen = _code(_SCREEN)
    call = screen[screen.index("ChatHistoryView("):]
    call = call[:call.index("historyActionError")]
    assert "loadFailed: viewModel.historyLoadFailed" in call
    assert "onRetry: { viewModel.loadHistory() }" in call


# ── 3. row density + the 3-dot target ────────────────────────────────────────
#
# Every session may be listed, but at ~140pt a row only four and a half of them
# were on screen. The 3-dot's 44pt tap-target frame sat INSIDE the meta HStack and
# made an 11pt caption line 44pt tall; it is a trailing overlay now, and the row
# reserves that width so a truncated title never runs under the glyph. These pin
# the shape that keeps the target at 44pt WITHOUT the row paying for it.


def _row_label() -> str:
    """The row Button's label — everything between `Button(action:` and `.buttonStyle`."""
    body = _decl_block(_code(_ROW), "struct ChatHistoryItemRow")
    start = body.index("Button(action:")
    end = body.index(".buttonStyle(", start)
    return body[start:end]


def test_the_3_dot_is_an_overlay_not_a_meta_line_child():
    body = _decl_block(_code(_ROW), "struct ChatHistoryItemRow")
    label = _row_label()
    assert "MoreOptionsButton" not in label, (
        "the 3-dot is back inside the row label — its 44pt frame sets the meta line's "
        "height again and the row grows ~30pt"
    )
    overlay_at = body.find(".overlay(alignment: .trailing)")
    assert overlay_at != -1, "the 3-dot overlay is gone"
    overlay = _decl_block(body[overlay_at:], ".overlay(alignment: .trailing)")
    assert "MoreOptionsButton" in overlay
    # The popup anchors under the tapped 3-dot; the anchor must ride with the button.
    assert "anchorPreference(key: ChatRowMenuAnchorKey.self" in overlay


def test_the_row_keeps_the_title_clear_of_the_3_dot_and_stays_tappable():
    label = _row_label()
    # Reserve the overlay's width on the trailing edge, and never let the row shrink
    # below the 44pt target at small Dynamic Type sizes (slop is clipped by the parent).
    assert ".padding(.trailing, HitSlop.minimumTarget)" in label
    assert re.search(r"\.frame\([^)]*minHeight:\s*HitSlop\.minimumTarget", label), \
        "the row no longer holds a 44pt minimum height"
    # A Button hit-tests what its label DRAWS: the shape must come AFTER the padding
    # and frame, or the trailing gap and the Spacer are dead.
    shape_at = label.rindex(".contentShape(Rectangle())")
    assert shape_at > label.rindex(".padding("), "contentShape must follow the padding"
    assert shape_at > label.rindex(".frame("), "contentShape must follow the frame"


def test_the_row_renders_the_title_and_not_the_answer_preview():
    label = _row_label()
    assert "Text(item.title)" in label
    assert "item.preview" not in label, (
        "the answer preview is back in the row — that is the 2-line block the compact "
        "layout removed; search still reads `preview` from the model"
    )
    # The title stays one line: the density gain depends on it.
    title = label[label.index("Text(item.title)"):]
    assert ".lineLimit(1)" in title[:400]
