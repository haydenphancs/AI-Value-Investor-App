"""Source-scan guards for the Technical Analysis card on the crypto and stock screens.

Developer audit (2026-09-21): the crypto Analysis tab passed `isTechnicalLoaded: true` as a
LITERAL — no shimmer while the request was in flight, a blank tab forever on failure — and
never prefetched the detail (every first "Details" tap spun). The gauge printed "Hold" over
a step row that says "Neutral". Both screens dropped the card silently on failure while the
Index/Commodity screens had a message and a retry. And the Fear & Greed card was retitled
"Crypto Market".

Comments are stripped and every scan is brace-bound to the declaration it checks
(`.claude/rules/testing.md` §3); the explanatory comments beside each fix name the very
tokens these tests look for.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_CRYPTO_VIEW = _IOS / "Views/Screens/CryptoDetailView.swift"
_STOCK_VIEW = _IOS / "Views/Screens/TickerDetailView.swift"
_CRYPTO_VM = _IOS / "ViewModels/CryptoDetailViewModel.swift"
_STOCK_VM = _IOS / "ViewModels/TickerDetailViewModel.swift"
_CONTENT = _IOS / "Views/Organisms/TickerAnalysisContent.swift"
_MODELS = _IOS / "Models/TickerDetailModels.swift"
_METER = _IOS / "Views/Molecules/TechnicalMeter.swift"
_BADGE = _IOS / "Views/Atoms/TechnicalSignalBadge.swift"
_FEAR_GREED = _IOS / "Views/Organisms/CryptoFearGreedSection.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace:i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


def _call_block(src: str, header: str) -> str:
    """The parenthesised argument list of a call, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_paren = src.index("(", start)
    depth = 0
    for i in range(open_paren, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_paren:i + 1])
    pytest.fail(f"unbalanced parens after {header!r}")


# ── the crypto tab is honest about loading and failure ──────────────────────


def test_the_crypto_analysis_tab_passes_the_view_models_loaded_flag():
    call = _call_block(_read(_CRYPTO_VIEW), "TickerAnalysisContent(")
    assert "isTechnicalLoaded: viewModel.isTechnicalLoaded" in call, (
        "CryptoDetailView must pass the view model's flag")
    assert "isTechnicalLoaded: true" not in call, (
        "a literal `true` hides the shimmer and leaves the tab blank on failure")


@pytest.mark.parametrize("view", [_CRYPTO_VIEW, _STOCK_VIEW])
def test_both_screens_wire_the_failure_message_and_retry(view):
    call = _call_block(_read(view), "TickerAnalysisContent(")
    assert "technicalUnavailableMessage: viewModel.technicalUnavailableMessage" in call, view.name
    assert "technicalIsRetryable: viewModel.technicalIsRetryable" in call, view.name
    assert "viewModel.retryTechnicalAnalysis()" in call, view.name


@pytest.mark.parametrize("view", [_CRYPTO_VIEW, _STOCK_VIEW])
def test_both_analysis_tabs_prefetch_the_detail(view):
    body = _decl_block(_read(view), "private var tabContent: some View")
    analysis = body[body.index("case .analysis:") + len("case .analysis:"):]
    nxt = analysis.find("case .")
    analysis = analysis[:nxt] if nxt != -1 else analysis
    assert ".onAppear { viewModel.fetchTechnicalAnalysisDetail() }" in analysis, (
        f"{view.name}: the Analysis tab no longer prefetches the technical detail")


@pytest.mark.parametrize("vm", [_CRYPTO_VM, _STOCK_VM])
def test_both_view_models_expose_the_failure_state(vm):
    src = _strip_comments(_read(vm))
    assert "@Published var isTechnicalLoaded" in src, vm.name
    assert "@Published var technicalUnavailableMessage: String?" in src, vm.name
    assert "@Published var technicalIsRetryable: Bool" in src, vm.name
    retry = _decl_block(_read(vm), "func retryTechnicalAnalysis() async")
    assert "isTechnicalLoaded = false" in retry and "technicalUnavailableMessage = nil" in retry


