"""
Round-2 iOS state fixes for the TickerDetail Financials tab (G9, 2026-09-30).

There is no XCTest target, so these read the Swift. Every scan strips comments first (the
comment beside each fix names the very tokens looked for) and is brace-bound to the exact
declaration it means. Each guard was mutation-tested once by hand: break the Swift, watch
the test fail, restore.

* **R15** the Health Check badge counted the shown-but-unscored N/M ROE row (negative
  equity) in its denominator: the server sent 7 rows over ``total_count`` 6 and the card —
  and Cay AI's context — read "[3/7] Mix". The DTO now recounts over the SCORED rows, and the
  backend half is pinned here too: ``total_count`` / ``passed_count`` are exactly the rows
  whose ``highlighted_value`` is not the token iOS filters on, so the two halves agree.
* **R22** the Financials Try Again called ``loadTickerData()``, whose coalescer JOINS a load
  still busy with holders / technical / news — every tap did nothing. It now re-runs the six
  fetches itself, fenced on a generation token so a retry overtaken by pull-to-refresh
  writes nothing.
* **R50** a retry after the early-return path kept analyst / sentiment "loaded" with no
  data, so their skeletons never showed; the load now re-arms them when nothing is on screen.
* **R32 (mapping)** ``EarningsDTO.toDisplayModel`` carries the server's ``degraded`` reasons
  onto ``EarningsData``, and a degraded build with no quarter at all offers the tab's retry.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_REPOSITORY = _IOS / "Core/Repositories/StockRepository.swift"
_VM = _IOS / "ViewModels/TickerDetailViewModel.swift"
_CONTENT = _IOS / "Views/Organisms/TickerFinancialsContent.swift"
_SCREEN = _IOS / "Views/Screens/TickerDetailView.swift"
_HC_MODELS = _IOS / "Models/HealthCheckModels.swift"
_DETAIL_MODELS = _IOS / "Models/TickerDetailModels.swift"


# ── scanning helpers ─────────────────────────────────────────────────────────


def _code_only(src: str) -> str:
    """Drop `/* */` blocks and `//` comments, keeping string literals intact."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        blanked = re.sub(r'"(?:[^"\\]|\\.)*"',
                         lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', line)
        idx = blanked.find("//")
        out.append(line[:idx] if idx != -1 else line)
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    """Brace-balanced `{ … }` body of the first literal `header` in `src`."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted (vacuous otherwise)"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace: i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


def _read(path: Path) -> str:
    return _code_only(path.read_text(encoding="utf-8"))


def _flat(src: str) -> str:
    return re.sub(r"\s+", " ", src)


def _vm() -> str:
    return _block(_read(_VM), "class TickerDetailViewModel: ObservableObject {")


def test_the_helpers_are_not_vacuous():
    assert _code_only('    // retryFinancials()') .strip() == ""
    assert _code_only('let s = "a // b" // tail') == 'let s = "a // b" '
    assert _code_only("a /* retryFinancials() */ b") == "a  b"
    assert _block("x { y { z } w } v", "x {") == "{ y { z } w }"


# ══ R15: the Health Check badge counts SCORED rows only ═══════════════════════


def _hc_dto() -> str:
    return _block(_read(_REPOSITORY), "struct HealthCheckResponseDTO: Codable, FinancialsCacheable {")


def _hc_to_display() -> str:
    return _block(_hc_dto(), "func toDisplayModel() -> HealthCheckSectionData {")


def test_the_badge_recounts_over_the_scored_rows_not_every_shown_row():
    body = _hc_to_display()
    flat = _flat(body)
    assert "let scored = displayMetrics.filter { !$0.isNotMeaningful }" in flat, (
        "the recount must leave the not-meaningful row out — it is outside the server's count"
    )
    assert "let scoredPassed = scored.filter { $0.status == .positive }.count" in flat
    assert flat.count("return HealthCheckSectionData(") == 1, "a second return path skips the recount"
    ret = flat[flat.index("return HealthCheckSectionData("):]
    assert "passedCount: scoredPassed" in ret and "totalCount: scored.count" in ret, ret
    # The N/M row is still SHOWN: only the count leaves it out.
    assert "metrics: displayMetrics" in ret
    # The old denominator (every shown row) and the pass-through of the server count are gone.
    assert "totalCount: displayMetrics.count" not in flat
    assert "? passedCount :" not in flat


def test_ios_and_backend_name_the_not_meaningful_row_with_one_token():
    from app.services.health_check_service import NOT_MEANINGFUL

    metric = _block(_read(_HC_MODELS), "struct HealthCheckMetric: Identifiable {")
    m = re.search(r'static let notMeaningfulToken = "([^"]*)"', metric)
    assert m, "HealthCheckMetric.notMeaningfulToken is gone"
    assert m.group(1) == NOT_MEANINGFUL, (
        f"iOS filters rows on {m.group(1)!r} but the backend marks them {NOT_MEANINGFUL!r}"
    )
    assert "var isNotMeaningful: Bool { highlightedValue == Self.notMeaningfulToken }" in _flat(metric)


def test_a_payload_with_nothing_scored_is_empty_and_no_card():
    empty = _block(_hc_dto(), "var isEmptyPayload: Bool {")
    assert "metrics.allSatisfy { $0.highlightedValue == HealthCheckMetric.notMeaningfulToken }" in (
        _flat(empty)
    ), "a lone N/M row is 'total 0 / mix' — nothing scored — and must not be cached"
    fetch = _block(_vm(), "private func fetchHealthCheck(_ ticker: String, generation: Int) async -> FinancialsFailure? {")
    assert "self.healthCheckData = model.totalCount == 0 ? nil : model" in _flat(fetch), (
        "the card must key on the SCORED count, or a lone N/M row renders '[0/0] Mix'"
    )
    assert "model.metrics.isEmpty ? nil : model" not in _flat(fetch)


# ── R15, backend half: the server's counts are exactly the non-N/M rows ──────

_PROFILE = {"symbol": "TEST", "sector": "Technology", "industry": "Software - Infrastructure",
            "mktCap": 2.0e12}
_RATIOS = {"debtToEquityRatioTTM": 0.5, "priceToEarningsRatioTTM": 25.0,
           "currentRatioTTM": 1.5, "interestCoverageRatioTTM": 20.0, "quickRatioTTM": 1.2}
_BS = {"totalAssets": 500e9, "totalLiabilities": 250e9, "totalCurrentAssets": 150e9,
       "totalCurrentLiabilities": 100e9, "retainedEarnings": 100e9,
       "totalStockholdersEquity": 250e9}
_QUARTER = {"operatingIncome": 30e9, "interestExpense": 1e9, "revenue": 80e9,
            "netIncome": 25e9, "ebitda": 35e9}
_BENCH = {"debt_to_equity": 0.6, "pe_ratio": 28.0, "roe": 0.15, "current_ratio": 1.4,
          "interest_coverage": 15.0, "quick_ratio": 1.0}


def _income() -> List[Dict[str, Any]]:
    return [dict(_QUARTER, date=d)
            for d in ("2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30")]


class _FakeFMP:
    def __init__(self, answers: Dict[str, Any]) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            answer = self._answers.get(name, [])
            if isinstance(answer, BaseException):
                raise answer
            return copy.deepcopy(answer)

        return _call


async def _build(monkeypatch, *, ratios, roe, bs, income=None):
    from app.services import health_check_service as hc

    class _Lookup:
        def get_current_benchmark_values(self, industry, sector, metrics):
            return {m: _BENCH.get(m) for m in metrics}

    # Module-level `from … import get_sector_benchmark_lookup`: patch the CALLER's binding.
    monkeypatch.setattr(hc, "get_sector_benchmark_lookup", lambda: _Lookup())
    hc._cache.clear()
    hc._inflight.clear()
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = None
    svc.fmp = _FakeFMP({
        "get_company_profile": dict(_PROFILE),
        "get_ratios_ttm": [ratios],
        "get_key_metrics_ttm": [{"returnOnEquityTTM": roe}],
        "get_balance_sheet": [bs],
        "get_income_statement": _income() if income is None else income,
        "get_earning_calendar_full": [],
    })
    response, _next = await svc._build_health_check("TEST")
    return response


def _ios_badge(resp, token: str) -> str:
    """What the recount pinned above renders: positives over the rows NOT carrying `token`.

    Only meaningful together with the scan (`test_the_badge_recounts_over_the_scored_rows…`)
    and with `token` read from the Swift — on its own it would only check itself.
    """
    scored = [m for m in resp.metrics if m.highlighted_value != token]
    passed = sum(1 for m in scored if m.status == "positive")
    return f"[{passed}/{len(scored)}]"


@pytest.mark.asyncio
@pytest.mark.parametrize("label,de,roe,equity,expect_nm", [
    # Boeing-shaped: loss on negative equity, FMP ROE comes back POSITIVE (the finding).
    ("loss_on_negative_equity", -13.0, 3.03, -3.0e9, True),
    # McDonald's-shaped: profitable buybacks on negative equity, ROE comes back NEGATIVE.
    ("profit_on_negative_equity", -10.0, -2.16, -4.0e9, True),
    # Zero equity: undefined ratio, never a verdict.
    ("zero_equity", None, 0.8, 0.0, True),
    # Ordinary company: nothing is N/M, the badge is the server's own count.
    ("positive_equity", 0.5, 0.30, 250e9, False),
])
async def test_the_ios_badge_equals_the_servers_count(monkeypatch, label, de, roe, equity, expect_nm):
    metric = _block(_read(_HC_MODELS), "struct HealthCheckMetric: Identifiable {")
    token = re.search(r'static let notMeaningfulToken = "([^"]*)"', metric).group(1)

    ratios = dict(_RATIOS)
    if de is None:
        ratios.pop("debtToEquityRatioTTM")
    else:
        ratios["debtToEquityRatioTTM"] = de
    resp = await _build(monkeypatch, ratios=ratios, roe=roe,
                        bs=dict(_BS, totalStockholdersEquity=equity))

    nm_rows = [m for m in resp.metrics if m.highlighted_value == token]
    assert bool(nm_rows) is expect_nm, f"{label}: N/M rows {nm_rows}"
    if expect_nm:
        # The finding's shape: more rows than the server counted.
        assert len(resp.metrics) == resp.total_count + 1, label
    assert _ios_badge(resp, token) == f"[{resp.passed_count}/{resp.total_count}]", (
        f"{label}: iOS would render {_ios_badge(resp, token)} over a server "
        f"[{resp.passed_count}/{resp.total_count}]"
    )
    # Every N/M row is neutral, so leaving it out cannot move a passed count either way.
    assert all(m.status == "neutral" for m in nm_rows)


@pytest.mark.asyncio
async def test_a_lone_not_meaningful_row_is_total_zero_on_the_server(monkeypatch):
    # Negative equity with every other leg missing: the only row is the N/M ROE, the
    # server says total 0 / "mix" — which iOS must treat as nothing scored (no card).
    ratios: Dict[str, Optional[float]] = {}
    resp = await _build(monkeypatch, ratios=ratios, roe=3.03,
                        bs={"totalStockholdersEquity": -3.0e9}, income=[])
    assert resp.total_count == 0 and resp.passed_count == 0, resp
    assert resp.metrics and all(m.highlighted_value == "N/M" for m in resp.metrics), resp.metrics


# ══ R22: the Financials retry re-runs the six, fenced on a generation ═════════


def _retry() -> str:
    return _block(_vm(), "func retryFinancials() async {")


def test_the_tab_retry_calls_the_financials_retry_not_the_coalesced_load():
    screen = _read(_SCREEN)
    start = screen.find("TickerFinancialsContent(")
    assert start != -1
    call = _flat(screen[start: screen.find("onEarningsDetailTap", start)])
    assert "onRetry: { Task { await viewModel.retryFinancials() } }" in call, call
    assert "viewModel.loadTickerData()" not in call, (
        "loadTickerData() JOINS a load still busy with holders/technical/news — a no-op tap"
    )
    assert "isRetrying: viewModel.isLoading || viewModel.isRetryingFinancials" in call


def test_the_retry_fetches_the_six_itself():
    body = _retry()
    # Re-entrancy: a second tap while one is running does nothing.
    assert body.find("guard !isRetryingFinancials else") < body.find("fetchFinancialSections(")
    # The ONLY route into loadTickerData is the overview-failure branch, and it returns.
    overview = _block(body, "if errorMessage != nil {")
    assert "loadTickerData()" in overview and "return" in overview
    rest = body.replace(overview, "")
    assert "loadTickerData(" not in rest, "the retry routes through the coalescer again"
    # A fresh load already fetching the six is not raced.
    guard = rest.find("guard isFinancialsLoaded else")
    bump = rest.find("financialsGeneration &+= 1")
    flag = rest.find("isRetryingFinancials = true")
    fetch = rest.find("await fetchFinancialSections(tickerSymbol)")
    assert -1 not in (guard, bump, flag, fetch) and guard < bump < fetch and flag < fetch, rest
    assert "defer { isRetryingFinancials = false }" in _flat(rest)
    # The tab stays settled through a retry, so its notice / card can say "Retrying…".
    assert "isFinancialsLoaded = false" not in rest


def test_a_fresh_load_supersedes_any_financials_run_in_flight():
    load = _block(_vm(), "func loadTickerData() {")
    joined = load.find('print("⏳ TickerDetailVM: load already in flight — joining it")')
    bump = load.find("financialsGeneration &+= 1")
    task = load.find("loadTask = Task")
    assert -1 not in (joined, bump, task) and joined < bump < task, (
        "a fresh load (never a joined one) must bump the generation before it starts"
    )


def test_the_run_publishes_only_while_it_is_current():
    own = _block(_vm(), "private func fetchFinancialSections(_ ticker: String) async {")
    capture = own.find("let generation = financialsGeneration")
    group = own.find("await withTaskGroup(")
    assert -1 not in (capture, group) and capture < group
    calls = re.findall(r"self\.(fetch\w+)\(ticker, generation: generation\)", own)
    assert sorted(calls) == sorted(_SIX), calls
    gate = own.find("guard isCurrentFinancialsRun(generation")
    for write in ("financialsFailedSections =", "financialsError =", "self.isFinancialsLoaded = true"):
        at = own.find(write)
        assert at != -1 and gate != -1 and gate < at, f"{write} is not fenced on the run"


_SIX = ["fetchEarnings", "fetchGrowth", "fetchProfitPower", "fetchRevenueBreakdown",
        "fetchHealthCheck", "fetchSignalOfConfidence"]
_SECTION = {
    "fetchEarnings": ("Earnings", "earningsData"),
    "fetchGrowth": ("Growth", "growthData"),
    "fetchProfitPower": ("Profit Power", "profitPowerData"),
    "fetchRevenueBreakdown": ("Revenue Breakdown", "revenueBreakdownData"),
    "fetchHealthCheck": ("Health Check", "healthCheckData"),
    "fetchSignalOfConfidence": ("Signal of Confidence", "signalOfConfidenceData"),
}


@pytest.mark.parametrize("fetch", _SIX)
def test_each_fetcher_writes_only_for_the_current_run(fetch):
    section, prop = _SECTION[fetch]
    body = _block(
        _vm(), f"private func {fetch}(_ ticker: String, generation: Int) async -> FinancialsFailure? {{"
    )
    catch = _block(body, "catch {")
    do_arm = body[: body.find("catch {")]
    for arm, label in ((do_arm, "success"), (catch, "failure")):
        gate = arm.find("guard isCurrentFinancialsRun(generation")
        write = arm.find(f"self.{prop} =")
        assert gate != -1 and write != -1 and gate < write, (
            f"{fetch}: the {label} arm writes {prop} without checking its run is current"
        )
    assert f'let failure = financialsFailure("{section}", ticker: ticker, error: error)' in catch
    assert "return failure" in catch


def test_the_partial_failure_notice_says_retrying_and_cannot_be_tapped_twice():
    content = _block(_read(_CONTENT), "struct TickerFinancialsContent: View {")
    body = _block(content, "var body: some View {")
    notice_at = body.find("InlineRetryNotice(")
    assert notice_at != -1
    end = body.find("Spacer()", notice_at)
    assert end != -1
    notice = _flat(body[notice_at: end])
    assert 'retryTitle: isRetrying ? "Retrying\\u{2026}" : "Try Again"' in notice, notice
    assert ".disabled(isRetrying)" in notice
    card = _flat(body[body.find("DetailLoadFailureCard("):])
    assert "isRetrying: isRetrying" in card[: card.find(")")]


# ══ R50: analyst / sentiment re-arm on a load when nothing is on screen ═══════


def test_a_load_rearms_analyst_and_sentiment_only_when_they_show_nothing():
    load = _block(_vm(), "func loadTickerData() {")
    reset = load[: load.find("loadTask = Task")]
    flat = _flat(reset)
    assert "if analystRatingsData == nil { isAnalystLoaded = false }" in flat
    assert "if sentimentAnalysisData == nil { isSentimentLoaded = false }" in flat
    # Never unconditionally: a pull-to-refresh would flash skeletons over real data.
    stripped = flat.replace("if analystRatingsData == nil { isAnalystLoaded = false }", "")
    stripped = stripped.replace("if sentimentAnalysisData == nil { isSentimentLoaded = false }", "")
    assert "isAnalystLoaded = false" not in stripped and "isSentimentLoaded = false" not in stripped
    # …and AFTER the coalescer: a joined load must not un-settle the one it joins.
    joined = reset.find('print("⏳ TickerDetailVM: load already in flight — joining it")')
    assert joined != -1 and joined < reset.find("if analystRatingsData == nil")
    # The early-return path still settles both (no forever-shimmer offline).
    helper = _block(_vm(), "private func settlePhaseTwoAfterFailedLoad(message: String?) {")
    assert "isAnalystLoaded = true" in helper and "isSentimentLoaded = true" in helper


# ══ R32 (mapping): the degraded flag reaches the card, and a retry ════════════


def test_the_earnings_degraded_reasons_reach_the_display_model():
    dto = _block(_read(_REPOSITORY), "struct EarningsDTO: Codable, FinancialsCacheable {")
    body = _block(dto, "func toDisplayModel() -> EarningsData {")
    built = body.find("var data = EarningsData(")
    mapped = body.find("data.degraded = degraded ?? []")
    returned = body.find("return data")
    assert -1 not in (built, mapped, returned) and built < mapped < returned, body
    assert "return EarningsData(" not in body, "a second construction path skips the flag"


def test_the_display_model_declares_the_degraded_field_g8_owns():
    # The cross-agent contract (G8): `var degraded: [String] = []` on EarningsData, with a
    # default so every existing `EarningsData(...)` call (previews, sampleData) still compiles.
    model = _block(_read(_DETAIL_MODELS), "struct EarningsData {")
    assert re.search(r"\bvar degraded: \[String\] = \[\]", model), (
        "EarningsData.degraded is missing — the repository's mapping will not compile"
    )


def test_a_degraded_empty_earnings_build_offers_the_tab_retry():
    body = _block(
        _vm(), "private func fetchEarnings(_ ticker: String, generation: Int) async -> FinancialsFailure? {"
    )
    do_arm = body[: body.find("catch {")]
    kept = do_arm.find("self.earningsData = dto.toDisplayModel()")
    branch = do_arm.find("if dto.isEmptyPayload, let reasons = dto.degraded, !reasons.isEmpty {")
    assert -1 not in (kept, branch) and kept < branch, (
        "the degraded build stays on screen (its card says so; its closes feed the valuation "
        "chart) and THEN is named for a retry"
    )
    named = _block(do_arm[branch:], "if dto.isEmptyPayload, let reasons = dto.degraded, !reasons.isEmpty {")
    assert 'return FinancialsFailure(section: "Earnings", message: nil)' in named, (
        "a degraded-but-answered section must not become the tab's failure REASON"
    )
    # The card's reason comes only from a real failure's message.
    own = _block(_vm(), "private func fetchFinancialSections(_ ticker: String) async {")
    assert "financialsError = ordered.lazy.compactMap(\\.message).first" in own


@pytest.mark.parametrize("payload,expect_degraded", [
    ({"degraded": ["income", "estimates"]}, True),
    ({"degraded": []}, False),
    ({}, False),  # an older backend never sends the key
])
def test_the_backend_sends_degraded_as_a_list_ios_reads_with_a_default(payload, expect_degraded):
    from app.schemas.earnings import EarningsResponse

    resp = EarningsResponse.model_validate({
        "symbol": "AAPL", "eps_quarters": [], "revenue_quarters": [], "price_history": [],
        **payload,
    })
    dumped = resp.model_dump()
    assert isinstance(dumped["degraded"], list)
    assert bool(dumped["degraded"]) is expect_degraded
    # iOS decodes `degraded: [String]?` and maps `?? []` (pinned above): both shapes are safe.
    dto = _block(_read(_REPOSITORY), "struct EarningsDTO: Codable, FinancialsCacheable {")
    assert re.search(r"\blet degraded: \[String\]\?", dto)
