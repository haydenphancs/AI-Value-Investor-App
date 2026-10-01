"""Round-3 source-scan guards: degraded Financials builds on iOS (findings P11, P22).

There is no XCTest target, so these read the Swift source (testing.md §3): comments are
stripped before any assertion, and every check is brace-bound to the ONE function or
block it is about, so a token living in a sibling fetcher or in prose cannot satisfy it.

P11 — Growth, Profit Power, Health Check and Signal of Confidence answer an upstream FMP
failure with a 200 whose `degraded` names the failed leg and whose series are empty. Only
`fetchEarnings` turned that into a `FinancialsFailure`; the other four returned nil, so
the card vanished (or Profit Power said "Margin data isn't available for this company")
and the tab offered no Try Again. A DATA leg (not "profile"/"benchmarks", and not a
reason that describes the company's own filings) on an effectively empty section now
returns `FinancialsFailure(section:, message: nil)`; the partial cases carry an
`isDegraded` flag into the cards so an emptied series reads "temporarily unavailable".

P22 — `financialsContext` told Cay AI "N reported quarters had no analyst estimate" for a
build that lost the earnings feed (every quarter then falls to `.noEstimate`). A degraded
earnings record now says the record is partial and unknown instead.

Every assertion here FAILS on the pre-fix source (verified by hand-mutation).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"
_PP_CHART = _IOS / "Views" / "Molecules" / "ProfitPowerChartView.swift"
_PP_CARD = _IOS / "Views" / "Organisms" / "ProfitPowerSectionCard.swift"
_GROWTH_CARD = _IOS / "Views" / "Organisms" / "GrowthSectionCard.swift"
_SERVICES = _REPO / "backend" / "app" / "services"


def _strip_swift_comments(src: str) -> str:
    """Drop `/* */` blocks and `//` line comments (a `//` inside a string literal is
    kept: the scan walks the line and ignores `//` while inside quotes)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out_lines = []
    for line in src.splitlines():
        in_str = False
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and in_str:
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str and line.startswith("//", i):
                cut = i
                break
            i += 1
        out_lines.append(line[:cut])
    return "\n".join(out_lines)


def _block_from(src: str, open_brace: int) -> str:
    """The text from the `{` at `open_brace` through its matching `}`."""
    assert src[open_brace] == "{", src[open_brace: open_brace + 40]
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace: i + 1]
    raise AssertionError("unbalanced braces")


def _decl_body(src: str, decl_regex: str) -> str:
    """Brace-bound body of the ONE declaration matching `decl_regex`."""
    matches = list(re.finditer(decl_regex, src))
    assert len(matches) == 1, f"{decl_regex!r} matched {len(matches)} times"
    return _block_from(src, src.index("{", matches[0].end()))


def _vm() -> str:
    return _strip_swift_comments(_VM.read_text())


def _fetcher(name: str) -> str:
    return _decl_body(_vm(), rf"\bfunc\s+{name}\s*\(")


def _failure_branch(body: str, condition_regex: str, section: str) -> str:
    """The `if <condition> { ... }` block whose condition matches, which must return the
    message-less failure naming `section`."""
    m = re.search(rf"\bif\s+{condition_regex}\s*\{{", body)
    assert m, f"no `if {condition_regex}` branch in the fetcher"
    block = _block_from(body, m.end() - 1)
    assert re.search(
        rf'return\s+FinancialsFailure\(\s*section:\s*"{re.escape(section)}"\s*,\s*message:\s*nil\s*\)',
        block,
    ), f"the degraded branch does not return FinancialsFailure(section: {section!r}, message: nil)"
    return block


# ── Helpers are not vacuous ──────────────────────────────────────────────────


def test_the_comment_stripper_ignores_prose_and_keeps_strings():
    sample = (
        '// return FinancialsFailure(section: "Growth", message: nil)\n'
        "/* if dto.isEmptyPayload, !failedLegs.isEmpty { } */\n"
        'let url = "https://x.test" // trailing\n'
    )
    out = _strip_swift_comments(sample)
    assert "FinancialsFailure" not in out
    assert "isEmptyPayload" not in out
    assert '"https://x.test"' in out and "trailing" not in out


def test_the_brace_bound_scan_does_not_leak_into_the_next_function():
    src = "func a() { if x { y() } }\nfunc b() { z() }"
    assert _decl_body(src, r"\bfunc\s+a\s*\(") == "{ if x { y() } }"


# ── P11: the data-leg classification ─────────────────────────────────────────


