"""iOS market times are New York time, with a TRUE label (owner, 2026-10-29: "ET for markets").

The rule this pins, iOS half:
  * MARKET times — prices and their "as of" times, market status and hours, chart axes and the
    chart crosshair, pre-market and after-hours — are shown in New York time, labelled "ET" or
    the real "EDT"/"EST" of the instant. Never a hard-coded "EST": it is false while New York is
    on EDT, from March to November.
  * A date-only value never shifts by zone: it is parsed and formatted in the same zone.
  * A date beside an ET time is the NEW YORK date of that instant, never the phone's: for a
    4:00 PM ET close a phone in Tokyo is already on the next day.

What was wrong, and the guard (`GUARDS`) that pins each fix:
  chart_market_time      `ChartDateFormatters` had no zone, so a Denver phone's 1D axis ran
                         7:30 AM - 2:00 PM and its crosshair read 12:30 ET as "10:30 AM". Every
                         formatter the enum declares is now New York time (DERIVED from the enum,
                         not listed), a date-only bar is PARSED in that zone too, the intraday
                         crosshair says " ET", and the session axis and the label rules read a
                         New York calendar: no `Calendar.current` left in either chart file.
  closed_badge_date      `MarketStatusBadge` and `MarketStatus.displayText` printed the close's
                         DATE in the phone's zone beside the server's New York "4:00 PM EDT".
  zone_literal_ratchet   no "EST"/"EDT" in any string literal under ios/, CaydexWidgets/ or Shared/
                         outside #Preview blocks, except a label New York's own zone chooses for
                         the instant it names (`newYork.isDaylightSavingTime(for:)`) and an allow-list
                         (preview-only sample data whose instant really is EST; "EST." meaning
                         estimated), each entry with its reason and matched exactly once.
  fallback_badge_rule    `TickerDetailViewModel.determineMarketStatus` was a weekday-and-hour copy
                         (no holidays, no half-days, `now` at "4:00 PM EST"); it now reads
                         `MarketHoursUtil.liveSessionStatus(at:)`, else the shared closed line
                         `MarketStatus.lastRegularClosed(before:)`: the last close, its own New
                         York clock (`closeClock`, POSIX "h:mm a": "1:00 PM" on a half-day) and
                         the true EDT/EST of that instant — all three pinned exactly.
  last_close_rule        `MarketHoursUtil.lastRegularClose(before:)`, the Swift copy of the closed
                         branch of `market_status_fields`, reads `holidays` and `earlyCloses`.
  live_session_rule      the open / pre-market / after-hours half the badge reads instead
                         (`isMarketActive`, `liveSessionStatus`). Both rules are pinned exactly,
                         transliterated with the two tables READ from the Swift, and checked against
                         the real backend at every instant that matters, 2025-2028
                         (`test_the_swift_fallback_badge_agrees_with_the_backend`).
  server_label_fallback  the four `market_status` mappings fall back to "ET", never "EST".
  chat_card_as_of        the chat stock card's message-instant "As of" prints its New York clock
                         and " ET"; its bar-date "As of" stays a one-zone date-only round trip.
  snapshot_day_round_trip
                         the index Market Snapshots `generated_date`, a New York day, was parsed at
                         the PHONE's midnight and printed in New York: every phone east of New York
                         said "Updated on" the day before. Now parsed and printed in New York,
                         Gregorian both ways.
  closed_date_fallback   a closed status with no readable server date showed "now" ("Oct 31, 4:00 PM
                         ET" on a Saturday); it now shows `MarketStatus.lastRegularClosed`'s whole
                         line. The server's clock and label are read only WITH the server's date,
                         and a server close with no clock gets its own (`closeClock`), never a
                         fixed "4:00 PM" beside a 1:00 PM half-day.
  axis_zone_label        the chart axes print New York times but named no zone, so a Denver
                         reader took "9:30 AM" for their own clock. The last label of the 1D axis
                         and of a clock-format bar axis now ends in " ET" ("4:00 PM ET"), and each
                         1D label sits at its own time rather than spread by text width.
  commodity_close_date   the commodity closed line printed its date in the phone's zone; it now
                         prints the close's New York date through `MarketStatus.closeDay`.
  news_naive_stamp_new_york
                         FMP's zone-less "yyyy-MM-dd HH:mm:ss" is New York wall time (the backend's
                         `to_utc_instant`, verified live), but five parsers read it as UTC (4-5
                         hours off, and parity with Updates was two parsers wrong the same way) and
                         two dead mappers in the device zone. DERIVED: every formatter in the app
                         that parses that pattern has New York in force at its parse. A bare news
                         day parses at the phone's midnight, the zone `timeAgo` prints it in.

Source scans (there is no XCTest target, testing.md §3): a small Swift lexer blanks comments
without eating string literals (a URL's `//` is not a comment), every check is scoped to its
brace-bounded declaration, and `MUTATIONS` breaks each guard once IN MEMORY and asserts it fails
(`test_every_guard_kills_its_mutation`), so no guard here has only ever been seen passing.
"""

from __future__ import annotations

import functools
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_EASTERN = ZoneInfo("America/New_York")

_MODELS = "Models/TickerDetailModels.swift"
_BADGE = "Views/Atoms/MarketStatusBadge.swift"
_VM = "ViewModels/TickerDetailViewModel.swift"
_HOURS = "Core/Utilities/MarketHoursUtil.swift"
_AXIS = "Views/Molecules/Chart/ChartXAxisLabels.swift"
_COORDS = "Views/Molecules/Chart/ChartCoordinateSystem.swift"
_CHAT_CARD = "Views/Molecules/ChatStockWidgetView.swift"
_OVERVIEW = "Models/StockOverviewResponseModels.swift"
_ETF = "Models/ETFDetailResponseModels.swift"
_INDEX = "Models/IndexDetailResponseModels.swift"
_INDEX_SECTION = "Views/Organisms/IndexDetailSnapshotsSection.swift"
_COMMODITY = "Models/CommodityDetailModels.swift"
_ETF_VM = "ViewModels/ETFDetailViewModel.swift"
_INDEX_VM = "ViewModels/IndexDetailViewModel.swift"
_CRYPTO_VM = "ViewModels/CryptoDetailViewModel.swift"
_COMMODITY_VM = "ViewModels/CommodityDetailViewModel.swift"
_UPDATES = "Models/UpdatesModels.swift"
_GUARDED = (_MODELS, _BADGE, _VM, _HOURS, _AXIS, _COORDS, _CHAT_CARD, _OVERVIEW, _ETF, _INDEX,
            _INDEX_SECTION, _COMMODITY, _ETF_VM, _INDEX_VM, _CRYPTO_VM, _COMMODITY_VM, _UPDATES)
# Swept beside ios/ (keys relative to frontend/ios): the widget extension and the code it shares.
_WIDGET_ROOTS = ("CaydexWidgets", "Shared")
_WIDGET = "CaydexWidgets/MoversWidget.swift"
_WIDGET_STORE = "Shared/WidgetSnapshotStore.swift"

_ZONE_TOKEN = re.compile(r"\b(?:EST|EDT)\b")


# ── A small Swift lexer ──────────────────────────────────────────────────────────────────────

_CODE_TOKEN = re.compile(r'//|/\*|#*"|[()]')
_BLOCK_COMMENT_TOKEN = re.compile(r"/\*|\*/")


def _blank(buf: list[str], start: int, end: int) -> None:
    for k in range(start, end):
        if buf[k] != "\n":
            buf[k] = " "


