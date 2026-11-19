"""iOS says "Delisted" for a listing the backend CONFIRMED is delisted (owner, 2026-11-13).

The decisions this pins, iOS half (plan `if-a-ticker-is-cached-blanket.md`):
  D1  A grey "Delisted" pill replaces the price and change on the Tracking row and the Home
      watchlist tile. The detail screen keeps the last close, labelled as the last close.
  D2  The backend decides; iOS reads four wire keys — `listing_status` ("delisted" | null),
      `delisted_date`, `delisted_exchange`, `last_trade_date` — and fails OPEN: a missing,
      null or unknown status is "not delisted", today's behaviour.
  D3  Cay AI is told: the detail screen's AI context says "delisted … no live quote".
  D4  An exchange move FMP no longer prices is labelled too ("Delisted from NYSE · Oct 5,
      2026"); the copy never claims the company stopped trading and never gives a reason.

ROKU (delisted from NASDAQ on 2026-11-09, last traded 2026-11-06) is the live example: before
this, the detail screen showed $160.52 under a clock-based "Market Open" badge, polled every
15 s, and Cay AI called the price "(live)".

Two halves:

* EXECUTED. `DelistedListing.swift` is Foundation-only, so it is piped into `xcrun swift -` and
  run under TZ=Pacific/Kiritimati (UTC+14) and TZ=Pacific/Pago_Pago (UTC-11): a date-only
  "2026-11-09" must read "Nov 9, 2026" at both ends of the clock. Every test that runs Swift
  has "executed" in its name (subagents run `-k "not executed"`), and skips when xcrun is
  unavailable.
* SOURCE SCAN (testing.md §3; there is no XCTest target). Comments are stripped first — the
  fix's own comments name every token asserted here — and every check is brace-bound to the
  ONE declaration it is about. Each guard is a `_check_*` function over raw source text, and
  `test_each_guard_fails_on_its_mutation` breaks each one IN MEMORY (the Swift files on disk
  are never edited) and asserts it then fails, so no guard here has only ever been seen
  passing.

Category 1 (pure) — no network, no Supabase.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"

_FILES: Dict[str, Path] = {
    "helper": _IOS / "Core/Utilities/DelistedListing.swift",
    "overview_models": _IOS / "Models/StockOverviewResponseModels.swift",
    "tracking_models": _IOS / "Models/TrackingModels.swift",
    "home_models": _IOS / "Models/HomeDashboardModels.swift",
    "home_repo": _IOS / "Core/Repositories/HomeRepository.swift",
    "stock_repo": _IOS / "Core/Repositories/StockRepository.swift",
    "detail_models": _IOS / "Models/TickerDetailModels.swift",
    "header": _IOS / "Views/Molecules/TickerPriceHeader.swift",
    "detail_view": _IOS / "Views/Screens/TickerDetailView.swift",
    "detail_vm": _IOS / "ViewModels/TickerDetailViewModel.swift",
    "row": _IOS / "Views/Molecules/AssetRow.swift",
    "tile": _IOS / "Views/Molecules/MarketPulseCard.swift",
    "tracking_vm": _IOS / "ViewModels/TrackingViewModel.swift",
}

_WIRE_KEYS: Dict[str, str] = {
    "listingStatus": "listing_status",
    "delistedDate": "delisted_date",
    "delistedExchange": "delisted_exchange",
    "lastTradeDate": "last_trade_date",
}

# (file key, the declaration the four keys must live in)
_DTOS: List[Tuple[str, str]] = [
    ("overview_models", r"struct StockOverviewResponseDTO\s*:\s*Decodable"),
    ("overview_models", r"struct StockOverviewCoreResponseDTO\s*:\s*Decodable"),
    ("tracking_models", r"struct TrackedAssetDTO\s*:\s*Codable"),
    ("home_models", r"struct MarketPulseItemDTO\s*:\s*Decodable"),
    ("stock_repo", r"struct StockDetail\s*:\s*Codable"),
    ("stock_repo", r"struct StockQuote\s*:\s*Codable"),
]

_PILL = "TintedTagBadge(text: DelistedListing.pillText, color: AppColors.textSecondary)"


# ── extraction ───────────────────────────────────────────────────────────────


def _strip(src: str) -> str:
    """Drop `/* */` blocks and `//` comments. A `//` inside a string literal is kept (the scan
    walks each line and ignores `//` while inside quotes; `\\"` is skipped)."""
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    out = []
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
        out.append(line[:cut])
    return "\n".join(out)


def _span(code: str, at: int, opener: str = "{", closer: str = "}") -> Tuple[int, int]:
    """(open, close) of the bracket block whose opener is the first one at or after `at`."""
    start = code.find(opener, at)
    if start == -1:
        raise AssertionError(f"no {opener!r} after offset {at} — this scan has drifted")
    depth = 0
    for i in range(start, len(code)):
        if code[i] == opener:
            depth += 1
        elif code[i] == closer:
            depth -= 1
            if depth == 0:
                return start, i
    raise AssertionError(f"unbalanced {opener}{closer} after offset {at}")


def _braced(code: str, decl: str) -> str:
    """The brace-matched body of the FIRST match of the regex `decl` — or an AssertionError
    (never `pytest.fail`, so a mutation that deletes a declaration fails the guard)."""
    m = re.search(decl, code)
    if not m:
        raise AssertionError(f"{decl!r} not found — this scan has drifted")
    open_, close = _span(code, m.start())
    return code[open_:close + 1]


def _at(text: str, token: str, start: int = 0) -> int:
    """`text.index(token)` that fails the GUARD (AssertionError), never with a ValueError."""
    i = text.find(token, start)
    if i == -1:
        raise AssertionError(f"{token!r} not found — the guarded code is gone")
    return i


def _else_after(code: str, block_close: int) -> str:
    """The `else { … }` block that follows a block closing at `block_close`, or ''."""
    rest = code[block_close + 1:]
    m = re.match(r"\s*else\s*\{", rest)
    if not m:
        return ""
    base = block_close + 1
    open_, close = _span(code, base + m.end() - 1)
    return code[open_:close + 1]


def _if_block(code: str, opener: str) -> Tuple[str, str]:
    """(the `if …` block that starts with the literal `opener`, its `else` block or '')."""
    at = code.find(opener)
    if at == -1:
        raise AssertionError(f"{opener!r} not found — this scan has drifted")
    open_, close = _span(code, at + len(opener) - 1)
    return code[open_:close + 1], _else_after(code, close)


def _call_args(code: str, opener: str) -> List[str]:
    """Every `opener(` … `)` call in `code`, paren-balanced."""
    calls = []
    at = code.find(opener)
    while at != -1:
        open_, close = _span(code, at + len(opener) - 1, "(", ")")
        calls.append(code[open_:close + 1])
        at = code.find(opener, at + 1)
    return calls


def _flat(text: str) -> str:
    return " ".join(text.split())


def _sources() -> Dict[str, str]:
    out = {}
    for key, path in _FILES.items():
        if not path.exists():
            pytest.fail(f"expected file is missing: {path}")
        out[key] = path.read_text(encoding="utf-8")
    return out


def _code(src: Dict[str, str], key: str) -> str:
    return _strip(src[key])


def _drop_previews(code: str) -> str:
    """Blank every `#Preview … { … }` block (sample data may use what shipped code must not)."""
    out = code
    while True:
        m = re.search(r"#Preview\b", out)
        if not m:
            return out
        open_, close = _span(out, m.end())
        out = out[:m.start()] + out[close + 1:]


# ── A. the helper ────────────────────────────────────────────────────────────


def _check_helper_is_foundation_only_and_nonisolated(src: Dict[str, str]) -> None:
    code = _code(src, "helper")
    assert "import SwiftUI" not in code and "import UIKit" not in code, (
        "DelistedListing imports a UI framework — it can no longer run under `xcrun swift -`, "
        "and every executed case below silently stops being tested")
    assert "import Foundation" in code
    assert re.search(r"\bnonisolated\s+struct\s+DelistedListing\s*:\s*Hashable\s*,\s*Sendable\b", code), (
        "DelistedListing must be a nonisolated Hashable, Sendable struct: the DTO mappings that "
        "build it run off the main actor, and MarketPulseItem is Hashable")
    assert 'static let pillText = "Delisted"' in code


def _check_helper_parser_is_pinned(src: Dict[str, str]) -> None:
    code = _code(src, "helper")
    zone = re.search(r"private static let marketZone: TimeZone = ([^\n]+)", code)
    assert zone and 'TimeZone(identifier: "America/New_York")' in zone.group(1), (
        "the market zone is no longer New York")
    parser = _braced(code, r"private static let parser: DateFormatter = ")
    for token in ('formatter.locale = Locale(identifier: "en_US_POSIX")',
                  "formatter.calendar = Calendar(identifier: .gregorian)",
                  "formatter.timeZone = marketZone",
                  'formatter.dateFormat = "yyyy-MM-dd"',
                  "formatter.isLenient = false"):
        assert token in parser, f"the day parser lost `{token}`"


def _check_helper_display_shares_the_parse_zone(src: Dict[str, str]) -> None:
    """A date-only value is parsed and printed in ONE zone, or it shifts across midnight."""
    code = _code(src, "helper")
    display = _braced(code, r"private static func makeDisplayFormatter\(locale: Locale\?\) -> DateFormatter")
    assert "formatter.timeZone = marketZone" in display, "display and parse zones differ — a day can shift"
    assert "formatter.calendar = Calendar(identifier: .gregorian)" in display
    assert "setLocalizedDateFormatFromTemplate(" in display and "dateFormat =" not in display
    label = _braced(code, r"static func dayLabel\(_ ymd: String, locale: Locale\? = nil\) -> String\?")
    assert "parser.date(from: day)" in label and "display.string(from: date)" in label
    assert "makeDisplayFormatter(locale: locale)" in label


def _check_helper_reads_no_device_zone(src: Dict[str, str]) -> None:
    code = _code(src, "helper")
    for ambient in ("TimeZone.current", "Calendar.current", "autoupdatingCurrent", "Locale.current",
                    "NSTimeZone.local", "TimeZone.autoupdatingCurrent"):
        assert ambient not in code, f"DelistedListing reads the device's {ambient}"
    assert not re.search(r'"[^"\n]*\b(EST|EDT)\b[^"\n]*"', code), (
        "a fixed EST/EDT label is false half the year — and a calendar day has no zone label")


def _check_the_wire_rule_is_one_function(src: Dict[str, str]) -> None:
    code = _code(src, "helper")
    rule = _braced(code, r"static func from\(status: String\?, delistedDate: String\?, exchange: String\?,\s*lastTradeDate: String\?\) -> DelistedListing\?")
    assert "status.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()" in rule
    assert re.search(r'guard normalized == "delisted" else \{ return nil \}', rule), (
        "only the exact (trimmed, case-insensitive) word turns the label on — anything else fails open")
    # The pill word and the wire word live in the helper alone: every other file goes through
    # `DelistedListing.from` / `DelistedListing.pillText`, so the rule cannot drift per surface.
    offenders = []
    for path in sorted(_IOS.rglob("*.swift")):
        if path == _FILES["helper"]:
            continue
        rel = str(path.relative_to(_IOS))
        key = next((k for k, p in _FILES.items() if p == path), None)
        text = src[key] if key else path.read_text(encoding="utf-8")
        # Previews are dropped: `#Preview("Delisted")` is a preview's NAME, not shipped copy.
        if re.search(r'(?i)"delisted"', _drop_previews(_strip(text))):
            offenders.append(rel)
    assert not offenders, f"a second home for the delisted wire/pill word: {offenders}"


# ── B. the wire ──────────────────────────────────────────────────────────────


def _check_every_dto_declares_the_four_keys(src: Dict[str, str]) -> None:
    for key, decl in _DTOS:
        block = _braced(_code(src, key), decl)
        for swift_name, wire in _WIRE_KEYS.items():
            assert re.search(rf"\b(?:let|var)\s+{swift_name}:\s*String\?(?:\s*=\s*nil)?\s*\n", block), (
                f"{decl}: `{swift_name}` is missing or non-Optional — a non-Optional key throws "
                "keyNotFound against an older backend or a saved snapshot and blanks the screen")
            assert f'case {swift_name} = "{wire}"' in block, f"{decl}: CodingKey for {wire} is missing"
        # A `let` with an inline default is never decoded by synthesized Decodable.
        assert not re.search(r"\blet\s+(?:listingStatus|delistedDate|delistedExchange|lastTradeDate):\s*String\?\s*=", block), (
            f"{decl}: a `let` with a default is skipped by synthesized Decodable — use `var`")


def _check_tracked_asset_decodes_the_keys_leniently(src: Dict[str, str]) -> None:
    dto = _braced(_code(src, "tracking_models"), r"struct TrackedAssetDTO\s*:\s*Codable")
    init = _braced(dto, r"init\(from decoder: Decoder\) throws")
    for swift_name in _WIRE_KEYS:
        assert f"{swift_name} = lenient(String.self, .{swift_name})" in init, (
            f"`{swift_name}` is not decoded leniently — one malformed row could drop the whole watchlist")


def _check_every_mapping_uses_the_one_rule(src: Dict[str, str]) -> None:
    sites = [
        ("overview_models", r"func toCoreData\(\) -> TickerCoreData", "delisting: DelistedListing.from("),
        ("overview_models", r"func toDisplayModel\(\) -> TickerDetailData", "delisting: DelistedListing.from("),
        ("tracking_models", r"func toTrackedAsset\(\) -> TrackedAsset", "delisting: DelistedListing.from("),
        ("home_repo", r"private static func mapPulse\(_ dto: MarketPulseItemDTO\) -> MarketPulseItem",
         "delisting: DelistedListing.from(status: dto.listingStatus"),
        ("stock_repo", r"struct StockDetail\s*:\s*Codable", "DelistedListing.from(status: listingStatus"),
        ("stock_repo", r"struct StockQuote\s*:\s*Codable", "DelistedListing.from(status: listingStatus"),
    ]
    for key, decl, token in sites:
        block = _braced(_code(src, key), decl)
        assert block.count("DelistedListing.from(") == 1, f"{decl}: expected ONE call of the wire rule"
        assert token in block, f"{decl}: the model never receives the delisting"
    # Each display model carries it as a trailing, defaulted property (old call sites compile).
    for key, decl, prop in (
        ("overview_models", r"struct TickerCoreData \{", "var delisting: DelistedListing? = nil"),
        ("detail_models", r"struct TickerDetailData: Identifiable \{", "var delisting: DelistedListing? = nil"),
        ("tracking_models", r"struct TrackedAsset: Identifiable \{", "var delisting: DelistedListing? = nil"),
        ("home_models", r"struct MarketPulseItem: Identifiable, Hashable \{", "let delisting: DelistedListing?"),
    ):
        assert prop in _braced(_code(src, key), decl), f"{decl}: no `{prop}`"


def _check_the_watchlist_tile_keeps_the_label(src: Dict[str, str]) -> None:
    """The trap: `mapWatchlistTile` rebuilds the item by hand, and the init defaults the
    delisting to nil — the Your Watchlist strip (the only Home strip that can hold a delisted
    symbol) would silently show the stale last close as a live price."""
    block = _braced(_code(src, "home_repo"), r"private static func mapWatchlistTile\(_ dto: MarketPulseItemDTO\) -> MarketPulseItem")
    assert "delisting: tile.delisting" in block, "mapWatchlistTile drops the delisting"
    init = _braced(_code(src, "home_models"), r"struct MarketPulseItem: Identifiable, Hashable \{")
    assert "delisting: DelistedListing? = nil) {" in _flat(init) and "self.delisting = delisting" in init


# ── C. the Tracking row ──────────────────────────────────────────────────────


def _row_buttons(src: Dict[str, str]) -> Tuple[str, str]:
    body = _braced(_code(src, "row"), r"var body: some View")
    first = _at(body, "Button {")
    second = _at(body, "Button {", first + 1)
    return body[:second], body[second:]


def _check_row_hides_the_price_and_overlays_the_pill(src: Dict[str, str]) -> None:
    """The price column stays LAID OUT (opacity 0) so the card keeps its height —
    `AssetsListSection` sizes the whole List from one hidden row."""
    _, toggle = _row_buttons(src)
    hide = toggle.find(".opacity(asset.isDelisted ? 0 : 1)")
    assert hide != -1, "the price and change are no longer hidden on a delisted row"
    column = _at(toggle, ".holdingsPriceColumn(")
    assert _at(toggle, "PriceChangeLabel(") < hide < column, (
        "the hide must wrap the price VStack, before the column frame")
    overlay_at = toggle.find(".overlay(alignment: .trailing)")
    assert hide < overlay_at < column, "the pill overlay moved"
    gate, _ = _if_block(toggle[overlay_at:column], "if asset.isDelisted {")
    assert _PILL in gate, "the overlay no longer draws the textSecondary Delisted pill"
    assert ".fixedSize()" in gate, "a large text size would truncate the pill"
    # Nothing is REMOVED from the layout for a delisted row: no conditional around the price.
    assert "if !asset.isDelisted" not in toggle, "a conditional price changes the row's height"


def _check_row_pill_opens_the_details(src: Dict[str, str]) -> None:
    _, toggle = _row_buttons(src)
    action = toggle[: _at(toggle, "} label:")]
    assert "if asset.isDelisted { onTap?() } else { onToggleChangeDisplay?() }" in action, (
        "a delisted row has no change to toggle — its price column must open the details")


def _check_row_sparkline_keeps_its_slot(src: Dict[str, str]) -> None:
    navigation, _ = _row_buttons(src)
    frame = navigation.find(".frame(width: Self.sparklineWidth, height: 32)")
    assert frame != -1
    tail = navigation[frame: frame + 200]
    assert ".opacity(asset.isDelisted ? 0 : 1)" in tail, "a delisted row still draws a session line"
    assert "if asset.isDelisted" not in navigation, "removing the sparkline moves the chart column"


def _check_row_voiceover_reads_the_headline(src: Dict[str, str]) -> None:
    _, toggle = _row_buttons(src)
    assert "asset.isDelisted ? delistedAccessibilityLabel :" in _flat(toggle), (
        "VoiceOver would read the hidden last close as a live price")
    label = _braced(_code(src, "row"), r"private var delistedAccessibilityLabel: String")
    assert "asset.delisting?.spokenHeadline" in label
    hint = _braced(_code(src, "row"), r"private var priceAccessibilityHint: String")
    assert '"Opens details"' in hint and "asset.isDelisted" in hint


def _check_delisted_rows_sort_last(src: Dict[str, str]) -> None:
    flat = _flat(_braced(_code(src, "tracking_vm"), r"var filteredAssets: \[TrackedAsset\]"))
    for token in ("price: asset.priceKnown && !asset.isDelisted ? asset.price : nil",
                  "changePercent: asset.changeKnown && !asset.isDelisted ? asset.changePercent : nil",
                  "marketCap: asset.isDelisted ? nil : asset.marketCap"):
        assert token in flat, f"a delisted row's stale value still sorts: missing `{token}`"


# ── D. the Home tile ─────────────────────────────────────────────────────────


def _check_tile_pill_replaces_the_price(src: Dict[str, str]) -> None:
    code = _code(src, "tile")
    body = _braced(code, r"var body: some View")
    gate, other = _if_block(body, "if item.isDelisted {")
    assert "delistedPill" in gate and "item.priceText" not in gate, "the pill no longer replaces the price"
    assert "Text(item.priceText)" in other, "the price moved out of the not-delisted branch"
    pill = _braced(code, r"private var delistedPill: some View")
    assert _PILL in pill
    assert "spokenHeadline" in pill, "VoiceOver reads only the bare word"


def _check_tile_hides_the_spark_and_the_change(src: Dict[str, str]) -> None:
    body = _braced(_code(src, "tile"), r"var body: some View")
    assert "if !item.spark.isEmpty, !item.isDelisted {" in body, "a delisted tile still draws a session line"
    change, _ = _if_block(body, "if !item.isDelisted {")
    assert "Text(item.changeText)" in change, "a delisted tile still shows a day change"
    assert body.count("Text(item.changeText)") == 1


# ── E. the detail header and screen ──────────────────────────────────────────


def _check_header_badge_replaces_market_status(src: Dict[str, str]) -> None:
    code = _drop_previews(_code(src, "header"))
    struct = _braced(code, r"struct TickerPriceHeader: View")
    assert struct.count("MarketStatusBadge(") == 1, (
        "the market badge is drawn outside the delisted branch — a clock-based 'Market Open' "
        "beside a delisted listing")
    body = _braced(struct, r"var body: some View")
    assert "statusBadge" in body and "MarketStatusBadge(" not in body
    badge = _braced(struct, r"private var statusBadge: some View")
    gate, other = _if_block(badge, "if let delisting {")
    assert _PILL in gate and "delisting.detailLine" in gate and "MarketStatusBadge(" not in gate
    assert "accessibilityLabel(delisting.spokenHeadline)" in gate
    assert "MarketStatusBadge(status: marketStatus)" in other
    assert "var delisting: DelistedListing? = nil" in struct, "other callers (ETF, index) must not change"


def _check_header_says_last_close_and_never_flashes(src: Dict[str, str]) -> None:
    code = _drop_previews(_code(src, "header"))
    struct = _braced(code, r"struct TickerPriceHeader: View")
    body = _braced(struct, r"var body: some View")
    gate, other = _if_block(body, "if let delisting {")
    assert "lastCloseLabel(delisting)" in gate and "priceChange" not in gate
    assert "Image(systemName: arrowIcon)" in other, "the day change left the not-delisted branch"
    label = _braced(struct, r"private func lastCloseLabel\(_ delisting: DelistedListing\) -> some View")
    assert "delisting.lastCloseLine" in label and "AppColors.textSecondary" in label
    flash = body[_at(body, ".onChange(of: price)"):]
    flash = flash[: _span(flash, 0)[1] + 1]
    assert "guard delisting == nil else { return }" in flash, "a last close flashes green/red"


def _check_detail_view_passes_the_delisting_to_both_headers(src: Dict[str, str]) -> None:
    code = _code(src, "detail_view")
    headers = _call_args(code, "TickerPriceHeader(")
    assert len(headers) == 2, f"expected the full and the core header, found {len(headers)}"
    for model in ("tickerData", "core"):
        mine = [h for h in headers if f"changeKnown: {model}.changeKnown" in h]
        assert len(mine) == 1 and f"delisting: {model}.delisting" in mine[0], (
            f"the {model} header is not told about the delisting")


def _check_detail_view_explains_the_frozen_price(src: Dict[str, str]) -> None:
    code = _code(src, "detail_view")
    body = _braced(code, r"var body: some View")
    gate = body.find("if viewModel.tickerData != nil || viewModel.coreData != nil")
    notice_at = body.find("delistedNotice")
    skeleton = body.find("DetailHeaderChartSkeleton(")
    assert -1 < skeleton < notice_at < gate, (
        "the notice must sit between the header chain and the chart, outside both header branches")
    notice = _braced(code, r"private var delistedNotice: some View")
    gate_block, _ = _if_block(notice, "if let delisting = viewModel.delisting {")
    flat = _flat(gate_block)
    assert "InlineRetryNotice(" in flat and "delisting.notice(symbol: displaySymbol)" in flat
    assert 'systemImage: "info.circle"' in flat and "iconColor: AppColors.textMuted" in flat
    assert "onRetry" not in flat, "information, not a failure: the notice offers no retry"


def _check_the_pinned_price_says_delisted(src: Dict[str, str]) -> None:
    code = _code(src, "detail_view")
    assert "tickerPrice: isTabBarPinned ? pinnedPriceLabel : nil" in code
    label = _braced(code, r"private var pinnedPriceLabel: String\?")
    assert "if data.delisting != nil { return DelistedListing.pillText }" in label


def _check_the_share_text_says_delisted(src: Dict[str, str]) -> None:
    code = _code(src, "detail_view")
    share = _braced(code, r"private var shareItems: \[Any\]")
    assert share.count("ShareContent.items(") == 2, "exactly the loaded payload and the fallback"
    assert "let priceLine: String = Self.sharePriceLine(for: tickerData)" in share
    assert "\\(priceLine)" in share, "the share body no longer uses the delisting-aware price line"
    line = _braced(code, r"private static func sharePriceLine\(for data: TickerDetailData\) -> String")
    gate, _ = _if_block(line, "if let delisting = data.delisting {")
    assert "delisting.shareLine(lastClose: data.formattedPrice)" in gate


# ── F. polling ───────────────────────────────────────────────────────────────


def _check_live_updates_stop_for_a_delisting(src: Dict[str, str]) -> None:
    fn = _braced(_code(src, "detail_vm"), r"func startLivePriceUpdates\(\)")
    first = fn[1:].lstrip()
    assert first.startswith("guard delisting == nil else {"), (
        "the delisting guard is not the FIRST statement — a timer is armed before it")
    guard, _ = _if_block(fn, "guard delisting == nil else {")
    assert "stopLivePriceUpdates()" in guard and "return" in guard
    assert _at(fn, "guard delisting == nil else {") < _at(fn, "startChartRefreshTimer()") < _at(fn, "quotePollTask = Task")


def _check_the_chart_timer_stops_for_a_delisting(src: Dict[str, str]) -> None:
    fn = _braced(_code(src, "detail_vm"), r"private func startChartRefreshTimer\(\)")
    assert _at(fn, "guard delisting == nil else {") < _at(fn, "chartRefreshTask = Task")
    loop = fn[_at(fn, "chartRefreshTask = Task"):]
    assert _at(loop, "guard let self = self else { break }") < _at(loop, "guard self.delisting == nil else { break }"), (
        "the range sink can arm the timer before the overview lands; the loop must re-check")


def _check_the_poll_guards_both_sides_of_the_await(src: Dict[str, str]) -> None:
    vm = _code(src, "detail_vm")
    fn = _braced(vm, r"private func pollQuotePrice\(\) async")
    # The pre-fetch guard also STOPS the loop: a bare `return` left it waking every 15 s or
    # 60 s as a no-op for the rest of the visit when a label arrived by a path that does not
    # stop it.
    pre_guard, _ = _if_block(fn, "guard delisting == nil else {")
    assert _flat(pre_guard) == "{ stopQuotePoll() return }", (
        f"the poll's pre-fetch delisting guard must stop the loop and return: {_flat(pre_guard)!r}")
    order = [fn.find("guard delisting == nil else {"),
             fn.find("getStockQuote("),
             fn.find("if let delisted = quote.delisting {"),
             fn.find("guard let shown = self.tickerData, shown.delisting == nil, let price = quote.price else { return }"),
             fn.find("MarketHoursUtil.liveSessionStatus()")]
    assert all(i >= 0 for i in order) and order == sorted(order), (
        f"the poll's delisting guards are missing or out of order: {order} — after the await "
        "the 'Market Open' heal must not run for a delisted listing")
    apply = _braced(vm, r"private func applyDelistingFromPoll\(_ delisted: DelistedListing\)")
    assert "data.delisting = delisted" in apply and "core.delisting = delisted" in apply
    assert "stopLivePriceUpdates()" in apply


def _check_the_entitlement_refresh_settles_the_poll(src: Dict[str, str]) -> None:
    """The plan-change overview refresh applies an overview but is not `loadTickerData()`, so
    nothing re-ran the poll gate after it: a first label left the quote loop waking for the
    rest of the visit, and an overview that ERASED a poll's label (the server caches a whole
    overview for 120 s; /quote decides on every request) never re-armed the poll to restore it."""
    fn = _braced(_code(src, "detail_vm"), r"func refreshOverviewAfterEntitlementChange\(\) async")
    apply_at = _at(fn, "await applyOverview(")
    captured = _at(fn, "let hadDelisting = delisting != nil")
    assert _at(fn, "getStockOverview(") < captured < apply_at, (
        "`hadDelisting` must be read after the fetch and right before the apply: a poll's quote "
        "can put the label on screen while the request is in flight")
    assert "await" not in fn[captured:apply_at], (
        "no suspension between reading `hadDelisting` and the apply")
    tail = fn[apply_at:]
    opener = "if delisting != nil {"
    stop_open, stop_close = _span(tail, _at(tail, opener) + len(opener) - 1)
    stop_block = tail[stop_open:stop_close + 1]
    assert _flat(stop_block) == "{ stopLivePriceUpdates() }", f"a new label must stop the poll: {_flat(stop_block)!r}"
    rest = tail[stop_close + 1:]
    m = re.match(r"\s*else\s+if\s+hadDelisting\s*\{", rest)
    assert m, "the poll may re-arm ONLY when this apply erased a label (`else if hadDelisting`)"
    open_, close = _span(rest, m.end() - 1)
    assert "startLivePriceUpdates()" in rest[open_:close + 1]
    assert _else_after(rest, close) == "", "no unconditional `else` arm after the re-arm"
    # Exactly one arm in the whole function, and it is the guarded one above: an
    # unconditional start would re-arm a poll that onDisappear / willResignActive stopped.
    assert fn.count("startLivePriceUpdates()") == 1, "the poll is armed outside the `hadDelisting` branch"


def _check_the_view_model_reads_the_model_the_header_shows(src: Dict[str, str]) -> None:
    vm = _code(src, "detail_vm")
    prop = _braced(vm, r"var delisting: DelistedListing\? \{")
    flat = _flat(prop)
    assert flat == "{ if let data = tickerData { return data.delisting } return coreData?.delisting }", (
        f"the screen's delisting must be the full model's, else the core's — never an OR: {flat!r}")
    builder = _braced(vm, r"private func buildTickerDetailData\(\) -> TickerDetailData")
    assert "delisting: stockDetail?.delisting ?? stockQuote?.delisting ?? coreData?.delisting" in builder, (
        "the overview-failed fallback no longer reads the listing routes")


# ── G. Cay AI context and previews ───────────────────────────────────────────


def _check_the_ai_context_never_calls_a_last_close_live(src: Dict[str, str]) -> None:
    ctx = _braced(_code(src, "detail_vm"), r"private var baseStockContext: String\?")
    gate, other = _if_block(ctx, "if let delisting = td.delisting {")
    assert "lines.append(contentsOf: delisting.aiContextLines(lastClose: td.formattedPrice))" in gate
    assert '"Price:' not in gate and '"Price:' in other
    assert ctx.count('"Price:') == 1, "a live-looking Price line is sent for a delisted listing"
    helper = _code(src, "helper")
    lines = _braced(helper, r"func aiContextLines\(lastClose: String, locale: Locale\? = nil\) -> \[String\]")
    assert "there is no live quote" in lines and "Last close: " in lines


def _check_previews_show_the_delisted_state(src: Dict[str, str]) -> None:
    for key in ("row", "tile", "header"):
        code = _code(src, key)
        m = re.search(r'#Preview\("Delisted"\)', code)
        assert m, f"{_FILES[key].name}: no #Preview(\"Delisted\")"
        open_, close = _span(code, m.end())
        assert "delisting: .sample" in code[open_:close], f"{_FILES[key].name}: the preview shows no delisting"


def _check_pill_ink_is_text_secondary(src: Dict[str, str]) -> None:
    """`textSecondary` on the pill's own opaque surface (~6.6 light / 7.4 dark). Never
    `AppColors.neutral`: it is AMBER, a caution colour for a fact."""
    for key in ("row", "tile", "header"):
        code = _drop_previews(_code(src, key))
        assert _PILL in code, f"{_FILES[key].name}: the Delisted pill is gone or re-inked"
        for m in re.finditer(r"TintedTagBadge\(text: DelistedListing\.pillText, color: ([^)]+)\)", code):
            assert m.group(1) == "AppColors.textSecondary", f"{_FILES[key].name}: pill ink {m.group(1)}"
        assert "AppColors.neutral" not in code, f"{_FILES[key].name}: amber on a delisted label"


GUARDS: Dict[str, Callable[[Dict[str, str]], None]] = {
    "helper_foundation_only": _check_helper_is_foundation_only_and_nonisolated,
    "helper_parser_pinned": _check_helper_parser_is_pinned,
    "helper_same_zone": _check_helper_display_shares_the_parse_zone,
    "helper_no_device_zone": _check_helper_reads_no_device_zone,
    "wire_rule": _check_the_wire_rule_is_one_function,
    "dto_keys": _check_every_dto_declares_the_four_keys,
    "tracked_asset_lenient": _check_tracked_asset_decodes_the_keys_leniently,
    "mappings": _check_every_mapping_uses_the_one_rule,
    "watchlist_tile": _check_the_watchlist_tile_keeps_the_label,
    "row_pill": _check_row_hides_the_price_and_overlays_the_pill,
    "row_tap": _check_row_pill_opens_the_details,
    "row_sparkline": _check_row_sparkline_keeps_its_slot,
    "row_voiceover": _check_row_voiceover_reads_the_headline,
    "sort": _check_delisted_rows_sort_last,
    "tile_pill": _check_tile_pill_replaces_the_price,
    "tile_spark_change": _check_tile_hides_the_spark_and_the_change,
    "header_badge": _check_header_badge_replaces_market_status,
    "header_last_close": _check_header_says_last_close_and_never_flashes,
    "detail_headers": _check_detail_view_passes_the_delisting_to_both_headers,
    "detail_notice": _check_detail_view_explains_the_frozen_price,
    "pinned_price": _check_the_pinned_price_says_delisted,
    "share": _check_the_share_text_says_delisted,
    "live_updates": _check_live_updates_stop_for_a_delisting,
    "chart_timer": _check_the_chart_timer_stops_for_a_delisting,
    "poll": _check_the_poll_guards_both_sides_of_the_await,
    "entitlement_poll": _check_the_entitlement_refresh_settles_the_poll,
    "vm_source": _check_the_view_model_reads_the_model_the_header_shows,
    "ai_context": _check_the_ai_context_never_calls_a_last_close_live,
    "previews": _check_previews_show_the_delisted_state,
    "pill_ink": _check_pill_ink_is_text_secondary,
}


@pytest.mark.parametrize("name", sorted(GUARDS))
def test_the_guard_holds_on_the_real_source(name):
    GUARDS[name](_sources())


# ── each guard can fail ──────────────────────────────────────────────────────

# (guard, file key, exact text, replacement) — applied to an IN-MEMORY copy only.
MUTATIONS: List[Tuple[str, str, str, str]] = [
    ("helper_foundation_only", "helper", "import Foundation\n", "import Foundation\nimport SwiftUI\n"),
    ("helper_foundation_only", "helper", "nonisolated struct DelistedListing: Hashable, Sendable {",
     "struct DelistedListing: Hashable, Sendable {"),
    ("helper_parser_pinned", "helper", "        formatter.isLenient = false\n", ""),
    ("helper_parser_pinned", "helper", 'formatter.locale = Locale(identifier: "en_US_POSIX")',
     'formatter.locale = Locale(identifier: "en_GB")'),
    ("helper_same_zone", "helper",
     "        formatter.timeZone = marketZone\n        formatter.setLocalizedDateFormatFromTemplate",
     "        formatter.setLocalizedDateFormatFromTemplate"),
    ("helper_no_device_zone", "helper", '?? .gmt', '?? TimeZone.current'),
    ("helper_no_device_zone", "helper", 'var close: String = "Last close: \\(lastClose)"',
     'var close: String = "Last close (EST): \\(lastClose)"'),
    ("wire_rule", "helper", 'guard normalized == "delisted" else { return nil }',
     'guard normalized.hasPrefix("delist") else { return nil }'),
    ("wire_rule", "detail_view", "if data.delisting != nil { return DelistedListing.pillText }",
     'if data.delisting != nil { return "Delisted" }'),
    ("dto_keys", "tracking_models", 'case delistedExchange = "delisted_exchange"',
     'case delistedExchange = "delisted_exchang"'),
    ("dto_keys", "home_models", "    let lastTradeDate: String?\n", "    let lastTradeDate: String\n"),
    ("dto_keys", "stock_repo",
     "    var listingStatus: String? = nil\n    var delistedDate: String? = nil\n    var delistedExchange: String? = nil\n    var lastTradeDate: String? = nil\n\n    // Backend (FMP quote)",
     "    let listingStatus: String? = nil\n    var delistedDate: String? = nil\n    var delistedExchange: String? = nil\n    var lastTradeDate: String? = nil\n\n    // Backend (FMP quote)"),
    ("tracked_asset_lenient", "tracking_models", "listingStatus = lenient(String.self, .listingStatus)",
     "listingStatus = try c.decodeIfPresent(String.self, forKey: .listingStatus)"),
    ("mappings", "home_repo",
     "delisting: DelistedListing.from(status: dto.listingStatus, delistedDate: dto.delistedDate,\n"
     "                                            exchange: dto.delistedExchange, lastTradeDate: dto.lastTradeDate)",
     "delisting: nil"),
    ("mappings", "detail_models", "    var delisting: DelistedListing? = nil\n", ""),
    ("watchlist_tile", "home_repo", "            delisting: tile.delisting\n", ""),
    ("row_pill", "row",
     "                .opacity(asset.isDelisted ? 0 : 1)\n                .accessibilityHidden(asset.isDelisted)\n                .overlay",
     "                .opacity(1)\n                .accessibilityHidden(asset.isDelisted)\n                .overlay"),
    ("row_pill", "row",
     "                        TintedTagBadge(text: DelistedListing.pillText, color: AppColors.textSecondary)\n"
     "                            .fixedSize()\n",
     "                        EmptyView()\n"),
    ("row_tap", "row", "if asset.isDelisted { onTap?() } else { onToggleChangeDisplay?() }",
     "onToggleChangeDisplay?()"),
    ("row_sparkline", "row",
     "                    .opacity(asset.isDelisted ? 0 : 1)\n                    .accessibilityHidden(asset.isDelisted)\n",
     ""),
    ("row_voiceover", "row", "asset.delisting?.spokenHeadline ?? DelistedListing.pillText", "asset.formattedPrice"),
    ("sort", "tracking_vm", "marketCap: asset.isDelisted ? nil : asset.marketCap", "marketCap: asset.marketCap"),
    ("sort", "tracking_vm", "price: asset.priceKnown && !asset.isDelisted ? asset.price : nil",
     "price: asset.priceKnown ? asset.price : nil"),
    ("tile_pill", "tile", "if item.isDelisted {\n                    delistedPill",
     "if item.isDelisted {\n                    Text(item.priceText)"),
    ("tile_spark_change", "tile", "if !item.spark.isEmpty, !item.isDelisted {", "if !item.spark.isEmpty {"),
    ("header_badge", "header", "                statusBadge\n",
     "                MarketStatusBadge(status: marketStatus)\n                statusBadge\n"),
    ("header_badge", "header",
     "            MarketStatusBadge(status: marketStatus)\n        }\n    }\n",
     "            EmptyView()\n        }\n    }\n"),
    ("header_last_close", "header", "            guard delisting == nil else { return }\n", ""),
    ("detail_headers", "detail_view", "delisting: core.delisting", "delisting: nil"),
    ("detail_notice", "detail_view", "                    delistedNotice\n", ""),
    ("detail_notice", "detail_view", "iconColor: AppColors.textMuted\n            )\n            .padding(.horizontal, AppSpacing.lg)",
     "iconColor: AppColors.textMuted,\n                onRetry: {}\n            )\n            .padding(.horizontal, AppSpacing.lg)"),
    ("pinned_price", "detail_view", "tickerPrice: isTabBarPinned ? pinnedPriceLabel : nil",
     "tickerPrice: isTabBarPinned ? viewModel.tickerData?.formattedPrice : nil"),
    ("share", "detail_view", "        \\(priceLine)\n", "        \\(tickerData.formattedPrice)\n"),
    ("live_updates", "detail_vm",
     "        guard delisting == nil else {\n            stopLivePriceUpdates()\n",
     "        if false {\n            stopLivePriceUpdates()\n"),
    ("chart_timer", "detail_vm", "                guard self.delisting == nil else { break }\n", ""),
    ("poll", "detail_vm",
     "guard let shown = self.tickerData, shown.delisting == nil, let price = quote.price else { return }",
     "guard let shown = self.tickerData, let price = quote.price else { return }"),
    ("poll", "detail_vm",
     "        guard delisting == nil else {\n            stopQuotePoll()\n            return\n        }\n        do {\n",
     "        do {\n"),
    ("poll", "detail_vm",
     "        guard delisting == nil else {\n            stopQuotePoll()\n            return\n        }\n        do {\n",
     "        guard delisting == nil else {\n            return\n        }\n        do {\n"),
    ("entitlement_poll", "detail_vm",
     "            if delisting != nil {\n                stopLivePriceUpdates()\n            } else if hadDelisting {",
     "            if delisting != nil {\n            } else if hadDelisting {"),
    ("entitlement_poll", "detail_vm", "            } else if hadDelisting {\n", "            } else {\n"),
    ("entitlement_poll", "detail_vm",
     "            let hadDelisting = delisting != nil\n            await applyOverview(response, ticker: ticker, requested: requested, keepBarsOnScreen: true)\n",
     "            await applyOverview(response, ticker: ticker, requested: requested, keepBarsOnScreen: true)\n            let hadDelisting = delisting != nil\n"),
    ("entitlement_poll", "detail_vm",
     "            log.info(\"overview refreshed after an entitlement change for \\(ticker, privacy: .public)\")\n",
     "            log.info(\"overview refreshed after an entitlement change for \\(ticker, privacy: .public)\")\n            startLivePriceUpdates()\n"),
    ("vm_source", "detail_vm",
     "        if let data = tickerData { return data.delisting }\n        return coreData?.delisting\n",
     "        tickerData?.delisting ?? coreData?.delisting\n"),
    ("vm_source", "detail_vm",
     "delisting: stockDetail?.delisting ?? stockQuote?.delisting ?? coreData?.delisting",
     "delisting: coreData?.delisting"),
    ("ai_context", "detail_vm", "        if let delisting = td.delisting {\n            lines.append(contentsOf:",
     "        lines.append(\"Price: \\(td.formattedPrice)\")\n        if let delisting = td.delisting {\n            lines.append(contentsOf:"),
    ("previews", "tile", '#Preview("Delisted")', '#Preview("Delisted tile")'),
    ("pill_ink", "header", "                TintedTagBadge(text: DelistedListing.pillText, color: AppColors.textSecondary)\n",
     "                TintedTagBadge(text: DelistedListing.pillText, color: AppColors.neutral)\n"),
]


@pytest.mark.parametrize("guard, key, old, new", MUTATIONS,
                         ids=[f"{m[0]}-{i}" for i, m in enumerate(MUTATIONS)])
def test_each_guard_fails_on_its_mutation(guard, key, old, new):
    src = _sources()
    assert src[key].count(old) == 1, f"mutation anchor drifted ({src[key].count(old)} matches): {old!r}"
    src[key] = src[key].replace(old, new)
    with pytest.raises(AssertionError):
        GUARDS[guard](src)


def test_every_guard_has_a_mutation():
    assert {m[0] for m in MUTATIONS} == set(GUARDS)


def test_the_stripper_keeps_literals_and_drops_comments():
    sample = 'let u = "https://x.test/a" // TimeZone.current\n/* Calendar.current */ let v = "\\"//\\""\n'
    stripped = _strip(sample)
    assert '"https://x.test/a"' in stripped and "TimeZone.current" not in stripped
    assert "Calendar.current" not in stripped and 'let v = "\\"//\\""' in stripped
    # Anti-vacuity on the real helper: its header DOES name the wire key and SwiftUI while
    # explaining them, so a stripper that kept comments would see them.
    raw = _FILES["helper"].read_text(encoding="utf-8")
    assert "SwiftUI" in raw and "SwiftUI" not in _strip(raw)


# ── the wire matches the backend's ONE list ──────────────────────────────────


def test_the_swift_keys_are_the_backend_contract():
    """Every DTO decodes exactly the keys the backend writes (`app/schemas/listing_status.py`),
    and each response model iOS decodes declares them nullable (default None)."""
    from app.schemas.home_dashboard import MarketPulseItemResponse
    from app.schemas.listing_status import LISTING_FIELDS
    from app.schemas.stock_overview import StockOverviewCoreResponse, StockOverviewResponse
    from app.schemas.tracking import TrackedAssetResponse

    assert set(LISTING_FIELDS) == set(_WIRE_KEYS.values())
    src = _sources()
    for key, decl in _DTOS:
        block = _braced(_code(src, key), decl)
        found = set(re.findall(r'case \w+ = "(listing_status|delisted_date|delisted_exchange|last_trade_date)"', block))
        assert found == set(LISTING_FIELDS), f"{decl}: {sorted(found)}"
    for model in (StockOverviewCoreResponse, StockOverviewResponse, TrackedAssetResponse, MarketPulseItemResponse):
        for field in LISTING_FIELDS:
            assert field in model.model_fields, f"{model.__name__} lacks {field}"
            assert model.model_fields[field].default is None, f"{model.__name__}.{field} must default to None"
        assert model.model_fields["listing_status"].is_required() is False


# ── executed ─────────────────────────────────────────────────────────────────

_HARNESS = r"""
let us = Locale(identifier: "en_US")
var failures = 0
var checks = 0
func check(_ name: String, _ got: String?, _ expect: String?) {
    checks += 1
    if got == expect { print("ok|\(name)") } else {
        failures += 1
        print("FAIL|\(name)|\(got ?? "nil")|\(expect ?? "nil")")
    }
}
print("zone|\(TimeZone.current.identifier)")