def _non_data_reasons() -> set[str]:
    m = re.search(
        r"static\s+let\s+nonDataLegReasons\s*:\s*Set<String>\s*=\s*\[(.*?)\]", _vm(), re.S,
    )
    assert m, "TickerDetailViewModel.nonDataLegReasons is missing"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_peer_and_company_fact_reasons_never_offer_a_retry():
    reasons = _non_data_reasons()
    # Peer-only gaps leave the company series intact; the other three describe the
    # company's own filings — a retry returns the same answer.
    assert {"profile", "benchmarks", "no_metrics", "cash_flow_row",
            "cash_flow_statement_missing"} <= reasons


@pytest.mark.parametrize("leg", [
    # growth / profit power
    "annual_income", "quarterly_income", "annual_cashflow", "quarterly_cashflow",
    # health check
    "ratios", "key_metrics", "balance_sheet", "income",
    # signal of confidence
    "cash_flow", "annual_ratios",
])
def test_a_failed_data_leg_is_never_classified_as_a_company_fact(leg):
    assert leg not in _non_data_reasons()


def test_every_non_data_reason_is_one_the_backend_actually_emits():
    """Anti-typo: a misspelled reason would silently classify nothing."""
    backend = "\n".join(
        (_SERVICES / name).read_text()
        for name in ("growth_service.py", "profit_power_service.py",
                     "health_check_service.py", "signal_of_confidence_service.py")
    )
    for reason in _non_data_reasons():
        assert f'"{reason}"' in backend, f"{reason!r} is not a degraded reason any service emits"


def test_the_classifier_filters_out_the_non_data_reasons():
    body = _decl_body(_vm(), r"\bstatic\s+func\s+failedDataLegs\s*\(")
    assert re.search(r"\(\s*degraded\s*\?\?\s*\[\]\s*\)\.filter", body)
    assert re.search(r"!\s*nonDataLegReasons\.contains\(\s*\$0\s*\)", body)


# ── P11: the four fetchers ───────────────────────────────────────────────────


@pytest.mark.parametrize("fetcher, section", [
    ("fetchGrowth", "Growth"),
    ("fetchProfitPower", "Profit Power"),
    ("fetchSignalOfConfidence", "Signal of Confidence"),
])
def test_an_empty_build_that_lost_a_data_leg_offers_a_retry(fetcher, section):
    body = _fetcher(fetcher)
    assert re.search(r"let\s+failedLegs\s*=\s*Self\.failedDataLegs\(\s*dto\.degraded\s*\)", body)
    _failure_branch(body, r"dto\.isEmptyPayload\s*,\s*!failedLegs\.isEmpty", section)


def test_health_check_with_nothing_scored_after_a_failed_leg_offers_a_retry():
    body = _fetcher("fetchHealthCheck")
    assert re.search(r"let\s+failedLegs\s*=\s*Self\.failedDataLegs\(\s*dto\.degraded\s*\)", body)
    _failure_branch(body, r"model\.totalCount\s*==\s*0\s*,\s*!failedLegs\.isEmpty", "Health Check")


def test_a_raw_degraded_check_is_not_used_by_the_four_fetchers():
    """Triggering on ANY `degraded` would put a retry notice on a complete card whose only
    gap is the peer line ("benchmarks") — the corrected fix's explicit exclusion."""
    for name in ("fetchGrowth", "fetchProfitPower", "fetchHealthCheck", "fetchSignalOfConfidence"):
        body = _fetcher(name)
        assert not re.search(r"let\s+reasons\s*=\s*dto\.degraded", body), name
        assert not re.search(r"!\s*\(\s*dto\.degraded\s*\?\?\s*\[\]\s*\)\.isEmpty", body), name


def test_an_emptied_profit_power_draws_no_company_fact_card():
    """The only thing an empty Profit Power card draws is "Margin data isn't available for
    this company" — the defect. The degraded-empty branch drops the card (as Growth,
    Health Check and SoC already do) BEFORE the payload is assigned."""
    body = _fetcher("fetchProfitPower")
    block = _failure_branch(body, r"dto\.isEmptyPayload\s*,\s*!failedLegs\.isEmpty", "Profit Power")
    assert re.search(r"self\.profitPowerData\s*=\s*nil", block)
    assign = re.search(r"self\.profitPowerData\s*=\s*dto\.toDisplayModel\(\)", body)
    assert assign and assign.start() > body.index(block), (
        "the display model must be assigned only after the degraded-empty branch returned"
    )


@pytest.mark.parametrize("fetcher, flag", [
    ("fetchGrowth", "growthIsDegraded"),
    ("fetchProfitPower", "profitPowerIsDegraded"),
])
def test_the_partial_flag_follows_the_data_legs_and_clears_on_failure(fetcher, flag):
    body = _fetcher(fetcher)
    assert re.search(rf"self\.{flag}\s*=\s*!failedLegs\.isEmpty", body)
    catch = _block_from(body, body.index("{", re.search(r"\}\s*catch\b", body).end() - 1))
    assert re.search(rf"self\.{flag}\s*=\s*false", catch)


