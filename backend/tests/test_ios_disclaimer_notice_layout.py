"""The inline disclaimer must read as ONE centred paragraph with "Details" as its last
word (TestFlight E10, build 1.0 (8)).

`InlineDisclaimerNotice` laid prose and link out as two `HStack` siblings; once the prose
wrapped, its lines centred inside their own column while "Details" sat beside them,
vertically centred between the two lines. The two meter cards (`TechnicalAnalysisSection`,
`SentimentAnalysisSection`) additionally sit in a `.leading` VStack with a Spacer-centred
meter, so the wrapped block pinned left under a centred gauge. Concatenation keeps the
single tap target — the whole `Text` is the Button's label — and the hosts get full width.

Comment-stripped, brace-bounded (`.claude/rules/testing.md` §3).
"""
from __future__ import annotations

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_ATOM = _IOS / "Views" / "Atoms" / "InlineDisclaimerNotice.swift"
_TECH = _IOS / "Views" / "Organisms" / "TechnicalAnalysisSection.swift"
_SENT = _IOS / "Views" / "Organisms" / "SentimentAnalysisSection.swift"
_VALU = _IOS / "Views" / "Organisms" / "ValuationMeterSection.swift"


def _strip(src: str) -> str:
    """Block, full-line AND trailing `//` comments — a trailing comment on a code line
    must not satisfy an `in` assertion."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _code(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip(path.read_text(encoding="utf-8"))


def _decl(src: str, prefix: str) -> str:
    at = src.index(prefix)
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after {prefix!r}")


def test_inline_disclaimer_notice_has_no_hstack_in_its_body():
    struct = _decl(_code(_ATOM), "struct InlineDisclaimerNotice")
    body = _decl(struct, "var body: some View")
    assert "HStack(" not in body, "prose and link are siblings again — the link detaches on wrap"
    assert ".underline()" in struct and "Button {" in body  # anti-vacuity


def test_inline_disclaimer_notice_concatenates_prose_and_link():
    struct = _decl(_code(_ATOM), "struct InlineDisclaimerNotice")
    label = _decl(struct, "private var label: Text")
    assert re.search(r"prose\s*\+\s*Text\(\" \"\)\s*\+\s*link", label), label
    assert 'Text("\\u{00A0}")' not in label and "\\u{00a0}" not in label.lower(), "a plain space, not NBSP"


def test_inline_disclaimer_notice_keeps_the_empty_text_and_link_cases():
    struct = _decl(_code(_ATOM), "struct InlineDisclaimerNotice")
    label = _decl(struct, "private var label: Text")
    assert "(false, true)" in label and "(true, false)" in label and "(true, true)" in label
    body = _decl(struct, "var body: some View")
    assert body.count("Button {") == 1, "the whole line must stay ONE tap target"
    assert ".buttonStyle(.plain)" in body
    assert ".multilineTextAlignment(.center)" in body


def test_meter_card_disclaimers_fill_the_card_width():
    for path, token in ((_TECH, "AnalysisDisclaimerText.rating"),
                        (_SENT, "AnalysisDisclaimerText()"),
                        (_VALU, "AnalysisDisclaimerText.fairValue")):
        body = _decl(_code(path), "var body: some View")
        i = body.index(token)
        assert ".frame(maxWidth: .infinity)" in body[i: i + 120], f"{path.name}: the footer is not full width"
        assert body.count("Spacer()") >= 2, f"{path.name}: the meter is no longer Spacer-centred"  # anti-vacuity