// Days: the same calendar day in every zone, DST edges and the year boundary included.
check("nov9", DelistedListing.dayLabel("2026-11-09", locale: us), "Nov 9, 2026")
check("nov6", DelistedListing.dayLabel("2026-11-06", locale: us), "Nov 6, 2026")
check("dst_start", DelistedListing.dayLabel("2026-03-08", locale: us), "Mar 8, 2026")
check("dst_end", DelistedListing.dayLabel("2026-11-01", locale: us), "Nov 1, 2026")
check("dec31", DelistedListing.dayLabel("2026-12-31", locale: us), "Dec 31, 2026")
check("jan1", DelistedListing.dayLabel("2027-01-01", locale: us), "Jan 1, 2027")
check("leap", DelistedListing.dayLabel("2028-02-29", locale: us), "Feb 29, 2028")
check("valid_echo", DelistedListing.validDay("2026-11-09"), "2026-11-09")
check("phone_locale_reads_something", DelistedListing.dayLabel("2026-11-09") == nil ? "nil" : "ok", "ok")
let bad: [String] = ["", "2026-13-01", "2026-02-30", "2027-02-29", "11/09/2026", "2026-11-09T00:00:00Z",
                     " 2026-11-09", "2026-11-09 ", "2026-11-9", "2026-1-09", "20261109", "abcd-ef-gh",
                     "2026-00-10", "2026-11-00", "２０２６-11-09"]