class _Swift:
    """One Swift file, lexed. `code` is the source with every comment blanked; `skeleton` also
    blanks every string literal's text, so a brace or `#Preview` in it is CODE; `literals` holds
    `(start, end, text)` for each string literal, interpolated code left out of `text` (a literal
    nested in an interpolation is listed on its own). Blanking keeps newlines and offsets, so a
    position means the same place in `src`, `code` and `skeleton`."""

    def __init__(self, src: str):
        self.src = src
        self._code = list(src)
        self._skel = list(src)
        self.literals: list[tuple[int, int, str]] = []
        self._scan_code(0, interpolation=False)
        self.code = "".join(self._code)
        self.skeleton = "".join(self._skel)

    def _scan_code(self, i: int, *, interpolation: bool) -> int:
        """Scan code from `i`: to the end of the file, or to the `)` closing an interpolation."""
        src, depth = self.src, 0
        while True:
            m = _CODE_TOKEN.search(src, i)
            if m is None:
                return len(src)
            token, i = m.group(), m.start()
            if token == "//":
                end = src.find("\n", i)
                end = len(src) if end == -1 else end
                _blank(self._code, i, end)
                _blank(self._skel, i, end)
                i = end
            elif token == "/*":
                end, nest = i + 2, 1
                while nest:
                    inner = _BLOCK_COMMENT_TOKEN.search(src, end)
                    if inner is None:
                        end = len(src)
                        break
                    nest += 1 if inner.group() == "/*" else -1
                    end = inner.end()
                _blank(self._code, i, end)
                _blank(self._skel, i, end)
                i = end
            elif token.endswith('"'):
                i = self._scan_string(i, hashes=len(token) - 1)
            elif token == "(":
                depth += 1 if interpolation else 0
                i += 1
            else:
                if interpolation:
                    if depth == 0:
                        return i
                    depth -= 1
                i += 1

    def _take(self, text: list[str], start: int, end: int) -> None:
        text.append(self.src[start:end])
        _blank(self._skel, start, end)

    def _scan_string(self, start: int, *, hashes: int) -> int:
        """Scan the literal whose delimiter (`#`s, then `"` or `\"\"\"`) starts at `start`."""
        src = self.src
        i = start + hashes
        multiline = src.startswith('"""', i)
        i += 3 if multiline else 1
        close = ('"""' if multiline else '"') + "#" * hashes
        escape = "\\" + "#" * hashes
        stops = [close, escape] + ([] if multiline else ["\n"])
        stop = re.compile("|".join(re.escape(t) for t in stops))
        text: list[str] = []
        while True:
            m = stop.search(src, i)
            if m is None:
                self._take(text, i, len(src))
                self.literals.append((start, len(src), "".join(text)))
                return len(src)
            self._take(text, i, m.start())
            token, i = m.group(), m.start()
            if token == close:
                end = i + len(close)
                self.literals.append((start, end, "".join(text)))
                return end
            if token == "\n":
                # An unterminated single-line literal ends at its line.
                self.literals.append((start, i, "".join(text)))
                return i
            after = i + len(escape)
            if after < len(src) and src[after] == "(":
                text.append("\x00")
                i = self._scan_code(after + 1, interpolation=True) + 1
            else:
                self._take(text, i, min(after + 1, len(src)))
                i = after + 1

    # ── Spans ────────────────────────────────────────────────────────────────────────────

    def _match(self, at: int, opener: str, closer: str) -> tuple[int, int]:
        """(start, end) of the balanced `opener`…`closer` group whose opener is the first at or
        after `at`, matched on the skeleton (a brace inside a string is not code)."""
        first = self.skeleton.find(opener, at)
        assert first != -1, f"no {opener!r} after offset {at}"
        depth = 0
        for k in range(first, len(self.skeleton)):
            ch = self.skeleton[k]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return first, k + 1
        raise AssertionError(f"unbalanced {opener}{closer} after offset {at}")

    def _header(self, header: str) -> int:
        count = self.code.count(header)
        assert count == 1, f"guard is stale — expected exactly one {header!r}, found {count}"
        return self.code.index(header)

    def span(self, header: str) -> tuple[int, int]:
        """The brace-bounded block of the ONE declaration `header` starts."""
        return self._match(self._header(header), "{", "}")

    def block(self, header: str) -> str:
        start, end = self.span(header)
        return self.code[start:end]

    def call_span(self, header: str) -> tuple[int, int]:
        """The parenthesised argument list of the ONE call `header` (ending in `(`) starts."""
        assert header.endswith("("), header
        return self._match(self._header(header) + len(header) - 1, "(", ")")

    def preview_spans(self) -> list[tuple[int, int]]:
        return [self._match(m.end(), "{", "}") for m in re.finditer(r"#Preview\b", self.skeleton)]


@functools.lru_cache(maxsize=128)
def _lexed(src: str) -> _Swift:
    return _Swift(src)


def _code(s: dict[str, str], rel: str) -> _Swift:
    return _lexed(s[rel])


def _norm(text: str) -> str:
    """Whitespace-collapsed, so an exact-statement check survives re-indentation only."""
    return " ".join(text.split())


@functools.lru_cache(maxsize=1)
def _tree() -> tuple[tuple[str, str], ...]:
    """Every Swift file of the app (keyed below ios/) and of the widget extension and the code
    it shares with the app (keyed "CaydexWidgets/…" and "Shared/…": no folder of that name
    exists under ios/, so a key names one file). The widget prints market times too."""
    assert _IOS.is_dir(), f"guard is stale — {_IOS} moved"
    files = [(path.relative_to(_IOS).as_posix(), path) for path in sorted(_IOS.rglob("*.swift"))]
    for extra in _WIDGET_ROOTS:
        root = _IOS.parent / extra
        assert root.is_dir(), f"guard is stale — {root} moved"
        assert not (_IOS / extra).exists(), f"ios/{extra} now exists: its keys would collide"
        files += [(path.relative_to(_IOS.parent).as_posix(), path) for path in sorted(root.rglob("*.swift"))]
    return tuple((key, path.read_text(encoding="utf-8")) for key, path in files)


def _sources() -> dict[str, str]:
    """Every Swift file under ios/, keyed by its path below it, plus the widget's (`_tree`). A
    fresh dict per call, so a mutation never leaks into the next check."""
    tree = dict(_tree())
    for rel in _GUARDED:
        assert rel in tree, f"guard is stale — {rel} moved"
    return tree


# ── (a) chart_market_time ────────────────────────────────────────────────────────────────────

_DECLARED_FORMATTER = re.compile(r"static let (\w+): DateFormatter = (marketFormatter\(|\{)")


def _check_chart_market_time(s: dict[str, str]) -> None:
    models = _code(s, _MODELS)
    enum = models.block("enum ChartDateFormatters {")
    assert 'static let marketTimeZone: TimeZone = TimeZone(identifier: "America/New_York")' in enum, (
        "the chart's zone is no longer America/New_York — every axis tick and crosshair reads the phone's clock"
    )
    factory = models.block("private static func marketFormatter(_ pattern: String) -> DateFormatter")
    assert "f.timeZone = marketTimeZone" in factory, "the chart's display formatters print the phone's zone"
    assert "f.calendar = Calendar(identifier: .gregorian)" in factory, "a non-Gregorian phone relabels the market's year"

    # DERIVED: every DateFormatter the enum declares is market time — a new one cannot slip in.
    declared = _DECLARED_FORMATTER.findall(enum)
    names = {name for name, _ in declared}
    assert {"inputFormatter", "inputDateTimeFormatter", "timeOnly", "weekdayTime", "fullDateTime",
            "dayMonth", "fullDate"} <= names, f"anti-vacuity: the sweep found only {sorted(names)}"
    assert len(re.findall(r"static let \w+: DateFormatter\b", enum)) == len(declared), (
        "a ChartDateFormatters formatter is declared some other way and escapes this sweep"
    )
    closures = [name for name, how in declared if how == "{"]
    assert enum.count("DateFormatter()") == 1 + len(closures), (
        "a DateFormatter() is built in ChartDateFormatters outside the factory and the declared closures"
    )
    parse = models.block("static let inputFormatter: DateFormatter = {")
    assert "f.timeZone = marketTimeZone" in parse, (
        "a date-only bar must be PARSED in the zone it is formatted in, or it prints the day before"
    )
    for name in closures:
        body = models.block(f"static let {name}: DateFormatter = {{")
        assert "f.timeZone = marketTimeZone" in body, f"ChartDateFormatters.{name} is not in market time"

    crosshair = models.block("func formatDateForCrosshair(_ dateString: String) -> String")
    intraday = crosshair[crosshair.index("if isIntraday {"):]
    intraday = intraday[: intraday.index("}") + 1]
    assert 'return ChartDateFormatters.fullDateTime.string(from: date) + " ET"' in intraday, (
        "the intraday crosshair chip prints a New York clock without saying ET"
    )

    coords = _code(s, _COORDS)
    labels = coords.block("static func sessionTimeLabels(")
    assert 'TimeZone(identifier: "America/New_York")' in labels and "etCalendar.timeZone = etTimeZone" in labels
    assert "let day = etCalendar.startOfDay(for: referenceDate ?? Date())" in labels, (
        "the session axis must take its day from the New York calendar"
    )
    assert "guard let labelDate = etCalendar.date(" in labels, "the axis clock must be New York's"
    # A WALL-CLOCK minute of the New York day: elapsed minutes slip an hour on a DST Sunday's
    # 23- or 25-hour round-the-clock day (crypto's axis read 9:00 AM / 5:00 PM / 1:00 AM), and
    # `% 24` reads the window's 24:00 end as 12:00 AM.
    assert ("let minuteOfDay = window.openMinute + Int(minuteOffset) "
            "guard let labelDate = etCalendar.date( bySettingHour: (minuteOfDay / 60) % 24, "
            "minute: minuteOfDay % 60, second: 0, of: day ) else { continue }") in _norm(labels), (
        "the session axis no longer sets a New York wall-clock hour of the day"
    )
    assert "let formatter = ChartDateFormatters.timeOnly" in labels
    assert "Calendar.current" not in coords.code, "ChartCoordinateSystem reads the phone's calendar again"

    axis = _code(s, _AXIS)
    rules = axis.block("private static func format(first: String, last: String) -> DateFormatter")
    assert "let calendar = ChartDateFormatters.marketCalendar" in rules, (
        "'this year' / 'this week' must be judged in the zone the labels print in"
    )
    assert "Calendar.current" not in axis.code, "ChartXAxisLabels reads the phone's calendar again"
    calendar = models.block("static let marketCalendar: Calendar = {")
    assert "Calendar(identifier: .gregorian)" in calendar and "calendar.timeZone = marketTimeZone" in calendar
    # Anti-vacuity: the 1D axis really is these labels, measured from the first bar in view.
    session = axis.block("private var sessionLabels: some View")
    assert "TradingDayHelper.sessionTimeLabels(" in session
    assert "ChartDateFormatters.parseDate(series.dates[$0])" in session


# ── (b) closed_badge_date ────────────────────────────────────────────────────────────────────

def _outside_previews(swift: _Swift) -> str:
    out = list(swift.code)
    for start, end in swift.preview_spans():
        _blank(out, start, end)
    return "".join(out)


