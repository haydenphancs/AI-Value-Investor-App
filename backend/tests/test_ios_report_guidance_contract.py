"""The report's Company Guidance badge renders a READ stance or nothing (E1, TestFlight 2026-09-16).

"Earnings Call Transcripts" is off the FMP Order Form, so every report's
`management_guidance` arrives as "unknown". The shipped build mapped anything it did not
recognise to `.maintained` and rendered the badge unconditionally — a constant shown as
data on every report. Three iOS invariants, pinned from the Swift source (no XCTest
target), comment-stripped and brace-bound so a comment beside the fix cannot satisfy them:

  1. the DTO field is Optional (a row without the key still decodes);
  2. `mapGuidance` returns an Optional and has no `.maintained` fallback arm;
  3. the section renders the block only under `if let` on the mapped stance.

Backend parity: `test_ticker_report_schema_parity.py::test_revenue_forecast_unknown_guidance_is_a_valid_wire_value`.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_DTO = _IOS / "Models/TickerReportResponse.swift"
_MODELS = _IOS / "Models/TickerReportModels.swift"
_SECTION = _IOS / "Views/Organisms/ReportFutureForecastSection.swift"


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


def test_the_dto_field_is_optional():
    dto = _decl_block(_code(_DTO), "struct RevenueForecastDTO")
    assert re.search(r"let managementGuidance:\s*String\?", dto), (
        "RevenueForecastDTO.managementGuidance must be `String?` — a required String "
        "throws on a row without the key"
    )


def test_map_guidance_returns_optional_with_no_maintained_fallback():
    src = _code(_DTO)
    at = src.find("func mapGuidance(")
    assert at != -1
    sig = src[at:src.index("{", at)]
    assert "-> ManagementGuidance?" in sig, "mapGuidance must return an Optional stance"
    assert "String?" in sig, "mapGuidance must accept the Optional DTO value"
    body = _decl_block(src, "func mapGuidance(")
    assert "default: return nil" in body, "anything but a read stance must map to nil"
    # Pin the arms POSITIVELY (review finding, 2026-09-19: `case "unknown", "maintained":
    # return .maintained` satisfied the old negative check and put the badge back on
    # every report). Exactly three labels, each mapping to its own case, no `unknown`.
    arms = re.findall(r'case\s+([^:]+):\s*return\s+\.(\w+)', body)
    labels = {}
    for label_expr, target in arms:
        for label in re.findall(r'"([^"]+)"', label_expr):
            labels[label] = target
    assert labels == {"raised": "raised", "lowered": "lowered", "maintained": "maintained"}, labels
    assert all(len(re.findall(r'"[^"]+"', expr)) == 1 for expr, _ in arms), (
        "a case arm carries more than one label — a second string routed to a stance"
    )
    assert "unknown" not in body.lower()
    assert "nil" not in "".join(expr for expr, _ in arms)


def test_the_ui_model_carries_an_optional_stance():
    model = _decl_block(_code(_MODELS), "struct ReportRevenueForecast")
    assert re.search(r"let managementGuidance:\s*ManagementGuidance\?", model)


def test_the_section_renders_guidance_only_under_if_let():
    src = _code(_SECTION)
    body = _decl_block(src, "var body: some View")
    m = re.search(r"if let (\w+) = forecast\.managementGuidance\s*\{\s*companyGuidance\(\1\)", body)
    assert m, "companyGuidance must be gated on `if let … = forecast.managementGuidance`"
    # And it is not ALSO rendered unconditionally somewhere else in the body.
    assert body.count("companyGuidance(") == 1
    assert not re.search(r"^\s*companyGuidance\s*$", body, flags=re.M), (
        "an unconditional `companyGuidance` line is back"
    )
    # The block reads the enum it was handed, never a defaulted model field.
    block = _decl_block(src, "private func companyGuidance(")
    assert "forecast.managementGuidance" not in block