for raw in bad {
    check("bad_valid[\(raw.debugDescription)]", DelistedListing.validDay(raw), nil)
    check("bad_label[\(raw.debugDescription)]", DelistedListing.dayLabel(raw, locale: us), nil)
}
check("nil_day", DelistedListing.validDay(nil), nil)

// The one wire rule: trimmed, any case, the exact word — else fail open.
for yes in ["delisted", "DELISTED", " delisted ", "Delisted\n"] {
    check("from_yes[\(yes.debugDescription)]", DelistedListing.from(status: yes, delistedDate: nil, exchange: nil, lastTradeDate: nil) == nil ? "nil" : "set", "set")
}
let noes: [String?] = [nil, "", "active", "listed", "delisted_pending", "not delisted", "de listed"]
for no in noes {
    check("from_no[\(no ?? "nil")]", DelistedListing.from(status: no, delistedDate: "2026-11-09", exchange: "NASDAQ", lastTradeDate: "2026-11-06") == nil ? "nil" : "set", "nil")
}

// ROKU, every line.
let roku = DelistedListing.from(status: "delisted", delistedDate: "2026-11-09", exchange: "NASDAQ", lastTradeDate: "2026-11-06")!
check("roku_equals_sample", roku == DelistedListing.sample ? "eq" : "ne", "eq")
check("roku_headline", roku.headline(locale: us), "Delisted from NASDAQ · Nov 9, 2026")
check("roku_exchange_and_date", roku.exchangeAndDate(locale: us), "NASDAQ · Nov 9, 2026")
check("roku_detail_line", roku.detailLine(locale: us), "from NASDAQ · Nov 9, 2026")
check("roku_spoken", roku.spokenHeadline(locale: us), "Delisted from NASDAQ, Nov 9, 2026")
check("roku_last_close", roku.lastCloseLine(locale: us), "Last close · Nov 6, 2026")
check("roku_notice", roku.notice(symbol: "ROKU", locale: us),
      "ROKU was delisted from NASDAQ on Nov 9, 2026. The price shown is its last close, from Nov 6, 2026, and no longer updates.")