def _check_closed_badge_date(s: dict[str, str]) -> None:
    badge = _code(s, _BADGE)
    info = badge.block("private var statusAdditionalInfo: String?")
    assert "return MarketStatus.closeLine(date: date, time: time, timezone: timezone)" in info, (
        "the closed badge no longer prints the close's New York date"
    )
    assert "DateFormatter" not in _outside_previews(badge), (
        "MarketStatusBadge builds its own formatter again — in the phone's zone"
    )
    assert "if let additionalInfo = statusAdditionalInfo {" in badge.block("var body: some View"), (
        "anti-vacuity: the badge no longer draws the close line"
    )

    models = _code(s, _MODELS)
    start, end = models.span("enum MarketStatus {")
    display_start, display_end = models.span("var displayText: String {")
    assert start < display_start < display_end < end, "displayText moved out of MarketStatus"
    display = models.code[display_start:display_end]
    closed = display[display.index("case .closed(let date, let time, let timezone):"):]
    closed = closed[: closed.index("case .preMarket:")]
    assert 'return "Market Closed  \\(Self.closeLine(date: date, time: time, timezone: timezone))"' in closed, (
        "MarketStatus.displayText no longer prints the close's New York date"
    )
    line = models.block("static func closeLine(date: Date, time: String, timezone: String) -> String")
    assert "\\(closeDay(date))" in line, "closeLine no longer prints the day through closeDay"
    day = models.block("static func closeDay(_ date: Date) -> String")
    assert "TickerDetailFormatters.marketCloseDateFormatter.string(from: date)" in day
    formatter = models.block("static let marketCloseDateFormatter: DateFormatter = {")
    assert 'f.timeZone = TimeZone(identifier: "America/New_York")' in formatter, (
        "the close date prints in the phone's zone — Tokyo reads a 4:00 PM ET close as the next day"
    )
    assert "f.calendar = Calendar(identifier: .gregorian)" in formatter
    assert 'f.dateFormat = "MMM d"' in formatter


# ── (c) zone_literal_ratchet ─────────────────────────────────────────────────────────────────

# Every EST/EDT a string literal under ios/ may still hold outside a #Preview, with its reason.
# A RATCHET: a new entry needs a reason a reviewer accepts, and each entry must match exactly once
# (a stale one fails). Keyed (file, the declaration that holds it, the literal's text).
_ALLOWED_ZONE_LITERALS: dict[tuple[str, str, str], str] = {
    (_MODELS, "static let sampleApple = TickerDetailData(", "EST"):
        "preview-only sample data: a Dec 18, 2024 close, a day New York IS on EST",
    ("Models/ETFDetailModels.swift", "static let sampleSPY = ETFDetailData(", "EST"):
        "preview-only sample data: a February close (EST)",
    ("Models/IndexDetailModels.swift", "static let sampleSP500 = IndexDetailData(", "EST"):
        "preview-only sample data: a February close (EST)",
    ("Views/Molecules/CongressFlowSummaryCard.swift", "struct CongressFlowSummaryCard: View", "EST. TOTAL BUYS"):
        '"EST." is ESTIMATED, not a zone',
    ("Views/Molecules/CongressFlowSummaryCard.swift", "struct CongressFlowSummaryCard: View", "EST. TOTAL SELLS"):
        '"EST." is ESTIMATED, not a zone',
}

# A label CHOSEN for its instant BY NEW YORK'S ZONE is not a hard-coded zone. The receiver must
# be New York (the identifier itself, or a `newYork` / `marketTimeZone` DECLARED as New York —
# `_new_york_receiver`), and the instant must not be "now" in any spelling: the phone's zone,
# or today's DST, puts EDT on a winter close.
_DST_CHOSEN = re.compile(
    r'(?P<zone>\bnewYork|\bmarketTimeZone|TimeZone\(identifier: "America/New_York"\)!?)'
    r"\.isDaylightSavingTime\(for: (?!Date\(\)|(?:Date)?\.now\b|now\b|Date\(timeIntervalSinceNow)"
    r'(?:[^()]|\([^()]*\))*\)\s*\?\s*"EDT"\s*:\s*"EST"'
)
_NEW_YORK_DECLARATION = r'let {name}(?::\s*TimeZone)?\s*=\s*TimeZone\(identifier: "America/New_York"\)'


def _new_york_receiver(swift: "_Swift", m: re.Match) -> bool:
    """The receiver of a chosen label really is New York: the identifier itself, or a name
    declared as New York in the block around the label (or as a `static let` in the file). A
    variable merely NAMED `newYork` proves nothing."""
    zone = m.group("zone")
    if zone.startswith("TimeZone("):
        return True
    declared = _NEW_YORK_DECLARATION.format(name=re.escape(zone))
    try:
        start, end = _enclosing_block(swift, m.start())
    except AssertionError:  # top level: no block around it
        start = end = 0
    return bool(re.search(declared, swift.code[start:end])
                or re.search(r"static " + declared, swift.code))


def _check_zone_literal_ratchet(s: dict[str, str]) -> None:
    offenders: list[str] = []
    matched = dict.fromkeys(_ALLOWED_ZONE_LITERALS, 0)
    swept_files = swept_literals = previews = 0
    for rel, src in sorted(s.items()):
        if not _ZONE_TOKEN.search(src):
            continue
        swift = _lexed(src)
        swept_files += 1
        swept_literals += len(swift.literals)
        preview_spans = swift.preview_spans()
        previews += len(preview_spans)
        chosen = [m.span() for m in _DST_CHOSEN.finditer(swift.code) if _new_york_receiver(swift, m)]
        allowed = []
        for key in _ALLOWED_ZONE_LITERALS:
            if key[0] == rel:
                header = key[1]
                allowed.append((key, swift.call_span(header) if header.endswith("(") else swift.span(header)))
        for start, end, text in swift.literals:
            if not _ZONE_TOKEN.search(text):
                continue
            if any(a <= start < b for a, b in preview_spans):
                continue
            if any(a <= start and end <= b for a, b in chosen):
                continue
            key = next((k for k, (a, b) in allowed if k[2] == text and a <= start < b), None)
            if key is not None:
                matched[key] += 1
                continue
            line = src.count("\n", 0, start) + 1
            offenders.append(f'{rel}:{line}: "{text}"')
    assert swept_files >= 5 and swept_literals >= 500 and previews >= 3, (
        f"anti-vacuity: swept {swept_files} files / {swept_literals} literals / {previews} previews"
    )
    assert not offenders, (
        'a hard-coded EST/EDT label, false for part of every year — use "ET", or choose the label '
        'with New York\'s own zone for the instant it names, '
        '`newYork.isDaylightSavingTime(for: instant) ? "EDT" : "EST"`: ' + "; ".join(offenders)
    )
    stale = {k: n for k, n in matched.items() if n != 1}
    assert not stale, f"an allow-listed EST/EDT literal moved or multiplied — re-derive: {stale}"


# ── (d) fallback_badge_rule ──────────────────────────────────────────────────────────────────

# (header, exact body, what drifted). The clock is the close's own New York "h:mm a" in POSIX —
# the backend's "%-I:%M %p" — so a half-day reads "1:00 PM" with no second calendar to forget.
_CLOSED_LINE_PIECES = (
    ("static func lastRegularClosed(before instant: Date) -> MarketStatus",
     "{ let close: Date = MarketHoursUtil.lastRegularClose(before: instant) "
     'let newYork: TimeZone = TimeZone(identifier: "America/New_York")! '
     "return .closed( date: close, time: closeClock(close), "
     'timezone: newYork.isDaylightSavingTime(for: close) ? "EDT" : "EST" ) }',
     "the shared closed line no longer carries the last close, its own clock and its own EDT/EST"),
    ("static func closeClock(_ date: Date) -> String",
     "{ TickerDetailFormatters.marketCloseClockFormatter.string(from: date) }",
     "closeClock no longer reads the New York close-clock formatter"),
    ("static let marketCloseClockFormatter: DateFormatter = {",
     '{ let f = DateFormatter() f.locale = Locale(identifier: "en_US_POSIX") '
     "f.calendar = Calendar(identifier: .gregorian) "
     'f.timeZone = TimeZone(identifier: "America/New_York") f.dateFormat = "h:mm a" return f }',
     "the close clock is no longer New York's POSIX \"h:mm a\" (a 1:00 PM half-day, a 24-hour phone)"),
)


def _check_fallback_badge_rule(s: dict[str, str]) -> None:
    vm = _code(s, _VM)
    fn = vm.block("private func determineMarketStatus() -> MarketStatus")
    assert "if let session = MarketHoursUtil.liveSessionStatus(at: now) {" in fn, (
        "the fallback badge decides the session itself again — holidays and half-days are lost"
    )
    assert "return MarketStatus.lastRegularClosed(before: now)" in fn, (
        "the fallback badge builds its own closed line again instead of the shared one"
    )
    for copy in (".weekday", "Calendar.current", "abbreviation(", "date: now", "4 * 60", "20 * 60"):
        assert copy not in fn, f"determineMarketStatus keeps its own session math again ({copy!r})"
    # The shared closed line every fallback prints, its clock and that clock's formatter: the
    # three pieces `_swift_last_close` transliterates, pinned exactly (whitespace-collapsed).
    models = _code(s, _MODELS)
    for header, body, why in _CLOSED_LINE_PIECES:
        got = _norm(models.block(header))
        assert got == body, f"{why} — re-derive the transliteration: {got!r}"
    for copy in (".weekday", "9 * 60 + 30", "16 * 60"):
        assert copy not in vm.code, f"TickerDetailViewModel holds a weekday-and-hour session copy ({copy!r})"
    assert "let marketStatus = determineMarketStatus()" in vm.code, (
        "anti-vacuity: the fallback model no longer reads determineMarketStatus"
    )


