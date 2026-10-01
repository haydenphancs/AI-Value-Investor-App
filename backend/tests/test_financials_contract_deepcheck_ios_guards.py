"""
Source-scan guards for the iOS half of the Financials contract/state fixes (W8, 2026-09-30).

There is no XCTest target, so these read the Swift. Every scan strips comments first (the
explanatory comment beside each fix names every token we look for) and brace-bounds the exact
declaration it means (the mock repository at the bottom of StockRepository.swift declares the
same six getters). Each guard was mutation-tested once by hand.

What they pin:

* #57 / #59 / #33 — the six Financials getters cache for `CacheTTL.financials` (30 min, not the
  24h `fundamental` that STACKED on the backend's own 24h tier), and only through
  `cacheFinancialsIfComplete`, which refuses a payload the server marked `degraded` or that is
  empty. An earnings entry is stale once its next earnings day (UTC) has arrived.
* #57 — an empty health check / signal of confidence maps to NO card, not "[0/0] Mix" or a
  fabricated "0.0% yield, share count unchanged".
* #91 — the early-return path (overview AND both fallbacks failed) settles every Phase-2 flag,
  the six Financials fetches settle the tab in their own group, and the tab shows a failure
  card with a retry instead of shimmering forever / blaming the company. The Analysis tab's
  valuation chart, which used the Financials flag as its spinner, still waits for its holders
  fallback now that the flag flips before holders lands.
* #93 (iOS half) — every Financials fetch failure is recorded through `AppError.from(_:)`, so
  the typed backend error reaches the tab.
* #30 / #60 — Cay AI's beat/miss line counts only quarters that HAD an estimate.
* #44 — the margins line names its fiscal year.
* #92 — the buyback verdict comes from the summary, outside the dividend block.

Round 2 (G9): the tab's retry is `retryFinancials()`, not the coalesced `loadTickerData()`
(R22 — more in `test_ios_state_round2.py`), each fetcher RETURNS its failure for the run to
publish, and the two rule tables (the stale-earnings rule, the fiscal-period label) are no
longer hand-copied Python ports that only tested themselves (R51): their constants are read
out of the Swift, and the guard clauses themselves are pinned.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_REPOSITORY = _IOS / "Core/Repositories/StockRepository.swift"
_VM = _IOS / "ViewModels/TickerDetailViewModel.swift"
_CONTENT = _IOS / "Views/Organisms/TickerFinancialsContent.swift"
_SCREEN = _IOS / "Views/Screens/TickerDetailView.swift"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    """Brace-balanced body of the first `header` in `src` (already comment-stripped)."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start + len(header) - 1 if header.endswith("{") else start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace: i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


def _src(path: Path) -> str:
    return _strip_comments(path.read_text())


def _repository_class() -> str:
    # The REAL repository — `MockStockRepository` declares the same getters.
    return _block(_src(_REPOSITORY), "final class StockRepository: StockRepositoryProtocol {")


_GETTERS = [
    ("getEarnings", "EarningsDTO"),
    ("getGrowth", "GrowthResponseDTO"),
    ("getProfitPower", "ProfitPowerResponseDTO"),
    ("getHealthCheck", "HealthCheckResponseDTO"),
    ("getSignalOfConfidence", "SignalOfConfidenceResponseDTO"),
    ("getRevenueBreakdown", "RevenueBreakdownDTO"),
]


def _getter(name: str, dto: str) -> str:
    return _block(_repository_class(), f"func {name}(ticker: String) async throws -> {dto} {{")


# ── #57 / #59 / #33: the repository cache ─────────────────────────────────────


def test_the_financials_ttl_is_thirty_minutes():
    ttl = _block(_repository_class(), "private enum CacheTTL {")
    m = re.search(r"static let financials\s*:\s*TimeInterval\s*=\s*(\d+)", ttl)
    assert m and int(m.group(1)) == 1800, "CacheTTL.financials must be 1800s"


@pytest.mark.parametrize("name,dto", _GETTERS)
def test_each_financials_getter_reads_the_short_ttl_and_caches_only_through_the_gate(name, dto):
    body = _getter(name, dto)
    assert "CacheTTL.financials" in body, f"{name} must read CacheTTL.financials"
    assert "CacheTTL.fundamental" not in body, (
        f"{name} is back on the 24h TTL — it stacks on the backend's 24h tier"
    )
    assert "setCache(" not in body, (
        f"{name} writes the cache directly — a degraded/empty payload would be pinned"
    )
    assert "cacheFinancialsIfComplete(" in body, f"{name} must cache through the gate"


def test_the_gate_refuses_a_degraded_or_empty_payload_before_writing():
    gate = _block(_repository_class(), "private func cacheFinancialsIfComplete<")
    guard = gate.find("guard response.isCacheable else")
    write = gate.find("setCache(")
    assert guard != -1 and write != -1 and guard < write, (
        "cacheFinancialsIfComplete must check isCacheable BEFORE setCache"
    )
    else_block = _block(gate[guard:], "guard response.isCacheable else {")
    assert "return" in else_block

    src = _src(_REPOSITORY)
    ext = _block(src, "extension FinancialsCacheable {")
    cacheable = _block(ext, "var isCacheable: Bool {")
    flat = re.sub(r"\s+", "", cacheable)
    assert "(degraded??[]).isEmpty" in flat and "!isEmptyPayload" in flat, cacheable


_EMPTY_RULES = {
    "EarningsDTO": ["epsQuarters.isEmpty", "revenueQuarters.isEmpty"],
    "GrowthResponseDTO": ["epsAnnual", "epsQuarterly", "revenueAnnual", "revenueQuarterly",
                          "netIncomeAnnual", "netIncomeQuarterly", "operatingProfitAnnual",
                          "operatingProfitQuarterly", "freeCashFlowAnnual",
                          "freeCashFlowQuarterly", "allSatisfy"],
    "ProfitPowerResponseDTO": ["annual.isEmpty", "quarterly.isEmpty"],
    # Nothing SCORED: empty, or only not-meaningful rows (R15) — `allSatisfy` covers both.
    "HealthCheckResponseDTO": ["metrics.allSatisfy", "HealthCheckMetric.notMeaningfulToken"],
    "RevenueBreakdownDTO": ["revenueSources.isEmpty", "reportedRevenue == nil"],
    "SignalOfConfidenceResponseDTO": ["dataPoints.isEmpty"],
}


@pytest.mark.parametrize("dto,tokens", sorted(_EMPTY_RULES.items()))
def test_each_dto_declares_what_empty_means(dto, tokens):
    src = _src(_REPOSITORY)
    m = re.search(rf"struct {dto}: Codable, FinancialsCacheable \{{", src)
    assert m, f"{dto} must conform to FinancialsCacheable"
    body = _block(src, m.group(0))
    empty = _block(body, "var isEmptyPayload: Bool {")
    for token in tokens:
        assert token in empty, f"{dto}.isEmptyPayload no longer checks {token!r}"


def test_an_earnings_entry_is_dropped_once_its_next_earnings_day_arrives():
    body = _getter("getEarnings", "EarningsDTO")
    hit = _block(body, "if let cached: EarningsDTO = getCached(")
    check = hit.find("nextEarningsDayHasArrived()")
    ret = hit.find("return cached")
    assert check != -1 and ret != -1 and check < ret, (
        "getEarnings must test nextEarningsDayHasArrived() before returning a cached entry"
    )
    assert "cache.removeValue(forKey: cacheKey)" in hit

    fn = _stale_rule_body()
    flat = re.sub(r"\s+", " ", fn)
    # Both halves of the guard clause (R51): without `raw.count >= 10` an empty or short date
    # compares "arrived" on every read (the cache is defeated); without `prefix(10)` a
    # timestamp-tailed date ("2026-09-30T20:00:00") sorts AFTER report day and is served stale.
    assert "guard let raw = nextEarningsDate?.date, raw.count >= 10 else { return false }" in flat
    assert "return String(raw.prefix(10)) <= Self.utcDayFormatter.string(from: now)" in flat
    dto = _block(_src(_REPOSITORY), "struct EarningsDTO: Codable, FinancialsCacheable {")
    fmt = _block(dto, "private static let utcDayFormatter: DateFormatter = {")
    assert 'TimeZone(identifier: "UTC")' in fmt and '"yyyy-MM-dd"' in fmt
    assert 'Locale(identifier: "en_US_POSIX")' in fmt


def _stale_rule_body() -> str:
    dto = _block(_src(_REPOSITORY), "struct EarningsDTO: Codable, FinancialsCacheable {")
    return _block(dto, "func nextEarningsDayHasArrived(now: Date = Date()) -> Bool {")


def _swift_stale_rule():
    """`EarningsDTO.nextEarningsDayHasArrived`, rebuilt from the numbers IN THE SWIFT.

    Not a hand-copied port (which would only test itself): the minimum length and the
    compared prefix are read out of the Swift source, so changing or deleting either there
    changes — or breaks — this rule, and the case table below catches it.
    """
    flat = re.sub(r"\s+", " ", _stale_rule_body())
    min_len = re.search(r"guard let raw = nextEarningsDate\?\.date, raw\.count >= (\d+) else", flat)
    prefix = re.search(r"return String\(raw\.prefix\((\d+)\)\) <= Self\.utcDayFormatter", flat)
    assert min_len and prefix, f"the Swift guard clause drifted: {flat}"
    n_min, n_prefix = int(min_len.group(1)), int(prefix.group(1))

    def rule(raw: str | None, today_utc: date) -> bool:
        if raw is None or len(raw) < n_min:
            return False
        return raw[:n_prefix] <= today_utc.isoformat()

    return rule


@pytest.mark.parametrize("raw,today,expected", [
    ("2026-09-30", date(2026, 9, 30), True),    # report day itself: stale
    ("2026-09-29", date(2026, 9, 30), True),    # already past
    ("2026-10-01", date(2026, 9, 30), False),   # still ahead
    ("2026-09-30", date(2026, 9, 29), False),   # tomorrow: a shorter prefix would say stale
    ("2026-12-31", date(2027, 1, 1), True),     # across a year boundary
    ("2026-09-30T20:00:00", date(2026, 9, 30), True),   # a timestamp tail is ignored
    (None, date(2026, 9, 30), False),           # no next date: age alone decides
    ("", date(2026, 9, 30), False),             # malformed: never "stale forever"
    ("2026-9-3", date(2026, 9, 30), False),     # unpadded: too short, not compared
    ("2026-09-3", date(2026, 9, 30), False),    # 9 chars: a looser length guard says stale
])
def test_the_stale_rule_read_from_the_swift(raw, today, expected):
    assert _swift_stale_rule()(raw, today) is expected


# ── wire fields reach the display models by assignment ────────────────────────


def test_the_new_wire_fields_are_mapped_onto_the_display_models():
    src = _src(_REPOSITORY)
    q = _block(src, "struct EarningsQuarterDTO: Codable {")
    assert "quarter.hasEstimate = hasEstimate ?? true" in _block(
        q, "func toDisplayModel() -> EarningsQuarterData {"
    )
    earnings = _block(_block(src, "struct EarningsDTO: Codable, FinancialsCacheable {"),
                      "func toDisplayModel() -> EarningsData {")
    # BOTH series through the one mapper, so neither can drop `hasEstimate`.
    assert "epsQuarters.map { $0.toDisplayModel() }" in earnings
    assert "revenueQuarters.map { $0.toDisplayModel() }" in earnings
    assert "EarningsQuarterData(" not in earnings

    growth = _block(_block(src, "struct GrowthResponseDTO: Codable, FinancialsCacheable {"),
                    "func toDisplayModel() -> GrowthSectionData {")
    assert "section.peerGroupLevels = peerGroupLevels ?? [:]" in growth

    soc = _block(_block(src, "struct SignalOfConfidenceResponseDTO: Codable, FinancialsCacheable {"),
                 "func toDisplayModel() -> SignalOfConfidenceSectionData {")
    assert "summaryModel.shareCountChangeKnown = summary.shareCountChangeKnown ?? true" in soc
    assert "info.avgYieldWindowLabel = dto.avgYieldWindow" in soc


# ── #57: empty health check / SoC are no card ─────────────────────────────────


def test_an_empty_health_check_or_signal_of_confidence_is_no_card():
    vm = _src(_VM)
    hc = _block(vm, "private func fetchHealthCheck(_ ticker: String, generation: Int) async -> FinancialsFailure? {")
    # The SCORED count (R15): a lone not-meaningful row is "[0/0] Mix" too.
    assert "self.healthCheckData = model.totalCount == 0 ? nil : model" in hc
    soc = _block(vm, "private func fetchSignalOfConfidence(_ ticker: String, generation: Int) async -> FinancialsFailure? {")
    # Still no card for an empty payload; since P19 (2026-10-01) also none when the
    # cash-flow FETCH failed (pinned in full by test_soc_p19_interior_cash_gap_ios.py).
    assert ("self.signalOfConfidenceData = (dto.dataPoints.isEmpty || dto.cashFlowLegFailed)"
            " ? nil : dto.toDisplayModel()") in soc


# ── #91: the Phase-2 flags always settle ──────────────────────────────────────

_SIX = ["fetchEarnings", "fetchGrowth", "fetchProfitPower", "fetchRevenueBreakdown",
        "fetchHealthCheck", "fetchSignalOfConfidence"]


def test_the_early_return_path_settles_every_phase_two_flag():
    vm = _src(_VM)
    load = _block(vm, "func loadTickerData() {")
    branch = _block(load, "if self.stockDetail == nil && self.stockQuote == nil {")
    settle = branch.find("self.settlePhaseTwoAfterFailedLoad(")
    ret = branch.find("return")
    assert settle != -1 and ret != -1 and settle < ret, (
        "the overview-failure branch must settle the Phase-2 flags BEFORE it returns"
    )

    helper = _block(vm, "private func settlePhaseTwoAfterFailedLoad(message: String?) {")
    for flag in ("isFinancialsLoaded", "isHoldersLoaded", "isTechnicalLoaded",
                 "isAnalystLoaded", "isSentimentLoaded"):
        assert re.search(rf"\b{flag} = true\b", helper), f"{flag} is never settled there"
    assert "holdersError = message" in helper
    assert "technicalIsRetryable = true" in helper


def test_the_financials_tab_settles_on_its_own_group():
    vm = _src(_VM)
    own = _block(vm, "private func fetchFinancialSections(_ ticker: String) async {")
    group = _block(own, "await withTaskGroup(of: FinancialsFailure?.self) { group -> [FinancialsFailure] in")
    called = re.findall(r"self\.(fetch\w+)\(", group)
    assert sorted(called) == sorted(_SIX), f"the Financials group holds {called}"
    after = own[own.find(group) + len(group):]
    assert "self.isFinancialsLoaded = true" in after, (
        "isFinancialsLoaded must flip when the SIX settle, after their group"
    )

    load = _block(vm, "func loadTickerData() {")
    phase_two = next(
        _block(load[m.start():], "await withTaskGroup(of: Void.self) { group in")
        for m in re.finditer(r"await withTaskGroup\(of: Void\.self\) \{ group in", load)
        if "fetchHolders(" in _block(load[m.start():], "await withTaskGroup(of: Void.self) { group in")
    )
    assert "self.fetchFinancialSections(ticker)" in phase_two
    for fetch in _SIX:
        assert f"self.{fetch}(" not in phase_two, (
            f"{fetch} is back in the outer group — the tab waits on holders/news again"
        )
    assert "isFinancialsLoaded = true" not in load, (
        "loadTickerData must not settle the tab itself — the six (or the early-return "
        "helper) do"
    )


@pytest.mark.parametrize("fetch,section", [
    ("fetchEarnings", "Earnings"), ("fetchGrowth", "Growth"),
    ("fetchProfitPower", "Profit Power"), ("fetchHealthCheck", "Health Check"),
    ("fetchSignalOfConfidence", "Signal of Confidence"),
    ("fetchRevenueBreakdown", "Revenue Breakdown"),
])
def test_every_financials_failure_is_recorded_for_the_tab(fetch, section):
    # Each fetcher RETURNS its failure; `fetchFinancialSections` publishes the run's set once
    # the six settle (round 2, R22 — so a retry can keep the notice up as "Retrying…").
    vm = _src(_VM)
    body = _block(vm, f"private func {fetch}(_ ticker: String, generation: Int) async -> FinancialsFailure? {{")
    catch = _block(body, "catch {")
    assert f'let failure = financialsFailure("{section}", ticker: ticker, error: error)' in catch
    assert "return failure" in catch


def test_a_recorded_failure_is_mapped_and_never_reported_for_a_cancelled_load():
    rec = _block(_src(_VM), "private func financialsFailure(")
    mapped = rec.find("AppError.from(error)")
    cancelled = rec.find("guard !Task.isCancelled else { return nil }")
    made = rec.find("return FinancialsFailure(section: section, message: appError.message)")
    assert -1 not in (mapped, cancelled, made) and mapped < cancelled < made
    own = _block(_src(_VM), "private func fetchFinancialSections(_ ticker: String) async {")
    assert "financialsError = ordered.lazy.compactMap(\\.message).first" in own
    assert "financialsFailedSections = ordered.map(\\.section)" in own


def test_the_tab_shows_a_retryable_failure_not_the_company_empty_state():
    content = _block(_src(_CONTENT), "struct TickerFinancialsContent: View {")
    body = _block(content, "var body: some View {")
    failure = body.find("else if isLoaded && !hasAnySection, let loadFailureMessage {")
    card = body.find("DetailLoadFailureCard(")
    empty = body.find("ChartUnavailableView(")
    assert -1 not in (failure, card, empty) and failure < card < empty, (
        "a FAILED load must be the retryable failure card, checked BEFORE the "
        "'isn't available for this company' state"
    )
    card_args = body[card: body.find("onRetry: onRetry", card) + len("onRetry: onRetry")]
    assert "message: loadFailureMessage" in card_args and "onRetry: onRetry" in card_args
    assert card_args.count("(") == 1, "the failure card's arguments drifted"
    notice = body.find("InlineRetryNotice(")
    assert notice != -1 and "failedSectionNames" in body[body.rfind("else if", 0, notice): notice]

    screen = _src(_SCREEN)
    call = screen[screen.find("TickerFinancialsContent("):]
    call = call[: call.find("onEarningsDetailTap")]
    assert "loadFailureMessage: viewModel.financialsError ?? viewModel.errorMessage" in call
    assert "failedSectionNames: viewModel.financialsFailedSections" in call
    # Round 2 (R22): the six fetches only. `loadTickerData()` JOINED a load still busy with
    # holders / technical / news, so every tap in that window did nothing.
    assert "onRetry: { Task { await viewModel.retryFinancials() } }" in call
    assert "viewModel.loadTickerData()" not in call


def test_the_valuation_spinner_still_waits_for_its_holders_fallback():
    # The Financials flag now flips when the SIX settle — before holders, whose 13F daily
    # prices are the valuation chart's fallback source. Keyed on the Financials flag alone,
    # the chart would say "no history" for a ticker whose earnings feed has no daily closes,
    # then pop in when holders landed.
    screen = re.sub(r"\s+", " ", _src(_SCREEN))
    assert (
        "isValuationPriceHistoryLoading: !viewModel.isFinancialsLoaded "
        "|| (viewModel.valuationPriceHistory.isEmpty && !viewModel.isHoldersLoaded)"
    ) in screen


# ── #30 / #60, #44, #92: what Cay AI is told ───────────────────────────────────


def test_beat_miss_counts_only_quarters_that_had_an_estimate():
    ctx = _block(_src(_VM), "private var financialsContext: String? {")
    earnings = _block(ctx, "if let ed = earningsData {")
    assert "let withEstimate = reported.filter { $0.result != .noEstimate }" in earnings
    for verdict in (".beat", ".missed", ".matched"):
        assert f"withEstimate.filter {{ $0.result == {verdict} }}" in earnings
        assert f"reported.filter {{ $0.result == {verdict} }}" not in earnings
    assert "\\(withEstimate.count)" in earnings and "\\(reported.count)" not in earnings


def test_the_margins_line_names_its_fiscal_year():
    ctx = _block(_src(_VM), "private var financialsContext: String? {")
    margins = _block(ctx, "if let pp = profitPowerData, let latest = pp.annualData.last {")
    assert "let periodLabel = Self.fiscalPeriodLabel(latest.period)" in margins
    assert 'Margins (\\(periodLabel)) — ' in margins
    assert 'Margins — ' not in margins
    assert "Avg Net Margin (\\(periodLabel))" in margins


def _period_label_body() -> str:
    return _block(_src(_VM), "static func fiscalPeriodLabel(_ period: String) -> String {")


def _swift_fiscal_period_label():
    """`TickerDetailViewModel.fiscalPeriodLabel`, rebuilt from the pieces IN THE SWIFT.

    The trim set, the empty fallback and the year prefix are read out of the Swift source,
    so the case table below fails when the Swift changes, not only when this copy does.
    """
    flat = re.sub(r"\s+", " ", _period_label_body())
    trim = re.search(r"let trimmed = period\.trimmingCharacters\(in: \.(\w+)\)", flat)
    fallback = re.search(r'guard !trimmed\.isEmpty else \{ return "([^"]*)" \}', flat)
    year = re.search(r'return trimmed\.allSatisfy\(\\\.isNumber\) \? "(\w*)\\\(trimmed\)" : trimmed', flat)
    assert trim and fallback and year, f"fiscalPeriodLabel drifted: {flat}"
    chars = {"whitespaces": " \t", "whitespacesAndNewlines": None}[trim.group(1)]

    def label(period: str) -> str:
        trimmed = period.strip(chars)
        if not trimmed:
            return fallback.group(1)
        return f"{year.group(1)}{trimmed}" if all(c.isnumeric() for c in trimmed) else trimmed

    return label


@pytest.mark.parametrize("period,label", [
    ("2023", "FY2023"), (" 2025 ", "FY2025"), ("Q1 '24", "Q1 '24"), ("TTM", "TTM"),
    ("", "latest fiscal year"), ("   ", "latest fiscal year"), ("FY2024", "FY2024"),
])
def test_the_period_label_read_from_the_swift(period, label):
    assert _swift_fiscal_period_label()(period) == label


def test_the_period_label_guard_clauses_are_in_the_swift():
    # The trim and the empty guard are what make " 2025 " a year and "" a sentence; without
    # them the label read "FY" (an empty string satisfies `allSatisfy`) or " 2025 ".
    flat = re.sub(r"\s+", " ", _period_label_body())
    assert "let trimmed = period.trimmingCharacters(in: .whitespaces)" in flat
    assert 'guard !trimmed.isEmpty else { return "latest fiscal year" }' in flat
    assert 'return trimmed.allSatisfy(\\.isNumber) ? "FY\\(trimmed)" : trimmed' in flat


def test_the_buyback_verdict_comes_from_the_summary_for_every_company():
    ctx = _block(_src(_VM), "var signalOfConfidenceContext: String? {")
    dividend = _block(ctx, "if let info = data.dividendInfo {")
    outside = ctx.replace(dividend, "")
    assert "data.summary.buybackStatus.rawValue" in outside, (
        "the buyback verdict must not depend on dividendInfo (nil for every non-payer)"
    )
    assert "buybackStatus" not in dividend
    # The card's window-true label, never a hard-coded "5Y" over two years of data.
    assert "info.averageYieldLabel" in dividend
    assert '"5Y' not in dividend
    assert "data.summary.shareCountDescription" in outside


# ── the scanners themselves ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    # The helpers find the REAL repository's getter, not the mock's.
    real = _getter("getEarnings", "EarningsDTO")
    assert "apiClient.request(" in real
    # A comment-only mention does not count.
    assert "setCache" not in _strip_comments("    // setCache(cacheKey, value: response)")
    # The block helper returns the balanced body.
    assert _block("a { b { c } d } e", "a {") == "{ b { c } d }"