check("roku_share", roku.shareLine(lastClose: "$160.52", locale: us),
      "Delisted from NASDAQ · Nov 9, 2026\nLast close $160.52 (Nov 6, 2026)")
check("roku_ai", roku.aiContextLines(lastClose: "$160.52", locale: us).joined(separator: "|"),
      "Listing status: delisted from NASDAQ on Nov 9, 2026; there is no live quote.|Last close: $160.52 on Nov 6, 2026.")

// D4: an exchange move reads "Delisted from NYSE", never "stopped trading".
let dds = DelistedListing.from(status: "delisted", delistedDate: "2026-10-05", exchange: "NYSE", lastTradeDate: "2026-10-03")!
check("dds_headline", dds.headline(locale: us), "Delisted from NYSE · Oct 5, 2026")

// Missing fields drop their clause; nothing is guessed.
let noExchange = DelistedListing(delistedDay: "2026-10-05", exchange: nil, lastTradeDay: nil)
check("noex_headline", noExchange.headline(locale: us), "Delisted · Oct 5, 2026")
check("noex_exchange_and_date", noExchange.exchangeAndDate(locale: us), "Oct 5, 2026")
check("noex_detail_line", noExchange.detailLine(locale: us), "Oct 5, 2026")
check("noex_spoken", noExchange.spokenHeadline(locale: us), "Delisted, Oct 5, 2026")
check("noex_last_close", noExchange.lastCloseLine(locale: us), "Last close")
check("noex_notice", noExchange.notice(symbol: "DDS", locale: us),
      "DDS was delisted on Oct 5, 2026. The price shown is its last close and no longer updates.")
