"""Source-scan guards: the report's Revenue Engine card shows the REPORTING currency.

The card formatted every amount with "$", so TSM's TWD segments read as US dollars (a
3.8T TWD year shown as "$3.80T", ~32x too large). The backend carries
`revenue_engine.reporting_currency` (an ISO code or None, `app/utils/currency.py`); iOS now
decodes it as an OPTIONAL field (reports cached before 2026-10-08 have no key) and prefixes
every amount with `moneyPrefix`: "$" for USD and for unknown — the card's old behaviour —
else the code ("TWD 3.80T"), the backend's `money_prefix` rule.

There is no XCTest target, so the Swift is pinned from Python. Each guard strips comments
first and is BRACE-BOUND to the declaration it means; each predicate is proved non-vacuous
on mutated source inside its test (break the Swift → the predicate fails).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.utils.currency import currency_code, money_prefix

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_REPORT_DTOS = _IOS / "Models/TickerReportResponse.swift"
_REVENUE_MODELS = _IOS / "Models/RevenueEngineModels.swift"
_REVENUE_SECTION = _IOS / "Views/Organisms/ReportRevenueEngineSection.swift"


def _strip_comments(src: str) -> str:
    """Drop `//` line comments and `/* */` blocks, leaving string literals intact."""
    out = []
    i, n = 0, len(src)
    in_str = False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _read(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return path.read_text(encoding="utf-8")


def _decl_body(src: str, decl_regex: str) -> str:
    """The brace-balanced body of the FIRST declaration matching `decl_regex`."""
    m = re.search(decl_regex, src)
    assert m, f"declaration not found: {decl_regex}"
    start = src.index("{", m.end() - 1 if src[m.end() - 1] == "{" else m.end())
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {decl_regex!r}")


_DTO_DECL = r"struct\s+RevenueEngineDTO\s*:\s*Codable\s*\{"
_DATA_DECL = r"struct\s+ReportRevenueEngineData\s*\{"
_SEGMENT_DECL = r"struct\s+RevenueSegment\s*:\s*Identifiable\s*\{"


# ── 1. The DTO decodes the key OPTIONALLY and maps it through the normaliser ──────────


def _dto_decodes_currency_optionally(raw: str) -> bool:
    src = _strip_comments(raw)
    dto = _decl_body(src, _DTO_DECL)
    convert = _decl_body(src, r"func\s+toTickerReportData\s*\(\s*\)\s*->\s*TickerReportData\s*\{")
    return bool(
        re.search(r"let\s+reportingCurrency\s*:\s*String\?", dto)
        and re.search(r'case\s+reportingCurrency\s*=\s*"reporting_currency"', dto)
        # Synthesized Decodable uses decodeIfPresent for an Optional: an absent key → nil.
        # A hand-written decoder could `decode` it and crash every older cached report.
        and "init(from" not in dto
        and re.search(
            r"reportingCurrency\s*:\s*ReportRevenueEngineData\.currencyCode\(\s*"
            r"revenueEngine\.reportingCurrency\s*\)",
            convert,
        )
    )


def test_the_dto_decodes_reporting_currency_optionally_and_maps_it():
    raw = _read(_REPORT_DTOS)
    assert _dto_decodes_currency_optionally(raw)
    # Non-vacuous: a REQUIRED field (old reports would fail to decode) trips it…
    assert not _dto_decodes_currency_optionally(
        raw.replace("let reportingCurrency: String?", "let reportingCurrency: String", 1))
    # …a wrong wire key trips it…
    assert not _dto_decodes_currency_optionally(
        raw.replace('= "reporting_currency"', '= "reportingCurrency"', 1))
    # …and dropping the value at the DTO → domain boundary trips it.
    assert not _dto_decodes_currency_optionally(
        raw.replace("reportingCurrency: ReportRevenueEngineData.currencyCode(", "_x: (", 1))


# ── 2. The domain model carries it as an Optional defaulting to nil ───────────────────


def test_the_domain_model_carries_an_optional_currency_defaulting_to_nil():
    data = _decl_body(_strip_comments(_read(_REVENUE_MODELS)), _DATA_DECL)
    # `var … = nil` so every existing memberwise init and preview still compiles.
    assert re.search(r"var\s+reportingCurrency\s*:\s*String\?\s*=\s*nil", data)


# ── 3. No amount formatter on the card hardcodes "$" ──────────────────────────────────


def _formatters_are_currency_aware(raw: str) -> bool:
    src = _strip_comments(raw)
    data = _decl_body(src, _DATA_DECL)
    segment = _decl_body(src, _SEGMENT_DECL)
    fmt = _decl_body(data, r"func\s+formatMillions\s*\(\s*_\s+value\s*:\s*Double\s*\)\s*->\s*String\s*\{")
    total = _decl_body(data, r"var\s+formattedTotalRevenue\s*:\s*String\s*\{")
    elim = _decl_body(data, r"var\s+formattedEliminations\s*:\s*String\s*\{")
    row = _decl_body(data, r"func\s+formattedRevenue\s*\(\s*for\s+segment\s*:\s*RevenueSegment\s*\)\s*->\s*String\s*\{")
    return bool(
        "Self.moneyPrefix(reportingCurrency)" in fmt
        # No literal dollar sign in any money format string of either type.
        and '"$%' not in data and '"$%' not in segment
        and "formatMillions(totalRevenue)" in total
        and "formatMillions(e)" in elim
        and "formatMillions(segment.currentRevenue)" in row
        # The row no longer formats itself: a per-row "$" formatter is the old trap.
        and not re.search(r"var\s+formattedRevenue\s*:", segment)
    )


def test_every_amount_formatter_goes_through_the_currency_prefix():
    raw = _read(_REVENUE_MODELS)
    assert _formatters_are_currency_aware(raw)
    # Non-vacuous: the old hardcoded tier formatter trips it…
    assert not _formatters_are_currency_aware(raw.replace(
        'return prefix + String(format: "%.2fT"', 'return String(format: "$%.2fT"', 1))
    # …a total that bypasses the shared formatter trips it…
    assert not _formatters_are_currency_aware(
        raw.replace("formatMillions(totalRevenue)", 'String(totalRevenue)', 1))
    # …and a prefix that ignores the currency trips it.
    assert not _formatters_are_currency_aware(
        raw.replace("Self.moneyPrefix(reportingCurrency)", 'Self.moneyPrefix(nil)', 1))


# ── 4. The card draws every amount from the currency-aware data ──────────────────────


def _section_uses_currency_aware_amounts(raw: str) -> bool:
    src = _strip_comments(raw)
    view = _decl_body(src, r"struct\s+ReportRevenueEngineSection\s*:\s*View\s*\{")
    header = _decl_body(view, r"private\s+var\s+headerSection\s*:\s*some\s+View\s*\{")
    card = _decl_body(view, r"private\s+func\s+segmentCard\s*\(")
    elim = _decl_body(view, r"private\s+var\s+eliminationsRow\s*:\s*some\s+View\s*\{")
    return bool(
        "data.formattedTotalRevenue" in header
        and "data.formattedRevenue(for: segment)" in card
        and "data.formattedEliminations" in elim
        and "segment.formattedRevenue" not in view
        and '"$' not in view
    )


def test_the_card_draws_every_amount_through_the_data_formatters():
    raw = _read(_REVENUE_SECTION)
    assert _section_uses_currency_aware_amounts(raw)
    assert not _section_uses_currency_aware_amounts(
        raw.replace("data.formattedRevenue(for: segment)", "segment.formattedRevenue", 1))
    assert not _section_uses_currency_aware_amounts(
        raw.replace("Text(data.formattedTotalRevenue)", 'Text("$" + data.period)', 1))
    # A comment naming the old formatter neither satisfies nor trips the guard.
    assert _section_uses_currency_aware_amounts(
        raw.replace("Text(data.formattedRevenue(for: segment))",
                    "// segment.formattedRevenue \"$\"\n                Text(data.formattedRevenue(for: segment))", 1))


def test_the_card_keeps_a_preview_of_a_non_usd_filer():
    src = _strip_comments(_read(_REVENUE_SECTION))
    assert "ReportRevenueEngineData.sampleForeignCurrency" in src
    sample = _strip_comments(_read(_REVENUE_MODELS))
    m = re.search(r"static\s+let\s+sampleForeignCurrency\s*=\s*ReportRevenueEngineData\(", sample)
    assert m and 'reportingCurrency: "TWD"' in sample[m.start():m.start() + 1200]


# ── 5. The Swift rule mirrors the backend's `currency_code` / `money_prefix` ─────────


def _swift_rule_mirrors_backend(raw: str) -> bool:
    data = _decl_body(_strip_comments(raw), _DATA_DECL)
    code = _decl_body(data, r"static\s+func\s+currencyCode\s*\(\s*_\s+raw\s*:\s*String\?\s*\)\s*->\s*String\?\s*\{")
    prefix = _decl_body(data, r"static\s+func\s+moneyPrefix\s*\(\s*_\s+currency\s*:\s*String\?\s*\)\s*->\s*String\s*\{")
    letters = re.search(r"static\s+let\s+asciiLetters\s*:\s*CharacterSet\s*=\s*([^\n]+\n[^\n]*)", data)
    return bool(
        # trim → exactly three scalars (not Characters) → ASCII letters only → upper-case
        "trimmingCharacters(in: .whitespacesAndNewlines)" in code
        and ".unicodeScalars" in code and "scalars.count == 3" in code
        and "asciiLetters.contains" in code and "uppercased()" in code
        and letters and '"A"..."Z"' in letters.group(1) and '"a"..."z"' in letters.group(1)
        and "CharacterSet.letters" not in data   # would admit "Ü"
        # "$" for unknown AND USD, else "CODE "
        and re.search(r'guard\s+let\s+code\s*=\s*currencyCode\(currency\)\s*,\s*code\s*!=\s*"USD"\s*'
                      r'else\s*\{\s*return\s+"\$"\s*\}', prefix)
        and 'code + " "' in prefix
    )


def test_the_swift_currency_rule_mirrors_the_backend_rule():
    raw = _read(_REVENUE_MODELS)
    assert _swift_rule_mirrors_backend(raw)
    assert not _swift_rule_mirrors_backend(raw.replace('code != "USD"', 'code != "EUR"', 1))
    assert not _swift_rule_mirrors_backend(raw.replace("scalars.count == 3", "scalars.count >= 3", 1))
    assert not _swift_rule_mirrors_backend(raw.replace(
        'CharacterSet(charactersIn: "A"..."Z").union(CharacterSet(charactersIn: "a"..."z"))',
        "CharacterSet.letters", 1))


# The table the Swift rule above is written to (backend half, executed). Includes the
# outliers the card can meet: a lower-case or padded code, garbage, non-ASCII letters that
# upper-case INTO ASCII ("ßU" → "SSU" must stay unknown), and None for an old report.
@pytest.mark.parametrize("raw, prefix", [
    ("USD", "$"), ("usd", "$"), (" USD ", "$"),
    (None, "$"), ("", "$"), ("   ", "$"), ("US$", "$"), ("N/A", "$"), ("USDT", "$"),
    ("ÜSD", "$"), ("ßU", "$"), ("X" * 10_000, "$"),
    ("TWD", "TWD "), ("twd", "TWD "), (" Twd\n", "TWD "), ("EUR", "EUR "), ("JPY", "JPY "),
])
def test_the_backend_rule_table_the_swift_mirrors(raw, prefix):
    assert money_prefix(raw) == prefix
    code = currency_code(raw)
    assert code is None or (len(code) == 3 and code.isascii() and code.isupper())