# ── P11: the cards' degraded wording ─────────────────────────────────────────


def test_profit_power_empty_state_says_temporarily_unavailable_when_degraded():
    src = _strip_swift_comments(_PP_CHART.read_text())
    chart = _decl_body(src, r"\bstruct\s+ProfitPowerChartView\s*:\s*View\s*")
    assert re.search(r"\bvar\s+isDegraded\s*:\s*Bool\s*=\s*false", chart)
    body = _decl_body(chart, r"\bvar\s+body\s*:\s*some\s+View\s*")
    m = re.search(r"ChartUnavailableView\(\s*message:\s*isDegraded\s*\?\s*\"([^\"]+)\"\s*:\s*\"([^\"]+)\"", body)
    assert m, "the empty state does not branch on isDegraded"
    assert "temporarily unavailable" in m.group(1)
    assert "for this company" not in m.group(1)
    assert m.group(2) == "Margin data isn't available for this company."


def test_profit_power_card_forwards_its_flag_to_the_chart():
    src = _strip_swift_comments(_PP_CARD.read_text())
    card = _decl_body(src, r"\bstruct\s+ProfitPowerSectionCard\s*:\s*View\s*")
    assert re.search(r"\bvar\s+isDegraded\s*:\s*Bool\s*=\s*false", card)
    chart_call = re.search(r"ProfitPowerChartView\((.*?)\)\s*\.padding", card, re.S)
    assert chart_call and re.search(r"isDegraded:\s*isDegraded", chart_call.group(1))


def test_growth_card_shows_a_temporarily_unavailable_note_when_degraded():
    src = _strip_swift_comments(_GROWTH_CARD.read_text())
    card = _decl_body(src, r"\bstruct\s+GrowthSectionCard\s*:\s*View\s*")
    # The original two-argument init stays byte-identical (test_growth_deepcheck_ios pins
    # it); the flag arrives through a delegating init, defaulting to false.
    init = _decl_body(card, r"\binit\s*\(\s*growthData:\s*GrowthSectionData\s*,\s*isDegraded:\s*Bool\s*,")
    assert re.search(r"self\.init\(\s*growthData:\s*growthData\s*,\s*onDetailTapped:\s*onDetailTapped\s*\)", init)
    assert re.search(r"self\.isDegraded\s*=\s*isDegraded", init)
    assert re.search(r"\bvar\s+isDegraded\s*:\s*Bool\s*=\s*false", card)
    m = re.search(r"\bif\s+isDegraded\s*\{", card)
    assert m and "partialDataNote" in _block_from(card, m.end() - 1)
    note = _decl_body(card, r"\bvar\s+partialDataNote\s*:\s*some\s+View\s*")
    assert "temporarily unavailable" in note


# ── P22: Cay AI's earnings context ───────────────────────────────────────────


def _earnings_context_block() -> str:
    ctx = _decl_body(_vm(), r"\bvar\s+financialsContext\s*:\s*String\?\s*")
    m = re.search(r"\bif\s+let\s+ed\s*=\s*earningsData\s*\{", ctx)
    assert m, "financialsContext no longer reads earningsData"
    return _block_from(ctx, m.end() - 1)


def test_a_degraded_earnings_record_is_reported_as_partial_not_as_no_coverage():
    block = _earnings_context_block()
    assert re.search(
        r'let\s+recordIsPartial\s*=\s*ed\.degraded\.contains\s*\{\s*\$0\s*!=\s*"prices"\s*\}', block,
    )
    m = re.search(r"\bif\s+recordIsPartial\s*\{", block)
    assert m
    partial = _block_from(block, m.end() - 1)
    assert "partial right now" in partial
    assert "had no analyst estimate" not in partial and "beats" not in partial
    rest = block[m.end() - 1 + len(partial):]
    e = re.match(r"\s*else\s*\{", rest)
    assert e, "the coverage lines must sit in the else branch of recordIsPartial"
    complete = _block_from(rest, e.end() - 1)
    assert "had no analyst estimate" in complete and "beats" in complete
    # Nothing outside the two branches may still claim coverage.
    outside = block.replace(partial, "").replace(complete, "")
    assert "had no analyst estimate" not in outside


def test_next_earnings_is_still_given_on_a_degraded_build():
    block = _earnings_context_block()
    m = re.search(r"\bif\s+recordIsPartial\s*\{", block)
    partial = _block_from(block, m.end() - 1)
    rest = block[m.end() - 1 + len(partial):]
    e = re.match(r"\s*else\s*\{", rest)
    complete = _block_from(rest, e.end() - 1)
    tail = rest[e.end() - 1 + len(complete):]
    assert "Next Earnings:" in tail