# ── (e) last_close_rule ──────────────────────────────────────────────────────────────────────

_LAST_CLOSE = "static func lastRegularClose(before instant: Date) -> Date"
_BELL = "private static func closeMinuteOfDay(_ day: Date, calendar: Calendar) -> Int"
_TRADING_DAY = "nonisolated private static func isTradingDay(_ day: Date, calendar: Calendar) -> Bool"

# The two bodies `_swift_last_close` transliterates, pinned exactly (whitespace-collapsed).
_LAST_CLOSE_BODY = (
    "{ let calendar: Calendar = etCalendar "
    "let today: Date = calendar.startOfDay(for: instant) "
    "let parts: DateComponents = calendar.dateComponents([.hour, .minute], from: instant) "
    "let minuteOfDay: Int = (parts.hour ?? 0) * 60 + (parts.minute ?? 0) "
    "var day: Date = today "
    "if !isTradingDay(today, calendar: calendar) || minuteOfDay < closeMinuteOfDay(today, calendar: calendar) "
    "{ day = previousTradingDay(before: today, calendar: calendar) } "
    "let closeMinute: Int = closeMinuteOfDay(day, calendar: calendar) "
    "var close: DateComponents = calendar.dateComponents([.year, .month, .day], from: day) "
    "close.hour = closeMinute / 60 "
    "close.minute = closeMinute % 60 "
    "return calendar.date(from: close) ?? day }"
)
_BELL_BODY = (
    "{ let parts: DateComponents = calendar.dateComponents([.year, .month, .day], from: day) "
    'let key: String = String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0) '
    "return earlyCloses.contains(key) ? 13 * 60 : 16 * 60 }"
)


def _check_last_close_rule(s: dict[str, str]) -> None:
    hours = _code(s, _HOURS)
    fn = _norm(hours.block(_LAST_CLOSE))
    assert fn == _LAST_CLOSE_BODY, f"lastRegularClose drifted from market_status_fields — re-derive: {fn!r}"
    bell = _norm(hours.block(_BELL))
    assert bell == _BELL_BODY, f"the half-day bell no longer reads earlyCloses — re-derive: {bell!r}"
    trading = hours.block(_TRADING_DAY)
    assert "return !holidays.contains(key)" in trading, "isTradingDay no longer reads holidays"
    calendar = hours.block("nonisolated private static let etCalendar: Calendar =")
    assert 'calendar.timeZone = TimeZone(identifier: "America/New_York")' in calendar


# ── live_session_rule: the other half of the fallback badge ──────────────────────────────────

_ACTIVE = "static func isMarketActive(at now: Date = Date()) -> Bool"
_LIVE = "static func liveSessionStatus(at now: Date = Date()) -> MarketStatus?"

# The two bodies `_swift_live_session` transliterates, pinned exactly (whitespace-collapsed).
_ACTIVE_BODY = (
    "{ let calendar = etCalendar "
    "let components = calendar.dateComponents([.weekday, .hour, .minute, .year, .month, .day], from: now) "
    "guard let weekday = components.weekday else { return false } "
    "if weekday == 1 || weekday == 7 { return false } "
    "guard let year = components.year, let month = components.month, let day = components.day, "
    "let hour = components.hour, let minute = components.minute else { return false } "
    'let key = String(format: "%04d-%02d-%02d", year, month, day) '
    "if holidays.contains(key) { return false } "
    "let minuteOfDay = hour * 60 + minute "
    "if earlyCloses.contains(key) && minuteOfDay >= 13 * 60 { return false } "
    "return (240..<1200).contains(minuteOfDay) }"
)
_LIVE_BODY = (
    "{ guard isMarketActive(at: now) else { return nil } "
    "let components = etCalendar.dateComponents([.hour, .minute], from: now) "
    "guard let hour = components.hour, let minute = components.minute else { return nil } "
    "let minuteOfDay = hour * 60 + minute "
    "if minuteOfDay < 9 * 60 + 30 { return .preMarket } "
    "if minuteOfDay < 16 * 60 { return .open } "
    "return .afterHours }"
)


def _check_live_session_rule(s: dict[str, str]) -> None:
    """`determineMarketStatus` takes open / pre-market / after-hours from these two, so they are
    pinned as transliterated: the fallback badge must name the session `session_phase` names."""
    hours = _code(s, _HOURS)
    active = _norm(hours.block(_ACTIVE))
    assert active == _ACTIVE_BODY, f"isMarketActive drifted from session_phase — re-derive: {active!r}"
    live = _norm(hours.block(_LIVE))
    assert live == _LIVE_BODY, f"liveSessionStatus drifted from session_phase — re-derive: {live!r}"


# ── server_label_fallback ────────────────────────────────────────────────────────────────────

_SERVER_MAPPINGS = (
    (_OVERVIEW, "var resolvedMarketStatus: MarketStatus", 'timezone: timezone ?? "ET")'),
    (_OVERVIEW, "func toDisplayModel() -> TickerDetailData", 'timezone: marketStatus.timezone ?? "ET"'),
    (_ETF, "func toDisplayModel() -> ETFDetailData", 'timezone: marketStatus.timezone ?? "ET"'),
    (_INDEX, "func toDisplayModel() -> IndexDetailData", 'timezone: marketStatus.timezone ?? "ET"'),
)


def _check_server_label_fallback(s: dict[str, str]) -> None:
    for rel, header, fallback in _SERVER_MAPPINGS:
        assert fallback in _code(s, rel).block(header), (
            f"{rel} `{header}`: a closed status with no label must fall back to \"ET\""
        )


# ── chat_card_as_of ──────────────────────────────────────────────────────────────────────────

def _check_chat_card_as_of(s: dict[str, str]) -> None:
    card = _code(s, _CHAT_CARD)
    label = card.block("private var asOfLabel: String?")
    assert 'if let messageDate { return Self.asOfInstantFormatter.string(from: messageDate) + " ET" }' in label, (
        "the chat card's message-instant 'As of' prints a New York time without saying ET"
    )
    assert "return Self.asOfFormatter.string(from: date)" in label, "anti-vacuity: the bar-date path moved"
    instant = card.block("private static let asOfInstantFormatter: DateFormatter = {")
    assert "f.timeZone = marketTimeZone" in instant, "the message instant prints in the phone's zone"
    assert 'f.setLocalizedDateFormatFromTemplate("MMMdyyyyjmm")' in instant and "dateFormat =" not in instant
    assert 'private static let marketTimeZone = TimeZone(identifier: "America/New_York")' in card.code
    for name in ("isoDayFormatter", "asOfFormatter"):
        body = card.block(f"private static let {name}: DateFormatter = {{")
        assert "f.timeZone = marketTimeZone" in body, (
            f"{name}: a date-only bar must be parsed and formatted in the same zone"
        )


# ── snapshot_day_round_trip ──────────────────────────────────────────────────────────────────

def _check_snapshot_day_round_trip(s: dict[str, str]) -> None:
    index = _code(s, _INDEX)
    parse = index.block("static func parseNewYorkDay(_ string: String) -> Date? {")
    for statement in ('df.timeZone = TimeZone(identifier: "America/New_York")',
                      "df.calendar = Calendar(identifier: .gregorian)",
                      'df.locale = Locale(identifier: "en_US_POSIX")', 'df.dateFormat = "yyyy-MM-dd"'):
        assert statement in parse, f"parseNewYorkDay lost `{statement}`"
    mapping = _norm(index.block("func toDisplayModel() -> IndexDetailData"))
    assert ("let genDate = IndexResponseFormatters.parseNewYorkDay(snapshotsData.generatedDate) "
            "?? IndexResponseFormatters.parseISO8601(snapshotsData.generatedDate) ?? Date()") in mapping, (
        "generated_date is read at the phone's midnight again — east of New York that is the day before"
    )
    shown = _code(s, _INDEX_SECTION).block("private var formattedDate: String {")
    assert 'formatter.timeZone = TimeZone(identifier: "America/New_York")' in shown
    assert "formatter.calendar = Calendar(identifier: .gregorian)" in shown, (
        "a Buddhist-calendar phone would print the New York day's year as 2569"
    )


# ── closed_date_fallback ─────────────────────────────────────────────────────────────────────

# The server's clock and label are used only WITH the server's date; with no readable date the
# whole closed line comes from `MarketStatus.lastRegularClosed` (its own bell and label).
_CLOSED_FALLBACKS = (
    (_OVERVIEW, "var resolvedMarketStatus: MarketStatus",
     ("guard let dateStr = date, let serverClose = fmt.date(from: dateStr) else {",
      "return MarketStatus.lastRegularClosed(before: Date())",
      "return .closed(date: serverClose, time: time ?? MarketStatus.closeClock(serverClose), "
      'timezone: timezone ?? "ET")')),
    (_OVERVIEW, "func toDisplayModel() -> TickerDetailData",
     ("guard let dateStr = marketStatus.date, let serverClose = fmt.date(from: dateStr) else {",
      "return MarketStatus.lastRegularClosed(before: Date())", "date: serverClose,",
      "time: marketStatus.time ?? MarketStatus.closeClock(serverClose),")),
    (_ETF, "func toDisplayModel() -> ETFDetailData",
     ('if let serverClose = ETFResponseFormatters.parseISO8601(marketStatus.date ?? "") {',
      "mktStatus = MarketStatus.lastRegularClosed(before: Date())", "date: serverClose,",
      "time: marketStatus.time ?? MarketStatus.closeClock(serverClose),")),
    (_INDEX, "func toDisplayModel() -> IndexDetailData",
     ('if let serverClose = IndexResponseFormatters.parseISO8601(marketStatus.date ?? "") {',
      "mktStatus = MarketStatus.lastRegularClosed(before: Date())", "date: serverClose,",
      "time: marketStatus.time ?? MarketStatus.closeClock(serverClose),")),
)


