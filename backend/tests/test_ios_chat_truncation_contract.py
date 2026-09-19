"""A cut answer must LOOK cut on iOS (TestFlight 2026-09-16, E1).

The MATIC answer ended at "For Polygon (MATIC), the" and the app rendered a finished turn:
chips, timestamp, nothing amiss. The backend now marks such a row `truncated: true` (wire:
`ChatMessageResponse.truncated`, rich_content-backed) and sends a single "Continue your
answer" chip. These source scans pin the iOS half of that contract, which is invisible when
it regresses: drop the DTO field and every cut answer silently renders complete again.

Comment-stripped and brace-bound, per testing.md — `AIMessageContent`'s own comments
contain every token below.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_MODELS = _IOS / "Models/ChatConversationModels.swift"
_CONTENT = _IOS / "Views/Molecules/AIMessageContent.swift"
_LIST = _IOS / "Views/Organisms/ChatMessagesList.swift"
_VM = _IOS / "ViewModels/ChatViewModel.swift"


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


def test_the_dto_decodes_truncated_as_optional_bool():
    """Optional, so a backend that predates the field (and every complete turn, which
    omits it) decodes unchanged — the crash-on-decode class the parity tests exist for."""
    dto = _decl_block(_code(_MODELS), "struct ChatMessageDTO")
    assert re.search(r"\blet truncated: Bool\?", dto), "ChatMessageDTO.truncated must be `Bool?`"
    keys = _decl_block(dto, "enum CodingKeys")
    assert re.search(r"\btruncated\b", keys), "the CodingKey is missing — the field would never decode"


def test_the_ui_model_carries_the_mark_from_the_dto():
    models = _code(_MODELS)
    rich = _decl_block(models, "struct RichChatMessage")
    assert re.search(r"\bvar truncated: Bool\b", rich)
    convert = _decl_block(_decl_block(models, "struct ChatMessageDTO"), "func toRichChatMessage()")
    assert re.search(r"truncated:\s*msgRole == \.assistant && \(truncated \?\? false\)", convert), (
        "toRichChatMessage must pass the mark through (assistant rows only, nil → false)"
    )


def test_the_done_frame_rebuild_keeps_the_mark():
    """The live turn is rebuilt field by field on `done`; a missing `truncated:` there
    renders the streamed answer complete while a history reload shows it cut short."""
    vm = _code(_VM)
    done = vm[vm.index('case "done":'):]
    done = done[:done.index('case "error":')]
    assert re.search(r"truncated:\s*base\.truncated", done)


def test_the_list_passes_the_mark_to_the_message_view():
    block = _decl_block(_code(_LIST), "private var assistantMessage")
    assert re.search(r"truncated:\s*message\.truncated", block)


def test_a_cut_answer_renders_a_notice_gated_on_the_mark_only():
    """The notice must show on EVERY render of a cut row (history included) — gated on
    `truncated`, never on `showFollowUps`/`isLast` — and must not carry its own button:
    the server-sent Continue chip is the single CTA."""
    body = _decl_block(_code(_CONTENT), "var body: some View")
    m = re.search(r"if truncated, !isStreaming, !thinkingActive \{(.*?)\n            \}", body, flags=re.S)
    assert m, "the truncated notice block is missing or its gate changed"
    block = m.group(1)
    assert "InlineRetryNotice(" in block
    assert "cut short" in block
    assert "onRetry: nil" in block, "one CTA: the Continue chip, not a second button"
    assert "showFollowUps" not in block and "isLast" not in block


def test_the_message_view_defaults_the_mark_off():
    """Every other caller / preview constructs the view without it."""
    content = _code(_CONTENT)
    assert re.search(r"\bvar truncated: Bool = false\b", content)
