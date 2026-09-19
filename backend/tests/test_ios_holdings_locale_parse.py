"""The Portfolio Insights config sheet reads its two `.decimalPad` fields through the LOCALE.

`PortfolioConfigRow.toUpdateItem()` used a bare `Double(sharesInput)` / `Double(dollarsInput)`.
Swift's `Double(String)` accepts only "." as the decimal separator, and the fields are
`.decimalPad`, which renders the locale's own — so on de_DE / fr_FR / es_ES / pt_BR the user
typed "12,5", the parse returned nil, `(parsed ?? 0) > 0` was false, and the row went out as
`{shares: null, market_value: null}`: the documented CLEAR. `PUT /portfolios/{id}/holdings`
answered 200, the sheet dismissed with no error, and the holding the user was EDITING was gone.
`PriceAlertsViewModel.parsedThreshold` fixed the same defect for the alert threshold
(`test_silent_degradation_guards.py::test_price_alert_threshold_parsing_is_locale_aware`); this
file pins the holdings twin, and the two halves the alert fix did not need:

  * RENDERING goes through the same separator — `String(12.5)` is always "12.5", and on a
    comma-decimal locale "." is the GROUPING separator, so a prefilled value the user never
    touched would otherwise be re-saved 10x larger;
  * a NON-EMPTY field that does not parse is `.invalid` and blocks Save — it must never be
    sent as a clear. Only an empty field (or "0") clears.

Source-scan (no XCTest target): comments stripped, every assertion brace-bound to the
declaration it names, and mutation-tested by hand (restore the bare `Double(sharesInput)` →
red; restore → green).
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_VIEW = _IOS / "Views" / "Screens" / "TrackingView.swift"


def _stripped() -> str:
    src = _VIEW.read_text(encoding="utf-8")
    # Block comments first, then line comments (a `//` inside a string literal is not a
    # concern in this file — pinned by the anti-vacuity test below).
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return "\n".join(
        "" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line)
        for line in src.splitlines()
    )


def _block(src: str, declaration: str) -> str:
    """The brace-bound body of `declaration` (its first occurrence) — nothing outside it."""
    i = src.find(declaration)
    assert i != -1, f"guard is stale — `{declaration}` not found in TrackingView.swift"
    start = src.index("{", i)
    depth = 0
    for j in range(start, len(src)):
        c = src[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"unbalanced braces after `{declaration}`")


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_stripper_removes_the_comment_that_quotes_the_old_code():
    """The explanatory comment next to the fix quotes `Double(sharesInput)`; an un-stripped
    scan would find (or miss) the token in prose."""
    raw = _VIEW.read_text(encoding="utf-8")
    assert "Double(String)" in raw, "the explanatory comment moved — re-check the stripper"
    stripped = _stripped()
    assert "Double(String)" not in stripped
    assert "func toUpdateItem" in stripped


def test_the_block_extractor_is_brace_bound():
    src = _stripped()
    body = _block(src, "func toUpdateItem")
    assert body.rstrip().endswith("}")
    assert "func setInputMode" not in body, "the block ran past its own closing brace"


# ── the parser ───────────────────────────────────────────────────────────────

def test_the_shared_parser_is_locale_aware():
    body = _block(_stripped(), "enum HoldingsNumberField")
    assert "Locale.current.decimalSeparator" in body, (
        "the decimal separator must come from the locale, not be assumed to be '.'"
    )
    assert "Locale.current.groupingSeparator" in body, (
        "the grouping separator must come from the locale, not be assumed to be ','"
    )
    assert '.replacingOccurrences(of: ",", with: "")' not in body, (
        "a hardcoded comma strip destroys the decimal separator on a comma-decimal locale"
    )
    assert "isFinite" in body, "NaN / inf must not parse as a holding"
    parse = _block(body, "static func parse")
    assert "case invalid" in body and ".invalid" in parse, (
        "a non-empty field that does not parse must be a distinct outcome, not a clear"
    )
    assert "return .empty" in parse, "an empty field is the (only) clear"


def test_rendering_goes_through_the_same_separator():
    """Half two: a prefilled fractional value must be spelled with the locale's separator,
    or the locale-aware parser reads a de_DE "12.5" as 125."""
    src = _stripped()
    fmt = _block(src, "private static func formatNumber")
    assert "HoldingsNumberField.localize(" in fmt, (
        "formatNumber must render through the locale's decimal separator — `String(Double)` "
        "always emits '.', which is the GROUPING separator on a comma-decimal device"
    )
    localize = _block(src, "static func localize")
    assert "decimalSeparator" in localize and 'replacingOccurrences(of: "."' in localize


# ── the two consumers ────────────────────────────────────────────────────────

@pytest.mark.parametrize("declaration", ["func toUpdateItem", "mutating func setInputMode"])
def test_no_bare_double_parse_on_the_input_fields(declaration):
    body = _block(_stripped(), declaration)
    assert "Double(sharesInput)" not in body and "Double(dollarsInput)" not in body, (
        f"{declaration} parses a .decimalPad field with a bare Double() — a comma-decimal "
        "locale turns the edit into a CLEAR"
    )
    assert "HoldingsNumberField.parse(" in body or "parsedActiveInput" in body, (
        f"{declaration} must read the field through the shared locale-aware parser"
    )


def test_to_update_item_cannot_turn_an_unparseable_field_into_a_clear():
    body = _block(_stripped(), "func toUpdateItem")
    assert "valueOrNil" in body
    assert "?? 0" not in body, "the `(parsed ?? 0) > 0 ? parsed : nil` clear-on-nil rule is back"


# ── the sheet blocks Save on an invalid row ──────────────────────────────────

def test_save_is_disabled_and_guarded_while_a_row_is_invalid():
    src = _stripped()
    sheet = _block(src, "struct PortfolioConfigSheet")
    assert "allSatisfy(\\.isValid)" in sheet, "the sheet must aggregate row validity"
    # The Save button's `.disabled(...)` must include the validity, not just isSubmitting.
    m = re.search(r'Button\(isSubmitting \? "Saving…" : "Save"\)(.*?)\.disabled\(([^)]*)\)', sheet, flags=re.DOTALL)
    assert m, "the Save button lost its .disabled modifier"
    assert "allRowsValid" in m.group(2), "Save must be disabled while any row is invalid"
    save = _block(sheet, "private func save()")
    assert "guard allRowsValid else" in save, (
        "save() must refuse an invalid row even if the button state lags"
    )
    assert save.index("guard allRowsValid") < save.index("toUpdateItem()"), (
        "the guard must run BEFORE the payload is built"
    )


def test_an_invalid_row_shows_an_inline_hint():
    row = _block(_stripped(), "private struct PortfolioConfigRowView")
    assert "if !row.isValid" in row and "HoldingsNumberField.invalidHint" in row


def test_the_fields_are_still_decimal_pads():
    """The premise: the keyboard shows the locale's separator."""
    row = _block(_stripped(), "private struct PortfolioConfigRowView")
    assert row.count(".decimalPad") == 2