def _check_closed_date_fallback(s: dict[str, str]) -> None:
    for rel, header, statements in _CLOSED_FALLBACKS:
        body = _norm(_code(s, rel).block(header))
        for statement in statements:
            assert statement in body, (
                f"{rel} `{header}`: a closed status with no readable date must show the last real "
                f"close, not now — missing `{statement}`"
            )
        assert "fmt.date(from: dateStr) ?? Date()" not in body
        assert "lastRegularClose(before: Date())" not in body, (
            f"{rel} `{header}`: a bare close date beside the server's (or a fixed) clock again"
        )
        assert '"4:00 PM"' not in body, (
            f"{rel} `{header}`: a fixed 4:00 PM clock again — a 1:00 PM half-day close says 4:00 PM"
        )


# ── axis_zone_label ──────────────────────────────────────────────────────────────────────────

def _check_axis_zone_label(s: dict[str, str]) -> None:
    axis = _code(s, _AXIS)
    session = axis.block("private var sessionLabels: some View {")
    assert 'if let last = shown.last { shown[shown.count - 1] = last + " ET" }' in session, (
        "the 1D axis prints New York times without naming the zone — a Denver reader takes 9:30 AM "
        "for their own clock"
    )
    assert "ForEach(Array(shown.enumerated()), id: \\.offset)" in session, "the axis draws the unlabelled list"
    # Each 1D label sits at its own time (the labels are evenly spaced in time, as the bars are
    # placed), clamped inside the axis — never spread by text width, where the longer last label
    # pulled the middle ones early.
    placed = _norm(session)
    assert ("let fraction: CGFloat = shown.count > 1 ? CGFloat(index) / CGFloat(shown.count - 1) : 0"
            in placed), "the 1D labels are no longer placed at their own time"
    assert ("-min(max(geometry.size.width * fraction - d.width / 2, 0), max(geometry.size.width - d.width, 0))"
            in placed), "the 1D labels are no longer centred on their time and clamped inside the axis"
    assert "Spacer()" not in session and "HStack" not in session, (
        "the 1D labels are spread by text width again: the longer \"4:00 PM ET\" moves the middle ticks"
    )
    bars = axis.block("private func barLabels(width: CGFloat) -> [Label] {")
    assert ("if format === ChartDateFormatters.timeOnly || format === ChartDateFormatters.weekdayTime,"
            in _norm(bars)), "the intraday bar axis no longer names its zone"
    assert 'out[out.count - 1] = Label(index: last.index, x: last.x, text: last.text + " ET")' in bars


# ── commodity_close_date ─────────────────────────────────────────────────────────────────────

def _check_commodity_close_date(s: dict[str, str]) -> None:
    display = _code(s, _COMMODITY).block("var displayText: String {")
    assert "let day = MarketStatus.closeDay(date)" in display, (
        "the commodity closed line prints its date in the phone's zone again"
    )
    assert "DateFormatter" not in display, "the commodity closed line builds its own formatter again"
    day = _code(s, _MODELS).block("static func closeDay(_ date: Date) -> String")
    assert "TickerDetailFormatters.marketCloseDateFormatter.string(from: date)" in day


# ── news_naive_stamp_new_york ────────────────────────────────────────────────────────────────

_NAIVE_PATTERN = "yyyy-MM-dd HH:mm:ss"
# `marketTimeZone` is `ChartDateFormatters`' New York (pinned by chart_market_time).
_NEW_YORK_ZONES = ('TimeZone(identifier: "America/New_York")', "marketTimeZone")

# The news parsers whose bare day `TickerNewsArticle.timeAgo` prints in the PHONE's zone: each
# must parse it in that zone too, or it prints the day before west of the parse zone.
# (file, the parser's header, the formatter that reads "yyyy-MM-dd")
_NEWS_BARE_DAY = (
    (_VM, "private func parseDate(_ dateString: String) -> Date? {", "dateFormatter"),
    (_ETF_VM, "private func parseNewsDate(_ dateString: String) -> Date? {", "f"),
    (_CRYPTO_VM, "private func parseDate(_ dateString: String) -> Date? {", "dateFormatter"),
    (_COMMODITY_VM, "private func parseDate(_ dateString: String) -> Date? {", "dateOnly"),
)


def _enclosing_block(swift: _Swift, pos: int) -> tuple[int, int]:
    """(start, end) of the innermost brace block holding offset `pos`, braced on the skeleton."""
    depth = 0
    for k in range(pos - 1, -1, -1):
        ch = swift.skeleton[k]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return swift._match(k, "{", "}")
            depth -= 1
    raise AssertionError(f"offset {pos} is in no brace block")


def _naive_stamp_sites(s: dict[str, str]) -> list[tuple[str, int, str, str]]:
    """(file, line, formatter, the zone in force at its parse) for EVERY formatter in the app that
    parses FMP's zone-less pattern, outside #Preview blocks. The zone in force is the last one the
    formatter is given before its first `.date(from:` after the pattern, or anywhere in its block
    when the parse happens elsewhere (a `static let` formatter, a list of formatters)."""
    sites = []
    for rel, src in sorted(s.items()):
        if _NAIVE_PATTERN not in src:
            continue
        swift = _lexed(src)
        previews = swift.preview_spans()
        for start, _end, text in swift.literals:
            if text != _NAIVE_PATTERN or any(a <= start < b for a, b in previews):
                continue
            line = src.count("\n", 0, start) + 1
            m = re.search(r"(\w+)\.dateFormat = $", swift.code[max(0, start - 80): start])
            assert m, f'{rel}:{line}: "{_NAIVE_PATTERN}" is set some other way — this sweep cannot judge it'
            var = m.group(1)
            b_start, b_end = _enclosing_block(swift, start)
            block = swift.code[b_start:b_end]
            parse = block.find(f"{var}.date(from:", start - b_start)
            scope = block[: parse if parse != -1 else len(block)]
            zones = re.findall(rf"\b{re.escape(var)}\.timeZone = ([^;\n]+)", scope)
            sites.append((rel, line, var, zones[-1].strip() if zones else "the device zone"))
    return sites


def _check_news_naive_stamp_new_york(s: dict[str, str]) -> None:
    sites = _naive_stamp_sites(s)
    assert len(sites) >= 9, f"anti-vacuity: the sweep found only {len(sites)} zone-less parsers: {sites}"
    wrong = [f"{rel}:{line} `{var}` reads it in {zone}" for rel, line, var, zone in sites
             if zone not in _NEW_YORK_ZONES]
    assert not wrong, (
        "FMP's zone-less stamp is New York wall-clock time (market_hours.to_utc_instant, verified "
        "live): read in another zone a story is 4-5 hours off, and one shared row shows two times "
        "on two screens — " + "; ".join(wrong)
    )
    for rel, header, var in _NEWS_BARE_DAY:
        body = _norm(_code(s, rel).block(header))
        assert f'{var}.timeZone = TimeZone.current {var}.dateFormat = "yyyy-MM-dd"' in body, (
            f"{rel} `{header}`: a bare news day must be parsed at the phone's midnight, the zone "
            "`TickerNewsArticle.timeAgo` prints it in — anywhere else it shows the day before"
        )
    timeago = _code(s, "Models/TickerNewsModels.swift").block("var timeAgo: String {")
    assert "formatter.timeZone" not in timeago, (
        "anti-vacuity: `timeAgo` no longer prints in the phone's zone — re-derive the bare-day zone"
    )


GUARDS: dict[str, Callable[[dict[str, str]], None]] = {
    "snapshot_day_round_trip": _check_snapshot_day_round_trip,
    "closed_date_fallback": _check_closed_date_fallback,
    "axis_zone_label": _check_axis_zone_label,
    "commodity_close_date": _check_commodity_close_date,
    "news_naive_stamp_new_york": _check_news_naive_stamp_new_york,
    "chart_market_time": _check_chart_market_time,
    "closed_badge_date": _check_closed_badge_date,
    "zone_literal_ratchet": _check_zone_literal_ratchet,
    "fallback_badge_rule": _check_fallback_badge_rule,
    "last_close_rule": _check_last_close_rule,
    "live_session_rule": _check_live_session_rule,
    "server_label_fallback": _check_server_label_fallback,
    "chat_card_as_of": _check_chat_card_as_of,
}


@pytest.mark.parametrize("guard", sorted(GUARDS))
def test_the_guard_holds_on_the_real_source(guard):
    GUARDS[guard](_sources())


# ── The closed badge agrees with the server ──────────────────────────────────────────────────

def _swift_table(s: dict[str, str], name: str) -> set[tuple[int, int, int]]:
    """The table as the COMPILER sees it: comments blanked first, so a day commented out of the
    Swift is out of the transliteration too (testing.md §3 rule 1)."""
    m = re.search(rf"private static let {name}: Set<String> = \[(.*?)\]", _code(s, _HOURS).code, re.S)
    assert m, f"MarketHoursUtil.{name} moved — this scan has drifted"
    days = {(int(y), int(mo), int(d)) for y, mo, d in re.findall(r'"(\d{4})-(\d{2})-(\d{2})"', m.group(1))}
    assert days, f"MarketHoursUtil.{name} parsed empty — this scan has drifted"
    return days


