"""Conclusion lead-in stripping — the Python server twin, held to the Swift client.

TestFlight 2026-09-10 (ORCL): "Investors should care because Oracle's upcoming earnings
report…". 14 of 31 live cards on 2026-09-27 opened their ↳ conclusion with "Investors
should…". The server strips before storing (`app/services/conclusion_lead_in.py`); iOS
strips at display time for rows that are never regenerated (`BulletTextFormatting.swift`).

Three layers:

1. Case tables for the Python functions — including every sentence the 2026-09-27
   review found a naive stripper would MANGLE.
2. Swift ↔ Python CONSTANT parity, always on: the Swift stem arrays are parsed from
   comment-stripped source and must equal the Python tuples, both ways.
3. Swift ↔ Python BEHAVIOUR parity: the same case table run through the real Swift
   function under `xcrun swift -`. GATED behind ``CAYDEX_SWIFT_HARNESS=1`` — it is a
   Swift compile, and on this 16 GB Mac Swift compiles are one-at-a-time and main-
   session only (CLAUDE.md "Machine safety"), which a subagent's pytest run must never
   start. Run it once, by hand, from the main session.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.conclusion_lead_in import (
    CONTINUATION_WORDS,
    EXACT_STEMS,
    MAX_LEAD_IN,
    MAX_WORDS,
    MIN_REST,
    OPEN_NOUN_STEMS,
    PHRASE_PRONOUNS,
    PHRASE_STEMS,
    display_strip,
    lead_in_remainder,
)

_ROOT = Path(__file__).resolve().parents[2]
_SWIFT = _ROOT / "frontend/ios/ios/Core/Utilities/BulletTextFormatting.swift"

# (input, server result, display result)
CASES = [
    # the TestFlight shapes
    ("Investors should care because Oracle's upcoming earnings report and its massive AI backlog will be key.",
     "Oracle's upcoming earnings report and its massive AI backlog will be key.",
     "Oracle's upcoming earnings report and its massive AI backlog will be key."),
    ("Everyday investors should care because the Fed decision reshapes borrowing costs.",
     "The Fed decision reshapes borrowing costs.",
     "The Fed decision reshapes borrowing costs."),
    ("Why should investors care? Because the backlog now exceeds a year of revenue.",
     "The backlog now exceeds a year of revenue.",
     "The backlog now exceeds a year of revenue."),
    ("This matters because, unlike peers, Oracle funds capex from cash flow.",
     "Unlike peers, Oracle funds capex from cash flow.",
     "Unlike peers, Oracle funds capex from cash flow."),
    ("Why it matters: the backlog must convert into revenue before the debt bill arrives.",
     "The backlog must convert into revenue before the debt bill arrives.",
     "The backlog must convert into revenue before the debt bill arrives."),
    ("For investors, the dividend cut signals a longer downturn ahead.",
     "The dividend cut signals a longer downturn ahead.",
     "The dividend cut signals a longer downturn ahead."),
    ("For everyday investors, the rate cut lowers savings yields everywhere.",
     "The rate cut lowers savings yields everywhere.",
     "The rate cut lowers savings yields everywhere."),
    ("The takeaway for everyday investors, While AI drives innovation, costs are rising.",
     "While AI drives innovation, costs are rising.",
     "While AI drives innovation, costs are rising."),
    # the docstring table from test_ios_conclusion_marker.py
    ("The takeaway: Investors should watch the guidance closely next quarter.",
     "Investors should watch the guidance closely next quarter.",
     "Investors should watch the guidance closely next quarter."),
    ("In short, watch two things: capex and buybacks next quarter",
     "Watch two things: capex and buybacks next quarter",
     "Watch two things: capex and buybacks next quarter"),
    ("So what: this changes very little for the long-term thesis.",
     "This changes very little for the long-term thesis.",
     "This changes very little for the long-term thesis."),
    # stacked: the server loops, the client makes one pass
    ("In short, investors should care because the backlog is turning into revenue.",
     "The backlog is turning into revenue.",
     "Investors should care because the backlog is turning into revenue."),
    # capitalisation keeps internally cased words
    ("In short, iPhone demand is holding up better than the bears expected.",
     "iPhone demand is holding up better than the bears expected.",
     "iPhone demand is holding up better than the bears expected."),
    ("INVESTORS SHOULD CARE BECAUSE the backlog now exceeds a year of revenue.",
     "The backlog now exceeds a year of revenue.",
     "The backlog now exceeds a year of revenue."),
    # review 2026-09-27: a remainder starting with a number is a sentence start
    ("In short, 80% of Oracle's revenue now comes from cloud contracts and backlog.",
     "80% of Oracle's revenue now comes from cloud contracts and backlog.",
     "80% of Oracle's revenue now comes from cloud contracts and backlog."),
    ("Investors should care because 80% of Oracle's revenue now comes from cloud.",
     "80% of Oracle's revenue now comes from cloud.",
     "80% of Oracle's revenue now comes from cloud."),
    ("Why it matters: $38 billion of new debt funds the data-center build.",
     "$38 billion of new debt funds the data-center build.",
     "$38 billion of new debt funds the data-center build."),
]

# Must come back UNCHANGED from the server; the client only ever applies its colon rule.
UNCHANGED = [
    "Sony, the electronics maker, reported stronger console sales this quarter.",
    "So the Fed cut rates, and the market rallied into the close on Friday.",
    "The takeaway, buy.",
    "For investors, especially retirees, the cut lowers savings yields across the board.",
    "For investors, however, the dividend is not yet approved by the board.",
    "For investors in Europe, rates matter more than the dollar this year.",
    "Why it matters is unclear, but analysts flagged the filing anyway.",
    "Why it matters — and to whom — depends on the Fed's next move entirely.",
    "The takeaway, though, is that capex is peaking for the hyperscalers.",
    "What this means, in practice, is higher mortgage rates for new buyers.",
    "Ultimately — and this is the key point — the Fed decides the next move.",
    "This matters because of Oracle's $38 billion data-center debt raise.",
    "Investors should care because they may face higher borrowing costs soon.",
    "Investors should watch how Oracle navigates the delays at Project Jupiter.",
    "Investors should care because rates fell.",
    "Investors are pricing in two cuts, which lifted small caps sharply.",
    "Overall revenue rose 8%, led by cloud infrastructure growth.",
    "In short supply, memory chips are lifting margins across the sector.",
    "This matters less than guidance, which was cut for the full year.",
    '"Investors should care because the backlog now exceeds a year of revenue."',
    # review 2026-09-27: manglings the guards now refuse
    "Investors should care because — as the filing shows — the backlog is finally converting.",
    "This matters because – unlike peers – Oracle funds capex from its own cash flow.",
    '"So, the backlog now exceeds a full year of revenue."',
    "“In short,” analysts said, the stock remains range-bound for now.",
    "Investors should care because rising yields raise their borrowing costs across the board.",
    "Earnings at 4:05 p.m. ET showed cloud revenue up 12% year over year.",
]


@pytest.mark.parametrize("text,server,display", CASES)
def test_strip_table(text, server, display):
    assert lead_in_remainder(text) == server
    assert display_strip(text) == display


@pytest.mark.parametrize("text", UNCHANGED)
def test_never_mangles(text):
    assert lead_in_remainder(text) == text
    assert display_strip(text) in (text, _colon(text))


def _colon(text):
    idx = text.find(":")
    if idx < 0 or idx > 40:
        return text
    if 0 < idx < len(text) - 1 and text[idx - 1].isdigit() and text[idx + 1].isdigit():
        return text
    return f"{text[:idx]}, {text[idx + 1:].lstrip(' ')}"


def test_a_clock_time_is_never_turned_into_a_comma():
    assert display_strip("Earnings at 4:05 p.m. ET showed cloud revenue up 12% year over year.") == (
        "Earnings at 4:05 p.m. ET showed cloud revenue up 12% year over year."
    )
    assert display_strip("Two risks: rates and capex, both rising into the quarter.") == (
        "Two risks, rates and capex, both rising into the quarter."
    )


@pytest.mark.parametrize("text", [c[0] for c in CASES] + UNCHANGED)
def test_server_strip_is_idempotent_and_never_degenerate(text):
    once = lead_in_remainder(text)
    assert lead_in_remainder(once) == once
    assert once.strip()
    if once != text.strip():
        assert len(once) >= MIN_REST


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_input_is_safe(bad):
    assert lead_in_remainder(bad) == ""


def test_the_server_never_rewrites_a_colon():
    text = "Two risks remain: rates and capex, both rising into the fourth quarter."
    assert lead_in_remainder(text) == text
    assert display_strip(text) == "Two risks remain, rates and capex, both rising into the fourth quarter."


# ── Swift ↔ Python constants (always on) ─────────────────────────────────────


def _swift_code() -> str:
    lines = []
    for line in _SWIFT.read_text().splitlines():
        if line.strip().startswith("//"):
            continue
        lines.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(lines)


def _swift_rows(name: str) -> set:
    m = re.search(rf"let {name}: \[\[String\]\] = \[(.*?)\n\]", _swift_code(), re.S)
    assert m, f"{name} not found in {_SWIFT.name}"
    return {tuple(re.findall(r'"([^"]*)"', row)) for row in re.findall(r"\[([^\[\]]*)\]", m.group(1))}


def _swift_set(name: str) -> set:
    m = re.search(rf"let {name}: Set<String> = \[(.*?)\]", _swift_code(), re.S)
    assert m, f"{name} not found in {_SWIFT.name}"
    return set(re.findall(r'"([^"]*)"', m.group(1)))


@pytest.mark.parametrize("swift_name,python", [
    ("conclusionLeadInExactStems", EXACT_STEMS),
    ("conclusionLeadInOpenNounStems", OPEN_NOUN_STEMS),
    ("conclusionLeadInPhraseStems", PHRASE_STEMS),
])
def test_stem_arrays_match_both_ways(swift_name, python):
    swift = _swift_rows(swift_name)
    assert len(swift) >= 4, f"{swift_name} parsed to {len(swift)} rows — the parser is broken"
    assert swift == set(python), (
        f"{swift_name} drifted from the Python stems: "
        f"only-Swift={sorted(swift - set(python))} only-Python={sorted(set(python) - swift)}"
    )


def test_word_sets_match():
    assert _swift_set("conclusionLeadInContinuationWords") == set(CONTINUATION_WORDS)
    assert _swift_set("conclusionLeadInPhrasePronouns") == set(PHRASE_PRONOUNS)


def test_numeric_guards_match():
    code = _swift_code()
    assert f"conclusionLeadInMaxWords = {MAX_WORDS}" in code
    assert f"maxLeadIn: Int = {MAX_LEAD_IN}" in code
    counts = set(re.findall(r"rest\.count >= (\d+)", code))
    assert counts == {str(MIN_REST)}, f"remainder guards disagree: {counts}"


def test_the_parser_ignores_commented_rows():
    """Anti-vacuity: a commented-out row must not count as a stem."""
    fake = 'private let x: [[String]] = [\n    ["a"],\n    // ["b"],\n]\n'
    lines = [l for l in fake.splitlines() if not l.strip().startswith("//")]
    m = re.search(r"let x: \[\[String\]\] = \[(.*?)\n\]", "\n".join(lines), re.S)
    rows = {tuple(re.findall(r'"([^"]*)"', r)) for r in re.findall(r"\[([^\[\]]*)\]", m.group(1))}
    assert rows == {("a",)}


def test_the_swift_file_stays_foundation_only():
    code = _swift_code()
    assert "import Foundation" in code
    assert "import SwiftUI" not in code and "import UIKit" not in code, (
        "BulletTextFormatting must stay Foundation-only, or the behaviour harness below "
        "cannot run it standalone"
    )


# ── Swift ↔ Python behaviour (gated: a Swift compile) ─────────────────────────

_KEEP_CASES = [
    ([], 5, []),
    (["a"], 5, ["a"]),
    (["a", "b", "c", "d", "e"], 5, ["a", "b", "c", "d", "e"]),
    (["a", "b", "c", "d", "e"], 4, ["a", "b", "c", "e"]),
    (["a", "b", "c", "d", "e", "f"], 5, ["a", "b", "c", "d", "f"]),
    (["a", "b"], 1, ["b"]),
    (["a"], 0, []),
]

_HARNESS = r'''
struct StripCase: Decodable { let input: String; let expected: String }
struct KeepCase: Decodable { let input: [String]; let limit: Int; let expected: [String] }
let strips = try! JSONDecoder().decode([StripCase].self, from: STRIPS.data(using: .utf8)!)
let keeps = try! JSONDecoder().decode([KeepCase].self, from: KEEPS.data(using: .utf8)!)
var failures = 0
for (i, c) in strips.enumerated() {
    let got = c.input.strippingConclusionLeadIn()
    if got == c.expected { print("ok|s\(i)") } else { failures += 1; print("FAIL|s\(i)|got=\(got)") }
}
for (i, c) in keeps.enumerated() {
    let got = c.input.keepingConclusion(limit: c.limit)
    if got == c.expected { print("ok|k\(i)") } else { failures += 1; print("FAIL|k\(i)|got=\(got)") }
}
print("DONE|\(failures)")
'''


@pytest.mark.skipif(
    os.environ.get("CAYDEX_SWIFT_HARNESS") != "1",
    reason="Swift compile — main session only (CLAUDE.md Machine safety); set CAYDEX_SWIFT_HARNESS=1",
)
def test_swift_behaves_exactly_like_the_python_port():
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    inputs = [c[0] for c in CASES] + UNCHANGED + [
        "Two risks remain: rates and capex, both rising into the fourth quarter.",
    ]
    strips = [{"input": t, "expected": display_strip(t)} for t in inputs]
    keeps = [{"input": a, "limit": n, "expected": e} for a, n, e in _KEEP_CASES]
    src = (
        _SWIFT.read_text()
        + '\nlet STRIPS = #"""\n' + json.dumps(strips) + '\n"""#\n'
        + 'let KEEPS = #"""\n' + json.dumps(keeps) + '\n"""#\n'
        + _HARNESS
    )
    proc = subprocess.run(["xcrun", "swift", "-"], input=src, text=True,
                          capture_output=True, timeout=300)
    if "DONE|" not in proc.stdout:
        pytest.fail(f"the Swift harness did not complete.\nstdout:\n{proc.stdout[-3000:]}\n"
                    f"stderr:\n{proc.stderr[-4000:]}")
    fails = [l for l in proc.stdout.splitlines() if l.startswith("FAIL|")]
    assert not fails, "\n".join(fails)
    assert proc.stdout.count("ok|") == len(strips) + len(keeps)
