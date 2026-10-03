"""iOS guard: the report's Insider section must say "no activity" / "unavailable" honestly.

There is no XCTest target, so this scans the Swift source per `.claude/rules/testing.md` §3
— comment-stripped and brace-bounded, so it cannot pass on the prose next to the fix.

The defects (NYAX, 2026-10-03):

* `ReportInsiderActivityTable.hasInsiderActivity` and its chart gate tested
  `!flow.flowData.isEmpty`. The backend always sends 13 month buckets, so both were always
  true: the empty state was unreachable and a ticker with no counted trades showed a
  "Neutral" pill, a "Buys 0 / Sells 0" table and a ±1M axis of zero bars. The gate must
  read each bucket's `hasActivity`, as `SmartMoneySection` already did.
* The chart used `SmartMoneyFlowChart`'s defaults (no gap, natural label widths), so the
  volume axis's top label ran into the price axis's bottom one ("1M$40") and the two plots
  had different widths. It must pass the Holders tab's `priceVolumeGap` + `uniformVolumeAxis`.
* A failed backend insider fetch (`unavailable: true`) must not render as "no insider
  transactions".
* Holders' insider summary painted an exactly-zero net flow as "+ 0 shares" in green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_TABLE = _IOS / "Views/Molecules/ReportInsiderActivityTable.swift"
_DTO = _IOS / "Models/TickerReportResponse.swift"
_HOLDERS_MODELS = _IOS / "Models/HoldersModels.swift"
_RECENT = _IOS / "Views/Organisms/RecentActivitiesSection.swift"
_REPORT_MODELS = _IOS / "Models/TickerReportModels.swift"
_SMART_MONEY = _IOS / "Views/Organisms/SmartMoneySection.swift"
_REPO = _IOS / "Core/Repositories/StockRepository.swift"


def _src(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return _strip_comments(path.read_text())


def _strip_comments(src: str) -> str:
    """Drop `//` / `///` comments — whole lines AND trailing ones: the comments beside these
    fixes name every token the assertions grep for, so an un-stripped scan would pass after
    the code is reverted. A `//` inside a string literal (an even quote count before it is
    required) is left alone."""
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        idx = line.find("//")
        while idx != -1:
            if line[:idx].count('"') % 2 == 0:
                line = line[:idx].rstrip()
                break
            idx = line.find("//", idx + 2)
        out.append(line)
    return "\n".join(out)


def _decl_body(src: str, header: str) -> str:
    """The brace-bounded body of one declaration (a whole-file scan passes when the token
    lives in a different type)."""
    start = src.index(header)
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


def _table() -> str:
    return _decl_body(_src(_TABLE), "struct ReportInsiderActivityTable")


def test_the_activity_gate_reads_each_bucket_not_the_bucket_count():
    body = _table()
    gate = _decl_body(body, "private var hasInsiderActivity")
    flow = _decl_body(body, "private var flowHasActivity")
    assert "flowHasActivity" in gate
    assert "flowData.isEmpty" not in gate, "13 zero buckets are not activity"
    assert ".hasActivity" in flow and "contains" in flow


def test_the_chart_is_gated_on_activity_and_uses_the_holders_layout():
    body = _table()
    start = body.index("SmartMoneyFlowChart(")
    call = body[start:body.index(")\n", start)]
    head = body[body.rindex("if let flow", 0, start):start]
    assert "flowHasActivity" in head and "flowData.isEmpty" not in head
    assert "uniformVolumeAxis: true" in call
    assert "priceVolumeGap:" in call and "priceVolumeGap: 0" not in call


def test_an_unavailable_section_says_so_and_shows_no_badge():
    body = _table()
    assert "insiderData.isUnavailable" in body
    assert "couldn't be loaded" in body
    badge_guard = body[:body.index("ReportSentimentBadge(")].rsplit("if ", 1)[1]
    assert "isUnavailable" in badge_guard


def test_the_popup_buckets_months_in_the_parsing_time_zone():
    counts = _decl_body(_table(), "private var monthlyInsiderCounts")
    assert 'TimeZone(identifier: "UTC")' not in counts
    assert "cal.timeZone = .current" in counts


def test_the_dto_decodes_unavailable_as_optional():
    dto = _decl_body(_src(_DTO), "struct InsiderDataDTO")
    assert "let unavailable: Bool?" in dto
    assert "unavailable" in _decl_body(dto, "enum CodingKeys")


def test_a_flat_insider_net_flow_is_neither_signed_nor_green():
    summary = _decl_body(_src(_HOLDERS_MODELS), "struct InsiderActivitySummary")
    color = _decl_body(summary, "var netFlowColor")
    assert "AppColors.textSecondary" in color
    flow = _decl_body(summary, "var formattedNetFlow")
    assert ">= 0" not in flow and ">= 0" not in _decl_body(summary, "var isNetPositive")


def test_the_holders_insider_list_has_an_empty_state():
    content = _decl_body(_src(_RECENT), "private var insidersContent")
    assert "displayedActivities.isEmpty" in content
    assert "InsiderFilterSelector(selectedFilter: InsiderActivityFilterOption.binding($insiderFilterID))" in content


def test_the_report_model_flag_has_no_default_and_the_mapping_sets_it():
    """A defaulted `isUnavailable` let the DTO mapping drop it with every test green."""
    model = _decl_body(_src(_REPORT_MODELS), "struct ReportInsiderData")
    assert "let isUnavailable: Bool\n" in model + "\n" and "isUnavailable: Bool =" not in model
    dto_file = _src(_DTO)
    call = dto_file[dto_file.index("let insider = ReportInsiderData("):]
    call = call[:call.index("\n        )\n")]
    assert "isUnavailable: insiderData.unavailable == true" in call


def test_the_holders_insider_tab_says_unavailable_not_no_activity():
    content = _decl_body(_src(_RECENT), "private var insidersContent")
    unavailable_at = content.index("data.insiderActivities.isUnavailable")
    assert unavailable_at < content.index("InsiderFlowSummaryCard("), "the 0/0 card would show first"
    assert "couldn't be loaded" in content
    section = _src(_SMART_MONEY)
    assert section.index("currentData.isUnavailable") < section.index(
        "currentData.flowData.allSatisfy({ !$0.hasActivity })"
    ), "the 'no activity' empty state must not answer for a failed fetch"


def test_the_holders_dtos_decode_unavailable_as_optional():
    repo = _src(_REPO)
    smart = _decl_body(repo, "struct SmartMoneyDataDTO")
    acts = _decl_body(repo, "struct InsiderActivitiesDataDTO")
    for dto in (smart, acts):
        assert "let unavailable: Bool?" in dto
        assert "isUnavailable: unavailable == true" in dto
    assert "unavailable" in _decl_body(smart, "enum CodingKeys")
