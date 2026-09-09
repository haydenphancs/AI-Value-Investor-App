"""Swift conversions that TRAP (crash) rather than throw — none may ship.

Swift has two failure styles and they are not interchangeable. A `throw` is recoverable
and, in a decoder, costs one field. A TRAP is `fatalError` — it kills the app, cannot be
caught, and leaves no error anywhere. Three of these shipped:

  * `Int(Double)` traps on NaN, ±Infinity, and anything outside Int64's range.
    `TrackingView.formatNumber` ran `String(Int(value))` behind only a
    `truncatingRemainder(dividingBy: 1) == 0` check, so typing 20 digits into the
    unbounded `.decimalPad` Shares field and switching to Dollars crashed the config
    sheet. `MoneyMovesContentModels`' two "lenient" decoders — whose entire stated
    purpose is that one bad value cannot break the payload — had the same hole, where
    it is worse: a content JSONB carrying `1e30` crashes the app at DECODE time.

  * `Dictionary(uniqueKeysWithValues:)` traps on a duplicate key.
    `EarningsTimelineChart` keyed on fiscal YEAR, which the backend derives as
    `int(date[:4])` with no dedupe — a company that moved its fiscal year-end ships two
    points for one calendar year and the chart crashed on open. Six more sites keyed on
    `ticker.uppercased()`, where two rows differing only in case collapse onto one key.

Comment-stripped per .claude/rules/testing.md §3 — the fixes left prose naming every
token this file greps for, so an unstripped scan would pass on the explanation.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

# Preview/mock scaffolding is allowed to be loose; it never sees server data.
_EXEMPT_SUFFIXES = ("PalettePreview.swift", "AppearanceProbe.swift", "ThemeContrastAudit.swift")


def _swift_files():
    for p in sorted(_IOS.rglob("*.swift")):
        if p.name.endswith(_EXEMPT_SUFFIXES):
            continue
        yield p


def _stripped(path: pathlib.Path) -> str:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def test_no_dictionary_is_built_with_the_trapping_initialiser():
    """`uniquingKeysWith:` is always available and never traps — use it."""
    offenders = []
    for p in _swift_files():
        for i, line in enumerate(_stripped(p).splitlines(), 1):
            if "uniqueKeysWithValues" in line:
                offenders.append(f"{p.relative_to(_IOS)}:{i}")
    assert offenders == [], (
        "Dictionary(uniqueKeysWithValues:) TRAPS on a duplicate key — a hard crash, not a "
        f"catchable error. Use `uniquingKeysWith:`. Offenders: {offenders}"
    )


def test_no_unguarded_int_conversion_of_a_double():
    """`Int(someDouble)` needs BOTH a finite check and a magnitude check.

    The guard must be within a few lines — `Int(...)` on an already-`Int` expression is
    fine, so this only flags conversions whose argument is a known Double-valued name.
    """
    offenders = []
    pattern = re.compile(r"\bInt\((?!try|Int|UInt)([A-Za-z_][A-Za-z0-9_.]*)\)")
    for p in _swift_files():
        lines = _stripped(p).splitlines()
        for i, line in enumerate(lines):
            for m in pattern.finditer(line):
                arg = m.group(1)
                # Only Double-ish argument names are candidates.
                if not re.search(r"(value|d|price|amount|shares|total|num)$", arg, re.I):
                    continue
                # `Int(String)` is FAILABLE (returns an Optional) and never traps — only
                # a Double argument is dangerous. A `guard let`/`if let` on the same line
                # proves the failable overload is in play.
                if re.search(r"\b(guard|if)\s+let\b", line):
                    continue
                window = "\n".join(lines[max(0, i - 8): i + 1])
                has_finite = "isFinite" in window or "finiteOrNil" in window
                has_bound = any(t in window for t in
                                ("magnitude", "9_007_199", "9.2e18", "Int64", "clamp"))
                if has_finite and has_bound:
                    continue
                # `rounded()`-only guards are NOT sufficient: 1e30 is a whole number.
                offenders.append(f"{p.relative_to(_IOS)}:{i + 1}  Int({arg})")
    assert offenders == [], (
        "Int(Double) TRAPS on NaN, +/-Infinity and anything outside Int64. A "
        "`truncatingRemainder`/`rounded()` check does NOT cover magnitude — 1e30 is a "
        f"whole number. Guard with isFinite AND a magnitude bound. Offenders: {offenders}"
    )


@pytest.mark.parametrize("name", [
    "Views/Screens/TrackingView.swift",
    "Models/MoneyMovesContentModels.swift",
    "Views/Molecules/EarningsTimelineChart.swift",
])
def test_the_three_files_that_shipped_a_trap_still_carry_their_guard(name):
    """Anti-vacuity: pins the specific fixes, so a whole-tree regex change cannot hide one."""
    src = _stripped(_IOS / name)
    assert "isFinite" in src or "uniquingKeysWith" in src, (
        f"{name} lost the guard that stopped it trapping"
    )