def test_the_crypto_fetch_sets_loaded_on_both_outcomes():
    fn = _decl_block(_read(_CRYPTO_VM), "private func fetchCryptoTechnicalAnalysis() async")
    assert fn.count("self.isTechnicalLoaded = true") == 2, "success AND failure must mark loaded"
    assert "sampleData" not in fn, "a fabricated gauge is financial misinformation"
    assert "technicalIsRetryable = false" in fn and "technicalIsRetryable = true" in fn


def test_a_reload_resets_both_technical_flags():
    """`loadTickerData` reset neither: the card kept a stale "Try Again" through the whole
    reload, and a tap on it raced the fetch already in flight."""
    fn = _decl_block(_read(_STOCK_VM), "func loadTickerData(")
    i = fn.index("isTechnicalLoaded = false")
    assert "technicalUnavailableMessage = nil" in fn[i:i + 200], (
        "both flags, together — one without the other hides the card or strands a retry")


@pytest.mark.parametrize("vm", [_CRYPTO_VM, _STOCK_VM])
def test_permanent_failures_are_classified_through_app_error(vm):
    """A 409 `FMP_NOT_ENTITLED` (a FRED-backed asset has no OHLCV) arrives as
    `.businessError`, never `.notFound` — the bare `.notFound` test offered a Try Again
    that could never succeed. `CommodityDetailViewModel` had the fix; these two did not."""
    src = _strip_comments(_read(vm))
    assert "case .featureUnavailable, .notFound:" in src, vm.name
    assert "if case APIError.notFound = error" not in src, (
        f"{vm.name}: the bare notFound test is back — it misses FMP_NOT_ENTITLED")
    assert src.count("switch AppError.from(error)") >= 1, vm.name


def test_a_crypto_refresh_shows_the_shimmer_again():
    """`refresh()` clearing only the message left every branch false — no data, loaded,
    no message — so the Technical card VANISHED for the whole refresh instead of
    showing a placeholder."""
    fn = _decl_block(_read(_CRYPTO_VM), "func refresh() async")
    i = fn.index("technicalUnavailableMessage = nil")
    assert "isTechnicalLoaded = false" in fn[i:i + 200], (
        "refresh must reset the loaded flag with the message, not instead of it")


def test_the_shared_content_renders_the_failure_branch():
    body = _decl_block(_read(_CONTENT), "var body: some View")
    i = body.index("if let technicalData = technicalAnalysisData")
    section = body[i:i + 900]
    assert "else if !isTechnicalLoaded" in section
    assert "else if let message = technicalUnavailableMessage" in section, (
        "the card must say why it is missing instead of vanishing")
    assert "InlineRetryNotice(message: message, onRetry: retry)" in section
    assert "ChartUnavailableView(message: message)" in section


# ── labels ───────────────────────────────────────────────────────────────────


def test_hold_displays_as_neutral_and_the_meter_uses_it():
    enum = _decl_block(_read(_MODELS), "enum TechnicalSignal: String")
    name = _decl_block(enum, "var displayName: String")
    assert 'case .hold: return "Neutral"' in name
    assert 'case hold = "Hold"' in enum, "the wire value must stay 'Hold' for decoding"
    meter = _strip_comments(_read(_METER))
    assert "label: signal.displayName" in meter
    assert "label: signal.rawValue" not in meter, "the meter prints the wire value again"


def test_zero_indicators_reads_as_not_enough_history():
    struct_block = _decl_block(_read(_MODELS), "struct TechnicalIndicatorResult: Codable")
    fn = _decl_block(struct_block, "var formattedCount: String")
    assert 'guard totalIndicators > 0 else { return "Not enough history" }' in fn


