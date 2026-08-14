#!/usr/bin/env bash
#
# sparkline-geometry-check.sh — assert the intraday chart X-mapping math.
#
# WHY A STANDALONE swiftc HARNESS
# -------------------------------
# There is NO XCTest target in this project (see .claude/rules/testing.md and the
# header of SparklineGeometry.swift, which says the math lives in a SwiftUI-free
# file precisely so it can be exercised this way). `xcodebuild build` proves the
# code compiles; it proves nothing about where a line is drawn.
#
# WHAT IT GUARDS
# --------------
# A sparkline ships as a bare [Double] with no timestamps. The x mapping used to
# be `index / (count - 1) * width`, so N points ALWAYS filled the tile: at 10:15 a
# 34-bar morning was pixel-identical to a completed 78-bar session — the card read
# "the day is done" beside a live price, contradicting the detail 1D chart. The
# fix positions the series between `width * from` and `width * to`.
#
# The load-bearing property is the FALLBACK: any unusable span must widen back to
# the full width, never shrink a chart that used to render fine.
#
# It compiles the REAL source files (not copies), so a signature change here
# fails the harness rather than letting it drift silently.
#
# Usage:  ./frontend/ios/scripts/sparkline-geometry-check.sh
# Exit 0 = all assertions hold.

set -euo pipefail

# scripts/ → ios/ → frontend/ → repo root
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
IOS="$ROOT/frontend/ios/ios"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- Stubs for the app types the chart math touches ---------------------------
# Deliberately minimal. They mirror the real declarations in
# Core/Repositories/StockRepository.swift, Models/ChartModels.swift and
# Models/TickerDetailModels.swift; if those change shape, this file stops
# compiling, which is the intended alarm.
cat > "$WORK/Stubs.swift" <<'SWIFT'
import Foundation

struct StockPricePoint {
    let date: String
    let close: Double
    let open: Double? = nil
    let high: Double? = nil
    let low: Double? = nil
    let volume: Double? = nil
    var isExtendedHours: Bool { false }
}

enum ChartAssetContext { case stock, etf, crypto, index, commodity }

enum ChartDateFormatters {
    static let inputDateTimeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "America/New_York")
        return f
    }()
    static func parseDate(_ s: String) -> Date? { inputDateTimeFormatter.date(from: s) }
}
SWIFT

# --- The math under test, extracted verbatim from the real files --------------
# `SparklineGeometry.swift` is SwiftUI-free and compiles as-is. `TradingDayHelper`
# lives inside ChartCoordinateSystem.swift, which imports SwiftUI and pulls in the
# whole app; `sed` lifts just the enum so the harness stays hermetic. The extract
# is line-exact — an edit inside it changes what is tested here.
sed -n '/^enum TradingDayHelper {/,/^}$/p' \
    "$IOS/Views/Molecules/Chart/ChartCoordinateSystem.swift" \
    > "$WORK/TradingDayHelper.swift"
sed -i '' '1i\
import CoreGraphics\
import Foundation\
' "$WORK/TradingDayHelper.swift"

if ! grep -q "static func window(for context: ChartAssetContext)" "$WORK/TradingDayHelper.swift"; then
    echo "FAIL: could not extract TradingDayHelper — did ChartCoordinateSystem.swift move?" >&2
    exit 1
fi

cat > "$WORK/main.swift" <<'SWIFT'
import CoreGraphics
import Foundation

var failures = 0
func check(_ cond: Bool, _ what: String) {
    if cond { print("  ok   \(what)") }
    else { print("  FAIL \(what)"); failures += 1 }
}
func near(_ a: CGFloat, _ b: CGFloat, _ tol: CGFloat = 0.001) -> Bool { abs(a - b) <= tol }

let size = CGSize(width: 100, height: 40)
let series = [10.0, 11.0, 12.0, 13.0, 14.0]

print("SparklineGeometry.normalizedPoints")

// The pre-span behaviour, which every fallback must reproduce exactly.
let full = SparklineGeometry.normalizedPoints(series, in: size)
check(near(full.first!.x, 0) && near(full.last!.x, 100), "default span fills the full width")

// The reported bug: a partial session must stop partway across.
let partial = SparklineGeometry.normalizedPoints(series, in: size, spanFrom: 0, spanTo: 0.43)
check(near(partial.first!.x, 0), "partial span still starts at the left edge")
check(near(partial.last!.x, 43), "partial span ends at width * to, not at width")
check(partial.count == series.count, "no points are dropped by a partial span")
// Even spacing inside the drawn slice — 5-min bars are uniform, so this is exact.
check(near(partial[1].x - partial[0].x, partial[2].x - partial[1].x), "points stay evenly spaced")

// An illiquid name whose first print is late starts inside the card, not at 0.
let shifted = SparklineGeometry.normalizedPoints(series, in: size, spanFrom: 0.2, spanTo: 0.6)
check(near(shifted.first!.x, 20) && near(shifted.last!.x, 60), "a shifted span offsets both ends")

// y-mapping must be untouched by any of this.
check(near(full.first!.y, size.height) && near(full.last!.y, 0), "y mapping is unchanged")
check(SparklineGeometry.normalizedPoints([5.0, 5.0], in: size)[0].y == size.height / 2,
      "a flat series is still centred vertically")