def _swift_last_close(holidays: set, early_closes: set) -> Callable[[float], tuple[datetime, str, str]]:
    """`lastRegularClose(before:)` and `MarketStatus.lastRegularClosed(before:)`'s clock and label
    (the line `determineMarketStatus` returns), transliterated statement for statement
    (`last_close_rule` and `fallback_badge_rule` pin the Swift they mirror) with both tables READ
    from the Swift source."""

    def trading(d: date) -> bool:
        return d.weekday() < 5 and (d.year, d.month, d.day) not in holidays

    def bell(d: date) -> int:
        return 13 * 60 if (d.year, d.month, d.day) in early_closes else 16 * 60

    def badge(ts: float) -> tuple[datetime, str, str]:
        local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_EASTERN)
        today = local.date()
        day = today
        if not trading(today) or local.hour * 60 + local.minute < bell(today):
            probe = today
            for _ in range(10):
                probe -= timedelta(days=1)
                if trading(probe):
                    break
            day = probe
        minute = bell(day)
        close = datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=_EASTERN)
        # `closeClock`: "h:mm a" in New York, en_US_POSIX.
        clock = f"{close.hour % 12 or 12}:{close.minute:02d} {'AM' if close.hour < 12 else 'PM'}"
        return close, clock, ("EDT" if close.dst() else "EST")

    return badge


def _swift_live_session(holidays: set, early_closes: set) -> Callable[[float], str | None]:
    """`liveSessionStatus(at:)` over `isMarketActive(at:)`, transliterated (`live_session_rule`
    pins the Swift), as the wire status it stands for; None = no session, the closed branch."""

    def live(ts: float) -> str | None:
        local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_EASTERN)
        key = (local.year, local.month, local.day)
        minute = local.hour * 60 + local.minute
        if local.weekday() >= 5 or key in holidays:
            return None
        if key in early_closes and minute >= 13 * 60:
            return None
        if not 240 <= minute < 1200:
            return None
        if minute < 9 * 60 + 30:
            return "pre_market"
        return "open" if minute < 16 * 60 else "after_hours"

    return live


def test_the_swift_fallback_badge_agrees_with_the_backend(monkeypatch):
    """Every instant that matters, every day 2025-2028 — overnight, the 04:00 pre-market edge,
    the open, a half-day's 13:00 bell either side, the 16:00 bell either side, the 20:00 end of
    after-hours, the DST weeks and every holiday: `determineMarketStatus` names the same session
    as `market_status_fields`, and when closed the same close, clock and label."""
    from app.utils import market_hours

    # A closure another test LEARNED at run time is process-local backend state iOS cannot know.
    monkeypatch.setattr(market_hours, "_OBSERVED_CLOSURES", set())
    s = _sources()
    holidays, early_closes = _swift_table(s, "holidays"), _swift_table(s, "earlyCloses")
    live = _swift_live_session(holidays, early_closes)
    last_close = _swift_last_close(holidays, early_closes)
    times = [(0, 0), (3, 59), (4, 0), (9, 29), (9, 30), (12, 59), (13, 0), (13, 1), (15, 59), (16, 0),
             (16, 1), (19, 59), (20, 0), (23, 59)]
    closed, mismatches = 0, []
    day = date(2025, 1, 1)
    while day <= date(2028, 12, 31):
        for h, mi in times:
            when = datetime(day.year, day.month, day.day, h, mi, tzinfo=_EASTERN)
            server = market_hours.market_status_fields(when)
            session = live(when.timestamp())
            if session is not None or server["status"] != market_hours.WIRE_STATUS_CLOSED:
                if session != server["status"]:
                    mismatches.append((when.isoformat(), session, server))
                continue
            closed += 1
            close, clock, label = last_close(when.timestamp())
            want = datetime.fromisoformat(server["date"])
            if (close.timestamp(), clock, label) != (want.timestamp(), server["time"], server["timezone"]):
                mismatches.append((when.isoformat(), close.isoformat(), clock, label, server))
        day += timedelta(days=1)
    assert closed > 8000, f"anti-vacuity: only {closed} closed instants were compared"
    assert not mismatches, f"the Swift fallback badge disagrees with market_status_fields: {mismatches[:5]}"


def test_the_swift_tables_are_read_as_the_compiler_reads_them():
    """A day commented out of `earlyCloses` is out of the compiled app, so it is out of the
    transliteration too — else the agreement test above checks a table the app does not have
    (it read the raw source, comments and all)."""
    s = _sources()
    assert (2026, 11, 27) in _swift_table(s, "earlyCloses")
    assert s[_HOURS].count('"2026-11-27"') == 1, "anchor moved — re-derive this control"
    for commented in ('/* "2026-11-27" */', '"2026-11-26" // "2026-11-27"'):
        mutated = dict(s)
        mutated[_HOURS] = s[_HOURS].replace('"2026-11-27"', commented, 1)
        assert (2026, 11, 27) not in _swift_table(mutated, "earlyCloses"), commented


def test_the_named_closes():
    """The cases the old weekday-and-hour copy got wrong, by name."""
    s = _sources()
    swift = _swift_last_close(_swift_table(s, "holidays"), _swift_table(s, "earlyCloses"))

    def at(y, mo, d, h, mi):
        return datetime(y, mo, d, h, mi, tzinfo=_EASTERN).timestamp()

    def close(y, mo, d, h):
        return datetime(y, mo, d, h, 0, tzinfo=_EASTERN)

    # A Saturday names Friday's close, in EDT in October — never "Saturday, 4:00 PM EST".
    assert swift(at(2026, 10, 31, 11, 0)) == (close(2026, 10, 30, 16), "4:00 PM", "EDT")
    # The Monday after the clocks change: Friday's close keeps Friday's own label.
    assert swift(at(2026, 11, 2, 3, 0)) == (close(2026, 10, 30, 16), "4:00 PM", "EDT")
    # Thanksgiving names Wednesday; the half-day after it closes at 1:00 PM, in EST.
    assert swift(at(2026, 11, 26, 12, 0)) == (close(2026, 11, 25, 16), "4:00 PM", "EST")
    assert swift(at(2026, 11, 27, 14, 0)) == (close(2026, 11, 27, 13), "1:00 PM", "EST")
    # 2 AM is still the previous session's night.
    assert swift(at(2026, 10, 28, 2, 0)) == (close(2026, 10, 27, 16), "4:00 PM", "EDT")


# ── Mutations: every guard has been seen failing ─────────────────────────────────────────────

def _rm(token: str) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        assert src.count(token) >= 1, f"mutation anchor {token!r} not in source"
        return src.replace(token, "", 1)
    return mutate


def _sub(pattern: str, repl: str, flags: int = re.S) -> Callable[[str], str]:
    def mutate(src: str) -> str:
        out, n = re.subn(pattern, repl, src, count=1, flags=flags)
        assert n == 1, f"mutation pattern {pattern!r} did not match"
        return out
    return mutate


def _drop_zone_line(header: str) -> Callable[[str], str]:
    """Delete the `f.timeZone = marketTimeZone` line inside the declaration `header` opens."""
    return _sub(rf"({re.escape(header)}.*?)\n[ \t]*f\.timeZone = marketTimeZone\n", r"\1\n")