def test_a_hold_does_not_claim_only_n_indicators_agree():
    """`matchingIndicators` is "agreeing with the verdict", and a Hold has no direction —
    the backend sends the NEUTRAL count. DOGE's weekly was 5 buy / 5 sell / 1 neutral (a
    dead heat) and the badge read "1 of 11 indicators", which looks like a broken number."""
    struct_block = _decl_block(_read(_MODELS), "struct TechnicalIndicatorResult: Codable")
    fn = _decl_block(struct_block, "var formattedCount: String")
    assert "guard signal == .hold else {" in fn, "a Hold must not use the agreement wording"
    assert "matchingIndicators * 2 >= totalIndicators" in fn, (
        "the majority test tells 'mostly neutral' apart from 'buys and sells cancel'")
    assert 'neutral"' in fn and 'Mixed · ' in fn


def test_the_signal_badge_is_a_labelled_button():
    body = _decl_block(_read(_BADGE), "var body: some View")
    assert ".accessibilityAddTraits(" in body and ".isButton" in body
    assert ".accessibilityLabel(" in body and "signal.displayName" in body


# ── price levels keep the decimals their magnitude needs ─────────────────────


def test_price_levels_are_formatted_magnitude_aware():
    """The backend already sends a sub-dollar asset's levels with 6 or 10 decimals
    (`_round_price`, 2026-08-21); iOS threw them away with `%.2f`, so DOGE's seven pivots
    rendered "0.12 / 0.11 / 0.11 / 0.10 / 0.09 / 0.08 / 0.08" — three pairs of identical
    levels — and SHIB's whole table read 0.00."""
    fmt = _read(_IOS / "Core/Utilities/PriceLevelFormat.swift")
    body = _decl_block(fmt, "var asPriceLevel: String")
    # The THRESHOLDS, not just the decimal counts: `magnitude >= 0` with 2 dp would keep
    # every branch present and still print DOGE's pivots as "0.11".
    assert "magnitude >= 1 {" in body and "decimals = 2" in body
    assert "magnitude >= 0.0001 {" in body and "decimals = 6" in body
    assert "decimals = 10" in body
    assert 'guard isFinite else { return "—" }' in body, "a missing level is not 0.00"
    # Trailing zeros are trimmed to a 2-dp floor: a bounded reading sitting at zero
    # (Williams %R at the top of its range) printed "0.0000000000" without this.
    assert 'text.hasSuffix("0")' in body and "> 3" in body, (
        "the trailing-zero trim is gone — sub-dollar rows regain false precision")
    # IEEE negative zero: Williams %R is -0.0 at the period high and printed "-0.00".
    assert "let value = self == 0 ? 0 : self" in body
    assert 'String(format: "%.\(decimals)f", value)' in body, "the normalised value is unused"

    models = _strip_comments(_read(_MODELS))
    for decl in ("struct PivotPointLevel", "struct FibonacciLevel",
                 "extension MovingAverageIndicator", "extension OscillatorIndicator"):
        pass
    # The four level/indicator formatters and the current price all route through it.
    assert models.count("value.asPriceLevel") >= 4, (
        "a level formatter is back on %.2f")
    assert "currentPrice.asPriceLevelCurrency" in models
    row = _strip_comments(_read(_IOS / "Views/Molecules/TechnicalIndicatorRow.swift"))
    assert "Text(value.asPriceLevel)" in row, "the support/resistance row is back on %.2f"
    assert 'String(format: "%.2f", value)' not in row


# ── the Fear & Greed title ────────────────────────────────────────────────────


def test_the_fear_greed_card_is_titled_crypto_market():
    src = _strip_comments(_read(_FEAR_GREED))
    assert 'Text("Crypto Market")' in src
    assert "Crypto Market Sentiment" not in src


# ── anti-vacuity ─────────────────────────────────────────────────────────────


def test_the_scans_are_not_vacuous():
    raw = _read(_CRYPTO_VIEW)
    # The fix's own comment names the literal; the stripped call must not.
    assert "isTechnicalLoaded: true" in raw or "literal `true`" in raw
    assert "isTechnicalLoaded: true" not in _call_block(raw, "TickerAnalysisContent(")
    # The call block is bounded: it ends before the `.onAppear` modifier.
    call = _call_block(raw, "TickerAnalysisContent(")
    assert ".onAppear" not in call
    meter_raw = _read(_METER)
    assert len(_strip_comments(meter_raw)) > 300