print("SparklineGeometry.clampedSpan — the fallback is the point")
for (from, to, label) in [
    (Double.nan, 0.5, "NaN from"),
    (0.0, Double.infinity, "infinite to"),
    (-0.2, 0.5, "negative from"),
    (0.0, 1.5, "to beyond 1"),
    (0.6, 0.4, "inverted pair"),
    (0.5, 0.5, "zero-width"),
] {
    let s = SparklineGeometry.clampedSpan(from: from, to: to)
    check(s.from == 0 && s.to == 1, "\(label) widens back to the full span")
    let pts = SparklineGeometry.normalizedPoints(series, in: size, spanFrom: from, spanTo: to)
    check(near(pts.last!.x, 100), "\(label) draws full width (never zero-width)")
}
let good = SparklineGeometry.clampedSpan(from: 0.1, to: 0.9)
check(near(good.from, 0.1) && near(good.to, 0.9), "a valid span passes through untouched")

// Guards that predate this change must still hold.
check(SparklineGeometry.normalizedPoints([1.0], in: size, spanTo: 0.4).isEmpty, "1 point → nothing")
check(SparklineGeometry.normalizedPoints([1.0, Double.nan], in: size).isEmpty, "NaN value → nothing")
check(SparklineGeometry.normalizedPoints(series, in: .zero).isEmpty, "zero size → nothing")

print("TradingDayHelper — session windows")
check(TradingDayHelper.window(for: .stock) == .regular, "stock → 09:30-16:00")
check(TradingDayHelper.window(for: .etf) == .regular, "etf → 09:30-16:00")
check(TradingDayHelper.window(for: .index) == .regular, "index → 09:30-16:00")
check(TradingDayHelper.window(for: .crypto) == .roundTheClock, "crypto → 00:00-24:00")
check(TradingDayHelper.window(for: .commodity) == .roundTheClock, "commodity → 00:00-24:00")
check(TradingDayHelper.SessionWindow.regular.length == 390, "regular session is 390 minutes")

func pts(_ times: [String]) -> [StockPricePoint] {
    times.map { StockPricePoint(date: "2026-08-13 \($0):00", close: 100) }
}

let equity = TradingDayHelper.timeFractions(for: pts(["09:30", "12:15", "15:55"]))
check(near(equity[0], 0), "09:30 is the equity session start")
check(near(equity[1], CGFloat(165.0 / 390.0)), "12:15 is ~42% through the equity session")
check(near(equity[2], CGFloat(385.0 / 390.0)), "15:55 is the last equity bar")

// The commodity bug this change fixes: on the equity window every overnight bar
// clamps to 0 and stacks on the left edge.
let overnight = pts(["00:00", "03:00", "06:00", "09:00"])
let onEquity = TradingDayHelper.timeFractions(for: overnight)
check(onEquity.allSatisfy { $0 == 0 }, "overnight bars all clamp to 0 on the equity window")
let onFullDay = TradingDayHelper.timeFractions(for: overnight, window: .roundTheClock)
check(Set(onFullDay).count == overnight.count, "the 24h window keeps them distinct")
check(near(onFullDay[1], CGFloat(180.0 / 1440.0)), "03:00 is 12.5% through a 24h day")

// Same instant, different asset class, different fraction — the whole point of
// choosing a window rather than opting crypto out of time mapping entirely.
let noon = pts(["00:00", "12:15"])
let cryptoNoon = TradingDayHelper.timeFractions(for: noon, window: .roundTheClock).last!
let stockNoon = TradingDayHelper.timeFractions(for: pts(["09:30", "12:15"])).last!
check(cryptoNoon != stockNoon, "a 24/7 asset and an equity read differently at the same time")

check(TradingDayHelper.timeFractions(for: pts(["12:15"]), window:
        TradingDayHelper.SessionWindow(openMinute: 600, closeMinute: 600)) == [0],
      "a zero-length window degrades to 0 instead of producing NaN")
check(TradingDayHelper.timeFractions(
        for: [StockPricePoint(date: "not-a-date", close: 1)]) == [0],
      "an unparseable date degrades to 0")

let dayLabels = TradingDayHelper.sessionTimeLabels(count: 4, window: .roundTheClock)
let bellLabels = TradingDayHelper.sessionTimeLabels(count: 4, window: .regular)
check(dayLabels.count == 4 && bellLabels.count == 4, "both axes emit the requested label count")
// The load-bearing property: the window actually reaches the label builder, so a
// 24/7 chart is not captioned with the equity bell.
check(dayLabels != bellLabels, "the 24h axis is captioned differently from the equity axis")
// A midnight-to-midnight axis legitimately starts and ends at the same clock
// time; the interior labels are what must span the day.
check(Set(dayLabels).count == 3, "24h axis wraps: first and last labels coincide, interior spans the day")
check(Set(bellLabels).count == 4, "equity axis labels are all distinct")
check(TradingDayHelper.sessionTimeLabels(count: 1).isEmpty, "a single label is meaningless → []")

print(failures == 0 ? "\nAll sparkline-geometry assertions hold." : "\n\(failures) FAILED")
exit(failures == 0 ? 0 : 1)
SWIFT

swiftc -O \
    "$IOS/Core/Utilities/SparklineGeometry.swift" \
    "$WORK/TradingDayHelper.swift" \
    "$WORK/Stubs.swift" \
    "$WORK/main.swift" \
    -o "$WORK/harness"

"$WORK/harness"