MUTATIONS: list[tuple[str, str, str, Callable[[str], str]]] = [
    ("intraday-parse-local", "chart_market_time", _MODELS,
     _drop_zone_line("static let inputDateTimeFormatter: DateFormatter = {")),
    ("daily-parse-local", "chart_market_time", _MODELS, _drop_zone_line("static let inputFormatter: DateFormatter = {")),
    ("labels-local", "chart_market_time", _MODELS,
     _drop_zone_line("private static func marketFormatter(_ pattern: String) -> DateFormatter {")),
    ("week-axis-local", "chart_market_time", _MODELS, _drop_zone_line("static let weekdayTime: DateFormatter = {")),
    ("time-axis-bare", "chart_market_time", _MODELS,
     _sub(r'static let timeOnly: DateFormatter = marketFormatter\("h:mm a"\)',
          'static let timeOnly: DateFormatter = {\n        let f = DateFormatter()\n'
          '        f.dateFormat = "h:mm a"\n        return f\n    }()')),
    ("zone-is-the-phone", "chart_market_time", _MODELS,
     _sub(r'TimeZone\(identifier: "America/New_York"\) \?\? \.current', "TimeZone.current")),
    ("crosshair-unlabelled", "chart_market_time", _MODELS, _rm(' + " ET"')),
    ("session-axis-phone-calendar", "chart_market_time", _COORDS,
     _sub(r"guard let labelDate = etCalendar\.date\(", "guard let labelDate = Calendar.current.date(")),
    ("session-axis-elapsed-minutes", "chart_market_time", _COORDS,
     _sub(r"guard let labelDate = etCalendar\.date\(\s*bySettingHour:.*?of: day\s*\)",
          "guard let labelDate = etCalendar.date(byAdding: .minute, value: minuteOfDay, to: day)")),
    ("session-axis-hour-24", "chart_market_time", _COORDS,
     _sub(r"\(minuteOfDay / 60\) % 24", "(minuteOfDay / 60)")),
    ("label-rules-phone-calendar", "chart_market_time", _AXIS,
     _sub(r"let calendar = ChartDateFormatters\.marketCalendar", "let calendar = Calendar.current")),
    ("badge-phone-date", "closed_badge_date", _BADGE,
     _sub(r"return MarketStatus\.closeLine\(date: date, time: time, timezone: timezone\)",
          'let formatter = DateFormatter()\n            formatter.dateFormat = "MMM d"\n'
          '            return "\\\\(formatter.string(from: date)), \\\\(time) \\\\(timezone)"')),
    ("close-date-phone-zone", "closed_badge_date", _MODELS,
     _sub(r'(static let marketCloseDateFormatter: DateFormatter = \{.*?)\n[ \t]*f\.timeZone = '
          r'TimeZone\(identifier: "America/New_York"\)\n', r"\1\n")),
    ("display-text-own-date", "closed_badge_date", _MODELS,
     _sub(r"Self\.closeLine\(date: date, time: time, timezone: timezone\)", "time) \\\\(timezone")),
    ("close-day-phone-formatter", "closed_badge_date", _MODELS,
     _sub(r"(static func closeDay\(_ date: Date\) -> String \{\s*)TickerDetailFormatters\.marketCloseDateFormatter",
          r"\1DateFormatter()")),
    ("snapshot-day-phone-zone", "snapshot_day_round_trip", _INDEX,
     _sub(r'(static func parseNewYorkDay.*?)\n[ \t]*df\.timeZone = TimeZone\(identifier: "America/New_York"\)\n',
          r"\1\n")),
    ("snapshot-day-shared-parser", "snapshot_day_round_trip", _INDEX,
     _sub(r"IndexResponseFormatters\.parseNewYorkDay\(snapshotsData\.generatedDate\)\s*\?\? ", "")),
    ("snapshot-print-phone-calendar", "snapshot_day_round_trip", _INDEX_SECTION,
     _rm("formatter.calendar = Calendar(identifier: .gregorian)")),
    ("closed-fallback-now-overview", "closed_date_fallback", _OVERVIEW,
     _sub(r"(var resolvedMarketStatus: MarketStatus.*?)return MarketStatus\.lastRegularClosed\(before: Date\(\)\)",
          r'\1return .closed(date: Date(), time: "4:00 PM", timezone: "ET")')),
    ("closed-fallback-now-overview-display", "closed_date_fallback", _OVERVIEW,
     _sub(r"(func toDisplayModel\(\) -> TickerDetailData.*?)return MarketStatus\.lastRegularClosed\(before: Date\(\)\)",
          r'\1return .closed(date: Date(), time: "4:00 PM", timezone: "ET")')),
    # The review's half-day case: a computed close date beside the server's (absent) clock.
    ("closed-fallback-server-clock-etf", "closed_date_fallback", _ETF,
     _sub(r"mktStatus = MarketStatus\.lastRegularClosed\(before: Date\(\)\)",
          'mktStatus = .closed(date: MarketHoursUtil.lastRegularClose(before: Date()), '
          'time: marketStatus.time ?? "4:00 PM", timezone: marketStatus.timezone ?? "ET")')),
    ("closed-fallback-now-index", "closed_date_fallback", _INDEX,
     _sub(r"mktStatus = MarketStatus\.lastRegularClosed\(before: Date\(\)\)",
          'mktStatus = .closed(date: Date(), time: "4:00 PM", timezone: "ET")')),
    ("closed-fallback-server-date-dropped", "closed_date_fallback", _INDEX,
     _sub(r'if let serverClose = IndexResponseFormatters\.parseISO8601\(marketStatus\.date \?\? ""\) \{',
          'if let serverClose = Optional(Date()) {')),
    ("missing-clock-fixed-four-overview", "closed_date_fallback", _OVERVIEW,
     _sub(r"time: time \?\? MarketStatus\.closeClock\(serverClose\)", 'time: time ?? "4:00 PM"')),
    ("missing-clock-fixed-four-etf", "closed_date_fallback", _ETF,
     _sub(r"time: marketStatus\.time \?\? MarketStatus\.closeClock\(serverClose\),",
          'time: marketStatus.time ?? "4:00 PM",')),
    ("axis-unlabelled", "axis_zone_label", _AXIS,
     _rm('if let last = shown.last { shown[shown.count - 1] = last + " ET" }')),
    ("axis-labels-all-in-one-place", "axis_zone_label", _AXIS,
     _sub(r"let fraction: CGFloat = shown\.count > 1 \? CGFloat\(index\) / CGFloat\(shown\.count - 1\) : 0",
          "let fraction: CGFloat = 0")),
    ("axis-labels-off-their-time", "axis_zone_label", _AXIS,
     _sub(r"(private var sessionLabels: some View \{.*?)geometry\.size\.width \* fraction - d\.width / 2",
          r"\1geometry.size.width * fraction")),
    ("axis-spread-by-text-width", "axis_zone_label", _AXIS,
     _sub(r"(private var sessionLabels: some View \{.*?)\.fixedSize\(\)", r"\1.fixedSize()\n Spacer()")),
    ("axis-draws-the-raw-list", "axis_zone_label", _AXIS,
     _sub(r"ForEach\(Array\(shown\.enumerated\(\)\)", "ForEach(Array(labels.enumerated())")),
    ("bar-axis-unlabelled", "axis_zone_label", _AXIS,
     _sub(r'out\[out\.count - 1\] = Label\(index: last\.index, x: last\.x, text: last\.text \+ " ET"\)',
          "out[out.count - 1] = last")),
    ("bar-axis-hour-only", "axis_zone_label", _AXIS,
     _sub(r"if format === ChartDateFormatters\.timeOnly \|\| format === ChartDateFormatters\.weekdayTime,",
          "if format === ChartDateFormatters.timeOnly,")),
    ("commodity-phone-date", "commodity_close_date", _COMMODITY,
     _sub(r"let day = MarketStatus\.closeDay\(date\)",
          'let day = { let f = DateFormatter(); f.dateFormat = "MMM d"; return f.string(from: date) }()')),
    ("news-naive-utc", "news_naive_stamp_new_york", _VM,
     _sub(r'(private func parseDate.*?)dateFormatter\.timeZone = TimeZone\(identifier: "America/New_York"\)',
          r'\1dateFormatter.timeZone = TimeZone(identifier: "UTC")')),
    ("news-bare-day-utc", "news_naive_stamp_new_york", _VM,
     _sub(r"(private func parseDate.*?)dateFormatter\.timeZone = TimeZone\.current",
          r'\1dateFormatter.timeZone = TimeZone(identifier: "UTC")')),
    ("news-pattern-set-another-way", "news_naive_stamp_new_york", _VM,
     _sub(r'dateFormatter\.dateFormat = "yyyy-MM-dd HH:mm:ss"',
          'dateFormatter.setLocalizedDateFormatFromTemplate("yyyy-MM-dd HH:mm:ss")')),
    ("updates-fmp-utc", "news_naive_stamp_new_york", _UPDATES,
     _sub(r'(private static let fmpSpaced.*?)f\.timeZone = TimeZone\(identifier: "America/New_York"\)',
          r'\1f.timeZone = TimeZone(identifier: "UTC")')),
    ("updates-fmp-device-zone", "news_naive_stamp_new_york", _UPDATES,
     _sub(r'(private static let fmpSpaced.*?)\n[ \t]*f\.timeZone = TimeZone\(identifier: "America/New_York"\)\n',
          r"\1\n")),
    ("etf-news-utc", "news_naive_stamp_new_york", _ETF_VM,
     _sub(r'(private func parseNewsDate.*?)f\.timeZone = TimeZone\(identifier: "America/New_York"\)',
          r'\1f.timeZone = TimeZone(identifier: "UTC")')),
    ("etf-news-zone-after-parse", "news_naive_stamp_new_york", _ETF_VM,
     _sub(r'(private func parseNewsDate.*?)(f\.timeZone = TimeZone\(identifier: "America/New_York"\)\n)'
          r'(\s*f\.dateFormat = "yyyy-MM-dd HH:mm:ss"\n\s*if let d = f\.date\(from: dateString\) \{ return d \}\n)',
          r"\1\3        \2")),
    ("etf-response-utc", "news_naive_stamp_new_york", _ETF,
     _sub(r'dt\.timeZone = TimeZone\(identifier: "America/New_York"\)', 'dt.timeZone = TimeZone(identifier: "UTC")')),
    ("index-news-utc", "news_naive_stamp_new_york", _INDEX_VM,
     _sub(r'fallback\.timeZone = TimeZone\(identifier: "America/New_York"\)',
          'fallback.timeZone = TimeZone(identifier: "UTC")')),
    ("crypto-news-utc", "news_naive_stamp_new_york", _CRYPTO_VM,
     _sub(r'(private func parseDate.*?)dateFormatter\.timeZone = TimeZone\(identifier: "America/New_York"\)',
          r'\1dateFormatter.timeZone = TimeZone(identifier: "UTC")')),
    ("commodity-news-utc", "news_naive_stamp_new_york", _COMMODITY_VM,
     _sub(r'simple\.timeZone = TimeZone\(identifier: "America/New_York"\)', 'simple.timeZone = TimeZone(identifier: "UTC")')),
    ("etf-bare-day-utc", "news_naive_stamp_new_york", _ETF_VM,
     _sub(r"(private func parseNewsDate.*?)f\.timeZone = TimeZone\.current",
          r'\1f.timeZone = TimeZone(identifier: "UTC")')),
    ("crypto-bare-day-utc", "news_naive_stamp_new_york", _CRYPTO_VM,
     _sub(r"(private func parseDate.*?)dateFormatter\.timeZone = TimeZone\.current",
          r'\1dateFormatter.timeZone = TimeZone(identifier: "UTC")')),
    ("commodity-bare-day-utc", "news_naive_stamp_new_york", _COMMODITY_VM,
     _sub(r"dateOnly\.timeZone = TimeZone\.current", 'dateOnly.timeZone = TimeZone(identifier: "UTC")')),
    ("etf-says-est-again", "zone_literal_ratchet", _ETF,
     _sub(r'marketStatus\.timezone \?\? "ET"', 'marketStatus.timezone ?? "EST"')),
    ("fallback-says-edt-always", "zone_literal_ratchet", _MODELS,
     _sub(r'newYork\.isDaylightSavingTime\(for: close\) \? "EDT" : "EST"', '"EDT"')),
    ("dst-label-phone-zone", "zone_literal_ratchet", _MODELS,
     _sub(r"newYork\.isDaylightSavingTime\(for: close\)", "TimeZone.current.isDaylightSavingTime(for: close)")),
    ("dst-label-now", "zone_literal_ratchet", _MODELS,
     _sub(r"newYork\.isDaylightSavingTime\(for: close\)", "newYork.isDaylightSavingTime(for: Date())")),
    ("dst-label-dot-now", "zone_literal_ratchet", _MODELS,
     _sub(r"newYork\.isDaylightSavingTime\(for: close\)", "newYork.isDaylightSavingTime(for: .now)")),
    ("dst-label-date-now", "zone_literal_ratchet", _MODELS,
     _sub(r"newYork\.isDaylightSavingTime\(for: close\)", "newYork.isDaylightSavingTime(for: Date.now)")),
    ("dst-label-fake-new-york", "zone_literal_ratchet", _MODELS,
     _sub(r'let newYork: TimeZone = TimeZone\(identifier: "America/New_York"\)!',
          "let newYork: TimeZone = TimeZone.current")),
    ("widget-naive-stamp-utc", "news_naive_stamp_new_york", _WIDGET,
     _sub(r"\Z", '\nfunc caydexMutationParse(_ s: String) -> Date? {\n    let f = DateFormatter()\n'
                 '    f.timeZone = TimeZone(identifier: "UTC")\n    f.dateFormat = "yyyy-MM-dd HH:mm:ss"\n'
                 "    return f.date(from: s)\n}\n")),
    ("widget-hard-coded-est", "zone_literal_ratchet", _WIDGET,
     _sub(r"\Z", '\nlet caydexMutationCloseLine = "Close 4:00 PM EST"\n')),
    ("shared-hard-coded-edt", "zone_literal_ratchet", _WIDGET_STORE,
     _sub(r"\Z", '\nlet caydexMutationAsOf = "As of 4:00 PM EDT"\n')),
    ("est-inside-a-sentence", "zone_literal_ratchet", _BADGE,
     _sub(r'return "Market Closed"', 'return "Market Closed 4:00 PM EST"')),
    ("weekday-copy-back", "fallback_badge_rule", _VM,
     _sub(r"(private func determineMarketStatus\(\) -> MarketStatus \{\n)",
          r"\1        if Calendar.current.component(.weekday, from: Date()) == 7 {"
          r' return .closed(date: Date(), time: "4:00 PM", timezone: "ET") }\n')),
    ("stamps-now", "fallback_badge_rule", _MODELS,
     _sub(r"(static func lastRegularClosed\(before instant: Date\) -> MarketStatus \{.*?)date: close,",
          r"\1date: instant,")),
    ("half-day-at-four", "fallback_badge_rule", _MODELS,
     _sub(r"time: closeClock\(close\),", 'time: "4:00 PM",')),
    ("close-clock-phone-zone", "fallback_badge_rule", _MODELS,
     _sub(r'(static let marketCloseClockFormatter: DateFormatter = \{.*?)\n[ \t]*f\.timeZone = '
          r'TimeZone\(identifier: "America/New_York"\)\n', r"\1\n")),
    ("close-clock-phone-locale", "fallback_badge_rule", _MODELS,
     _sub(r'(static let marketCloseClockFormatter: DateFormatter = \{.*?)\n[ \t]*f\.locale = '
          r'Locale\(identifier: "en_US_POSIX"\)\n', r"\1\n")),
    ("close-clock-24-hour", "fallback_badge_rule", _MODELS,
     _sub(r'(static let marketCloseClockFormatter: DateFormatter = \{.*?)f\.dateFormat = "h:mm a"',
          r'\1f.dateFormat = "HH:mm"')),
    ("fallback-builds-own-line", "fallback_badge_rule", _VM,
     _sub(r"return MarketStatus\.lastRegularClosed\(before: now\)",
          'return .closed(date: MarketHoursUtil.lastRegularClose(before: now), time: "4:00 PM", timezone: "ET")')),
    ("own-session-rule", "fallback_badge_rule", _VM,
     _sub(r"MarketHoursUtil\.liveSessionStatus\(at: now\)", "Optional<MarketStatus>.none")),
    ("half-day-ignored", "last_close_rule", _HOURS,
     _sub(r"earlyCloses\.contains\(key\) \? 13 \* 60 : 16 \* 60", "16 * 60")),
    ("close-is-always-today", "last_close_rule", _HOURS,
     _sub(r"\{\s*day = previousTradingDay\(before: today, calendar: calendar\)\s*\}", "{ day = today }")),
    ("bell-minute-counts-as-open", "last_close_rule", _HOURS,
     _sub(r"minuteOfDay < closeMinuteOfDay\(today", "minuteOfDay <= closeMinuteOfDay(today")),
    ("holidays-ignored", "last_close_rule", _HOURS, _sub(r"return !holidays\.contains\(key\)", "return true")),
    ("after-hours-to-21", "live_session_rule", _HOURS,
     _sub(r"return \(240\.\.<1200\)\.contains\(minuteOfDay\)", "return (240..<1260).contains(minuteOfDay)")),
    ("half-day-afternoon-open", "live_session_rule", _HOURS,
     _sub(r"if earlyCloses\.contains\(key\) && minuteOfDay >= 13 \* 60 \{ return false \}\n", "")),
    ("open-at-nine", "live_session_rule", _HOURS,
     _sub(r"if minuteOfDay < 9 \* 60 \+ 30 \{ return \.preMarket \}", "if minuteOfDay < 9 * 60 { return .preMarket }")),
    ("overview-fallback-blank", "server_label_fallback", _OVERVIEW,
     _sub(r'timezone: timezone \?\? "ET"\)', 'timezone: timezone ?? "")')),
    ("index-fallback-blank", "server_label_fallback", _INDEX,
     _sub(r'timezone: marketStatus\.timezone \?\? "ET"', "timezone: marketStatus.timezone ?? \"\"")),
    ("chat-instant-unlabelled", "chat_card_as_of", _CHAT_CARD,
     _sub(r'string\(from: messageDate\) \+ " ET"', "string(from: messageDate)")),
    ("chat-instant-phone-zone", "chat_card_as_of", _CHAT_CARD,
     _drop_zone_line("private static let asOfInstantFormatter: DateFormatter = {")),
]


