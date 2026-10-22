//
//  LineChartRenderer.swift
//  ios
//
//  Line chart with the current-price dot
//

import SwiftUI

/// ⚠️ DELIBERATELY has no Differentiate Without Color cue, and this is the reasoning
/// rather than an oversight — `test_ios_a11y_parity.py` exempts it by name.
///
/// The obvious cue for a line is a dash, and DASH IS ALREADY TAKEN here: the extended-hours
/// segments are drawn `dash: [4, 2]`. A second meaning on the same channel makes both
/// unreadable, which is worse than the problem being solved. Unlike
/// `CandlestickChartRenderer` — where extended hours could move onto opacity because it
/// was already half-encoded there — this renderer has no free channel: opacity is also
/// taken (0.35 on the same line), and width carries the same distinction.
///
/// It is acceptable because this is the DETAIL chart, and the direction it encodes is
/// stated in text directly above it: the price header renders a signed change and an
/// arrow. The chart is not the only carrier of the claim.
struct LineChartRenderer: View {
    let series: ChartSeries
    let frame: ChartFrame
    let yRange: ChartYRange
    let lineColor: Color
    /// Mute + dash the pre/after-hours segments (1D with extended bars only).
    var showExtendedHours: Bool = false

    var body: some View {
        Canvas { context, size in
            let range = frame.drawRange
            guard !range.isEmpty, range.upperBound <= series.count else { return }
            let coord = ChartCoordinateSystem(width: size.width, height: size.height, yRange: yRange, frame: frame)

            if showExtendedHours {
                let (regular, extended) = ChartSegments.split(range: range, series: series, coord: coord)
                context.stroke(regular, with: .color(lineColor), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                context.stroke(extended, with: .color(lineColor.opacity(0.35)),
                               style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round, dash: [4, 2]))
            } else {
                let path = ChartPath.line(range: range, coord: coord) { series.close[$0] }
                context.stroke(path, with: .color(lineColor), style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
            }

            // Current price dot — only when the NEWEST bar is in view. After panning back
            // into history the last drawn bar is a historical close, not the current price.
            if range.upperBound == series.count, let last = series.close.last {
                let x = coord.xPosition(for: series.count - 1)
                let y = coord.yPosition(for: last)
                context.fill(Path(ellipseIn: CGRect(x: x - 4, y: y - 4, width: 8, height: 8)), with: .color(lineColor))
            }
        }
    }
}

/// Regular vs pre/after-hours line segments (1D only, where every bar is drawn). Each
/// segment repeats the boundary point of its neighbour so the line stays connected.
enum ChartSegments {
    static func split(range: Range<Int>, series: ChartSeries, coord: ChartCoordinateSystem) -> (regular: Path, extended: Path) {
        var regular = Path(), extended = Path()
        var regularOpen = false, extendedOpen = false
        func point(_ i: Int) -> CGPoint {
            CGPoint(x: coord.xPosition(for: i), y: coord.yPosition(for: series.close[i]))
        }
        for i in range {
            let isExt = series.isExtended[i]
            let p = point(i)
            if isExt {
                if !extendedOpen {
                    if i > range.lowerBound { extended.move(to: point(i - 1)); extended.addLine(to: p) } else { extended.move(to: p) }
                    extendedOpen = true
                } else {
                    extended.addLine(to: p)
                }
                if regularOpen { regular.addLine(to: p); regularOpen = false }
            } else {
                if !regularOpen {
                    if i > range.lowerBound { regular.move(to: point(i - 1)); regular.addLine(to: p) } else { regular.move(to: p) }
                    regularOpen = true
                } else {
                    regular.addLine(to: p)
                }
                if extendedOpen { extended.addLine(to: p); extendedOpen = false }
            }
        }
        return (regular, extended)
    }
}
