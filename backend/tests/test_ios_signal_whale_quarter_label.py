"""The whale drill-down labels a 13F row by QUARTER, never "Filed <date>" (2026-10-09).

A 13F row's `date` is the quarter END its filing reports holdings for (the hydrators write
FMP's institutional-ownership `date`, with a `{y}-{q*3:02d}-30` fallback) — never the day
the 13F was filed. `SignalDetailFormat.whaleDate` used to render it "Filed Jun 30", so a
fund that filed in mid-August read as having filed on the last day of the quarter. It now
reads "Q2 2026 13F", derived from the month alone (the fallback 03-30 / 12-30 dates land in
the right quarter).

Source scan (testing.md §3): comments are stripped first — the doc comment above
`whaleDate` explains the old "Filed" label — and the check is brace-bound to the function,
because "Filed" legitimately stays in `insiderDate` (a Form 4 IS filed).
`SignalDetailModels.swift` imports SwiftUI, so it cannot run in the executed Swift harness.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MODELS = _REPO / "frontend/ios/ios/Models/SignalDetailModels.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _func_body(code: str, header: str) -> str:
    start = code.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = code.index("{", start)
    depth = 0
    for i in range(open_brace, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[open_brace:i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


def _code() -> str:
    return _strip_comments(_MODELS.read_text())


def test_whale_date_labels_the_13f_quarter_not_a_filing_day():
    body = _func_body(_code(), "static func whaleDate(")
    assert '"Filed' not in body, "a 13F row's date is the quarter END, not a filing date"
    assert re.search(r'"Q\\\(\w+\) \\\(\w+\) 13F"', body), "expected the 'Q2 2026 13F' label"
    # The quarter comes from the MONTH (1-12 → Q1-Q4), never from the day.
    assert re.search(r"\(\s*month\s*-\s*1\s*\)\s*/\s*3\s*\+\s*1", body)
    assert "(1...12).contains(month)" in body


def test_the_whale_row_uses_the_quarter_label_and_the_ceo_row_keeps_filed():
    code = _code()
    to_display = _func_body(code, "func toDisplay(kind: String) -> SignalHolder")
    whale_arm = to_display[to_display.index('case "whale":'):to_display.index('case "ceo":')]
    assert "SignalDetailFormat.whaleDate(" in whale_arm
    # A Form 4 IS filed — the CEO row's "Filed …" is correct and must not be swept up.
    assert '"Filed' in _func_body(code, "static func insiderDate(")


def test_the_scan_still_sees_the_explanatory_comment():
    # Anti-vacuity: the raw file still explains WHY (so the strip above is load-bearing).
    raw = _MODELS.read_text()
    assert "QUARTER END" in raw and "never the day the 13F was" in raw
