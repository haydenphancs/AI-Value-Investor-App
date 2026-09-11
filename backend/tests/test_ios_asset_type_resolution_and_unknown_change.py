"""Four iOS regressions caught by the W3 review of the 2026-09-11 fix pass.

1. `AssetDetailRouter` switched on `MarketTickerType.resolve(...).rawValue`, and the enum
   has no `fund` case — so the search route's `type: "fund"` (closed-end / index funds,
   still emitted by `stocks.py::_get_asset_type`) fell to the symbol heuristic, resolved
   `.stock`, and opened the company-profile screen on a fund. `resolve` now folds "fund"
   into `.etf`, and the router switches on the enum (exhaustive — no string arms).
2. `resolve` trusted any known rawValue before the symbol, so a legacy `"Stock"` for
   `BTCUSD` still resolved `.stock` — the exact case its doc comment said it handled.
   Mirrors the backend's `resolve_asset_class`: "stock" is never trusted over the symbol.
3. `IndexDetailData.isPositive` became `changeKnown && …`, but `TickerPriceHeader` has
   only two states, so an UNKNOWN change rendered as a red ▼ beside "—" — the fix that
   stopped fabricating "+0.00%" fabricated a decline instead. The header now takes
   `changeKnown` (neutral, no arrow, no flash), and the chart's colour / baseline no
   longer read the placeholder change.
4. `addTickerFromSearch` sent the bare `BTC` to `PUT /portfolios/{id}/tickers`, whose
   raw-first resolution picks the same-ticker SECURITY when the user also holds it — and
   the coin they had just starred was dropped from the portfolio. The portfolio now
   carries the stored (pair) spelling for a coin.

Source-scan guards, brace-bound and comment-stripped (no XCTest target). Each was
mutation-tested by hand: reverting the named line turns the matching test red.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(path: Path, header: str) -> str:
    src = path.read_text()
    start = src.find(header)
    assert start != -1, f"{header!r} not found in {path.name} — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip(src[open_brace:i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


# ── 1 + 2: MarketTickerType.resolve ──────────────────────────────────────────

def _resolve_body() -> str:
    return _block(_IOS / "Models/HomeModels.swift", "static func resolve(_ raw: String?, symbol: String)")


def test_resolve_folds_fund_into_etf():
    body = _resolve_body()
    assert re.search(r'if normalized == "fund" \{ return \.etf \}', body), (
        "a search result typed \"fund\" no longer routes to the ETF screen"
    )


def test_resolve_never_trusts_a_stock_label_over_the_symbol():
    body = _resolve_body()
    assert re.search(r'if normalized != "stock", let known = MarketTickerType\(rawValue: normalized\)', body), (
        "resolve trusts the DB column default \"Stock\" before looking at the symbol"
    )
    # and the symbol heuristic still runs after the trusted-class check
    i = body.index("let known = MarketTickerType(rawValue")
    tail = body[i:]
    for needle in ('hasPrefix("^")', "MarketHoursUtil.commoditySymbols.contains(sym)", 'hasSuffix("USD")'):
        assert needle in tail, f"the symbol fallback lost {needle}"


def test_the_router_switches_on_the_enum_and_sends_etf_to_the_etf_screen():
    body = _block(_IOS / "Views/Molecules/AssetDetailRouter.swift", "struct AssetDetailRouter")
    assert "switch MarketTickerType.resolve(selection.type, symbol: selection.symbol) {" in body
    assert ".rawValue" not in body, "switching on rawValue is how the \"fund\" arm went dead"
    assert re.search(r"case \.etf:\s*\n\s*ETFDetailView\(etfSymbol: selection\.symbol\)", body)
    assert re.search(r"case \.crypto:\s*\n\s*CryptoDetailView\(", body)
    assert re.search(r"case \.index:\s*\n\s*IndexDetailView\(", body)
    assert re.search(r"case \.commodity:\s*\n\s*CommodityDetailView\(", body)
    assert re.search(r"case \.stock:\s*\n(?:.*\n){0,6}?\s*TickerDetailView\(", body)
    assert "default:" not in body, "an exhaustive switch keeps a new case a compile error"


def test_the_backend_still_emits_fund_so_the_fold_is_load_bearing():
    """Anti-vacuity: if the search route stops saying \"fund\", the fold is dead code."""
    src = (Path(__file__).resolve().parents[1] / "app/api/v1/endpoints/stocks.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 'return "fund"' in code


# ── 3: an unknown change is neutral on the index header and chart ────────────

def test_the_price_header_gates_colour_arrow_and_flash_on_change_known():
    body = _block(_IOS / "Views/Molecules/TickerPriceHeader.swift", "struct TickerPriceHeader")
    assert re.search(r"var changeKnown:\s*Bool\s*=\s*true", body), "changeKnown must default true for the ETF/stock callers"
    color = re.search(r"private var changeColor: Color \{(.*?)\n    \}", body, re.S)
    assert color and "guard changeKnown else { return AppColors.textSecondary }" in color.group(1)
    assert re.search(r"if changeKnown \{\s*\n\s*Image\(systemName: arrowIcon\)", body), "the arrow is drawn for an unknown change"
    flash = re.search(r"\.onChange\(of: price\) \{ _, _ in(.*?)\n        \}", body, re.S)
    assert flash and "guard changeKnown else { return }" in flash.group(1), "the price flash asserts a direction for an unknown change"


def test_the_index_screen_passes_the_flag_and_a_chart_that_ignores_the_placeholder():
    body = _block(_IOS / "Views/Screens/IndexDetailView.swift", "struct IndexDetailView")
    header = re.search(r"TickerPriceHeader\((.*?)\n\s*\)", body, re.S)
    assert header and "changeKnown: indexData.changeKnown" in header.group(1)
    chart = re.search(r"TickerChartView\((.*?)\n\s*\)", body, re.S)
    assert chart and "isPositive: indexData.chartIsPositive" in chart.group(1)
    assert "previousClose: indexData.chartPreviousClose" in chart.group(1)


def test_the_header_protocol_derives_chart_colour_from_the_series_when_unknown():
    src = _strip((_IOS / "Models/IndexDetailResponseModels.swift").read_text())
    proto = re.search(r"protocol IndexHeaderRenderable \{(.*?)\n\}", src, re.S)
    assert proto and "var changeKnown: Bool { get }" in proto.group(1)
    ext = _block(_IOS / "Models/IndexDetailResponseModels.swift", "extension IndexHeaderRenderable")
    assert "if changeKnown { return isPositive }" in ext
    assert "return last >= first" in ext
    assert "changeKnown ? previousClose : nil" in ext


@pytest.mark.parametrize("header", ["struct IndexDetailData", "struct IndexCoreData"])
def test_both_index_models_keep_is_positive_gated(header):
    body = _block(_IOS / ("Models/IndexDetailModels.swift" if "Detail" in header else "Models/IndexDetailResponseModels.swift"), header)
    assert re.search(r"var isPositive: Bool \{\s*changeKnown && priceChange >= 0\s*\}", body)


# ── 4: the portfolio carries the coin's STORED spelling ──────────────────────

def test_add_from_search_uses_the_pair_form_for_a_coin():
    body = _block(_IOS / "ViewModels/TrackingViewModel.swift", "func addTickerFromSearch(_ result: StockSearchResult)")
    assert re.search(
        r'let symbol = \(result\.type \?\? ""\)\.lowercased\(\) == "crypto"\s*\n\s*\? CryptoSymbol\.pair\(result\.ticker\)\s*\n\s*: result\.ticker\.uppercased\(\)',
        body,
    ), "a starred coin reaches PUT /portfolios/{id}/tickers as the bare symbol"
    assert "portfolioStore.addTicker(symbol, to: portfolioId)" in body
