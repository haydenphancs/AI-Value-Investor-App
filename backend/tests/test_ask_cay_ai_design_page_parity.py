"""`caydex-ask-cay-ai-system-design.html` names files, symbols, settings and SSE frames.

WHY THIS EXISTS. The page is a JS-data-driven stage map that was last regenerated on
2026-07-20 and then silently rotted for seven weeks: it said the guardrail was log-only
(it redacts), that there were 3 tools (7, per asset class), "≤3 of 7 lenses"
(`CHAT_MAX_SPECIALISTS=2`), that only the fallback path touched the deep-dive cache, and
it did not mention credits at all — while a credit was pre-charged on every turn.
Nothing failed, because nothing read the page. This does: every `files` entry
("path" or "path · symbol"), every `CHAT_*` / `GEMINI_*` setting name and every SSE frame
name it claims must exist in the code it describes.

Source-level only: no build, no network, comments stripped before matching so prose
about a rename cannot satisfy the check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PAGE = _REPO / "documents" / "System Design" / "caydex-ask-cay-ai-system-design.html"
_BACKEND = _REPO / "backend" / "app"
_IOS = _REPO / "frontend" / "ios" / "ios"

_ROOTS = [
    _IOS,
    _BACKEND,
    _BACKEND / "api" / "v1" / "endpoints",
    _BACKEND / "services",
    _BACKEND / "services" / "agents",
    _BACKEND / "integrations",
]


def _page() -> str:
    assert _PAGE.exists(), f"missing {_PAGE}"
    return _PAGE.read_text(encoding="utf-8")


def _data_block() -> str:
    src = _page()
    start = src.index("const STAGES = [")
    end = src.index("/* ============================================================================\n   RENDER")
    return src[start:end]


def _file_entries() -> list[str]:
    """Every string inside a `files:[...]` array, in page order."""
    out: list[str] = []
    for arr in re.findall(r"files:\[(.*?)\]", _data_block(), flags=re.S):
        out.extend(re.findall(r'"([^"]+)"', arr))
    return out


def _strip_comments(src: str, swift: bool) -> str:
    if swift:
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))


def _resolve(rel: str) -> Path | None:
    for root in _ROOTS:
        cand = root / rel
        if cand.is_file():
            return cand
    return None


def _defines(src: str, sym: str) -> bool:
    """A declaration of `sym`, not a mention: def/func/class/struct/actor/enum/var/let,
    a module-level assignment, or a Swift `func sym(` with a label."""
    base = sym.split("(", 1)[0]
    pat = (
        rf"^\s*(?:@\w+\s+)*(?:(?:private|fileprivate|public|internal|static|final|nonisolated|async)\s+)*"
        rf"(?:def|func|class|struct|actor|enum|var|let|case)\s+{re.escape(base)}\b"
        rf"|^{re.escape(base)}\s*(?::|=)"
    )
    return re.search(pat, src, flags=re.M) is not None


def test_the_scan_finds_the_page_and_its_entries():
    """Anti-vacuity: the page has ~20 stages and ~60 file entries."""
    entries = _file_entries()
    assert len(entries) >= 40, f"found only {len(entries)} file entries — regex drifted?"
    assert sum(1 for e in entries if " · " in e) >= 25, "almost no `path · symbol` entries"
    assert "const PATHS" in _page() and "const GUARANTEES" in _page()


@pytest.mark.parametrize("entry", sorted(set(_file_entries())))
def test_every_file_and_symbol_the_page_names_exists(entry):
    rel, _, sym = entry.partition(" · ")
    path = _resolve(rel.strip())
    assert path is not None, f"the page names `{rel}` but no such file exists under the known roots"
    if sym:
        swift = path.suffix == ".swift"
        src = _strip_comments(path.read_text(encoding="utf-8", errors="ignore"), swift=swift)
        assert _defines(src, sym.strip()), (
            f"the page names `{sym}` in {rel}, but nothing declares it there any more"
        )


def _settings_named() -> set[str]:
    return set(re.findall(r"<code>((?:CHAT|GEMINI|REPORT)_[A-Z0-9_]+)</code>", _data_block()))


def test_every_setting_or_error_code_the_page_names_exists():
    """UPPER_SNAKE names in <code> are either a `config.py` setting or an `ErrorCode`."""
    from app.api.error_response import ErrorCode

    names = _settings_named()
    assert len(names) >= 8, f"expected many setting names, found {sorted(names)}"
    config = _strip_comments((_BACKEND / "config.py").read_text(encoding="utf-8"), swift=False)
    codes = {c.value for c in ErrorCode}
    missing = sorted(
        n for n in names
        if not re.search(rf"^\s+{n}\s*:", config, flags=re.M) and n not in codes
    )
    assert not missing, f"the page names these, but they are neither a setting nor an ErrorCode: {missing}"


# Frames the page describes → how each side handles them. `suggestions` is emitted and
# ignored by iOS (the chips land on `done`). There is NO `widget` frame: the internal
# ("widget", payload) generator event is collected into the terminal `done` message.
_FRAMES_BOTH_SIDES = ["meta", "sources", "routing", "reasoning", "token", "tool_step",
                      "reset", "credits", "done", "error"]
_FRAMES_SERVER_ONLY = ["suggestions"]


def _endpoint_src() -> str:
    return _strip_comments(
        (_BACKEND / "api" / "v1" / "endpoints" / "chat.py").read_text(encoding="utf-8"), swift=False)


@pytest.mark.parametrize("frame", _FRAMES_BOTH_SIDES + _FRAMES_SERVER_ONLY)
def test_every_sse_frame_the_page_names_is_emitted(frame):
    # EMISSION sites only — a quoted dict key elsewhere in chat.py must not satisfy this
    # (`"sources"`, `"credits"`, `"suggestions"` are also rich_content keys).
    assert re.search(rf'_sse\(\s*"{frame}"', _endpoint_src()), (
        f"chat.py no longer emits the `{frame}` frame"
    )
    assert f"<code>{frame}" in _data_block(), f"the page stopped describing `{frame}`"


def test_there_is_no_widget_frame_and_the_page_says_so():
    src = _endpoint_src()
    assert not re.search(r'_sse\(\s*"widget"', src), "a `widget` SSE frame appeared — update the page"
    assert 'kind == "widget"' in src, "widgets are collected from the internal generator event"
    block = _data_block()
    assert "<code>widget</code> and <code>suggestions</code> frames are informational" not in block
    assert "NO <code>widget</code> frame" in block or "no <code>widget</code> frame" in block


@pytest.mark.parametrize("frame", _FRAMES_BOTH_SIDES)
def test_every_two_sided_frame_has_an_ios_arm(frame):
    vm = _strip_comments(
        (_IOS / "ViewModels" / "ChatViewModel.swift").read_text(encoding="utf-8"), swift=True)
    assert f'case "{frame}"' in vm, f"ChatViewModel has no `case \"{frame}\"` arm"


def test_the_page_states_the_real_specialist_cap_and_tool_count():
    """The two numbers that rotted first."""
    from app.config import settings
    from app.services.agents.chat_tools import TOOL_DESCRIPTIONS

    block = _data_block()
    assert f"≤{settings.CHAT_MAX_SPECIALISTS} of 7 lenses" in block
    assert f"(≤{settings.CHAT_MAX_SPECIALISTS})" in block
    assert len(TOOL_DESCRIPTIONS) == 7, "the page's per-class tool table lists 7 tools"
    for tool in ("explain_price_move", "get_market_overview"):
        assert tool in block or tool.replace("get_", "") in block


def test_the_page_no_longer_carries_the_known_rot():
    """Each of these was a live claim on the 2026-07-20 revision and was false."""
    block = _data_block().lower()
    for stale in ("log-only", "log only", "≤3 of 7", "3 of 7 lenses",
                  "only</b> this path checks", "identical persisted message"):
        assert stale not in block, f"stale claim is back: {stale!r}"
    assert "402" in block and "settle_no_cost" in block and "refund_once" in block