let noDate = DelistedListing(delistedDay: "2026/10/05", exchange: "  NYSE ", lastTradeDay: "2026-10-03")
check("nodate_day_dropped", noDate.delistedDay, nil)
check("nodate_exchange_trimmed", noDate.exchange, "NYSE")
check("nodate_headline", noDate.headline(locale: us), "Delisted from NYSE")
check("nodate_exchange_and_date", noDate.exchangeAndDate(locale: us), "NYSE")
check("nodate_detail_line", noDate.detailLine(locale: us), "from NYSE")
check("nodate_last_close", noDate.lastCloseLine(locale: us), "Last close · Oct 3, 2026")
check("nodate_notice_blank_symbol", noDate.notice(symbol: "  ", locale: us),
      "This stock was delisted from NYSE. The price shown is its last close, from Oct 3, 2026, and no longer updates.")
let bare = DelistedListing.from(status: "delisted", delistedDate: nil, exchange: "   ", lastTradeDate: "2026-02-30")!
check("bare_headline", bare.headline(locale: us), "Delisted")
check("bare_exchange_and_date", bare.exchangeAndDate(locale: us), nil)
check("bare_detail_line", bare.detailLine(locale: us), nil)
check("bare_spoken", bare.spokenHeadline(locale: us), "Delisted")
check("bare_last_close", bare.lastCloseLine(locale: us), "Last close")
check("bare_share", bare.shareLine(lastClose: "$1.00", locale: us), "Delisted\nLast close $1.00")
check("bare_ai", bare.aiContextLines(lastClose: "$1.00", locale: us).joined(separator: "|"),
      "Listing status: delisted; there is no live quote.|Last close: $1.00.")