@pytest.mark.parametrize("label,guard,rel,mutate", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_every_guard_kills_its_mutation(label, guard, rel, mutate):
    sources = _sources()
    mutated = dict(sources)
    mutated[rel] = mutate(sources[rel])
    assert mutated[rel] != sources[rel], f"mutation {label!r} changed nothing"
    with pytest.raises(AssertionError):
        GUARDS[guard](mutated)


def test_every_guard_has_a_mutation():
    """A guard with no mutation is one nobody has seen fail."""
    covered = {row[1] for row in MUTATIONS}
    assert covered == set(GUARDS), f"guards with no mutation: {sorted(set(GUARDS) - covered)}"


def test_the_lexer_strips_comments_and_keeps_literals():
    """The CONTROL for every scan above: comment text vanishes, literal text survives, offsets and
    lines are kept, and an interpolation's nested literals are seen."""
    src = (
        '// "EST" in a line comment\n'
        '/* a block "EDT" /* nested */ still "EST" */\n'
        'let url = "https://example.com" // trailing "EST"\n'
        'let a = "4:00 PM \\(flag ? "EDT" : "EST") ET"\n'
        'let raw = #"a "quoted" EST"#\n'
        'let multi = """\n    EDT\n    """\n'
        '#Preview("Dark") { Text("EST {") }\n'
        'let tail = "after"\n'
    )
    swift = _Swift(src)
    texts = [text for _, _, text in swift.literals]
    assert len(swift.code) == len(src) and swift.code.count("\n") == src.count("\n")
    first, second = swift.code.splitlines()[:2]
    assert not first.strip() and not second.strip(), "a comment survived the lexer"
    assert "https://example.com" in swift.code and '"EST"' not in swift.code.splitlines()[2]
    assert "https://example.com" in texts, "the lexer ate a URL literal as a comment"
    assert "4:00 PM \x00 ET" in texts and "EDT" in texts and "EST" in texts, texts
    assert 'a "quoted" EST' in texts, texts
    assert "\n    EDT\n    " in texts, texts
    assert texts[-1] == "after", "a brace inside a literal threw the lexer off"
    (start, end), = swift.preview_spans()
    assert 'Text("EST {")' in swift.code[start:end] and "tail" not in swift.code[start:end]