check("bare_notice", bare.notice(symbol: "XYZ", locale: us),
      "XYZ was delisted. The price shown is its last close and no longer updates.")
let longExchange = DelistedListing(delistedDay: nil, exchange: String(repeating: "X", count: 41), lastTradeDay: nil)
check("long_exchange_dropped", longExchange.exchange, nil)
print("COUNT|\(checks)")
print("DONE|\(failures)")
"""

_EXPECTED_CHECKS = 83  # 9 days + 15×2 bad + 1 + 4 yes + 7 no + 9 ROKU + 1 DDS + 6 + 7 + 8 + 1
_ZONES = ("Pacific/Kiritimati", "Pacific/Pago_Pago")


def _run_executed_helper(tz: str) -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    env = dict(os.environ, TZ=tz)
    try:
        proc = subprocess.run(["xcrun", "swift", "-"],
                              input=_FILES["helper"].read_text(encoding="utf-8") + "\n" + _HARNESS,
                              text=True, capture_output=True, timeout=240, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail("the Swift harness did not complete — DelistedListing probably stopped compiling "
                    f"standalone (a SwiftUI import?)\nstdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}")
    return proc.stdout


@pytest.mark.parametrize("tz", _ZONES)
def test_the_helper_executed_keeps_every_day_in_every_zone(tz):
    out = _run_executed_helper(tz)
    lines = out.splitlines()
    zone = next((l.split("|", 1)[1] for l in lines if l.startswith("zone|")), None)
    assert zone == tz, f"the harness ran in {zone!r}, not {tz!r} — the zone sweep is vacuous"
    fails = [l for l in lines if l.startswith("FAIL|")]
    assert not fails, "\n".join(fails)
    count = next((int(l.split("|", 1)[1]) for l in lines if l.startswith("COUNT|")), -1)
    assert count == _EXPECTED_CHECKS, f"the harness ran {count} checks, expected {_EXPECTED_CHECKS}"
    assert sum(1 for l in lines if l.startswith("ok|")) == _EXPECTED_CHECKS
    assert "DONE|0" in lines
