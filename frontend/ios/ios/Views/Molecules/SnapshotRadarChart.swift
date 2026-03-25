//
//  SnapshotRadarChart.swift
//  ios
//
//  Molecule: Pentagon radar chart for Snapshot quant ratings.
//  Displays 5 snapshot categories as a filled polygon with grid rings.
//

import SwiftUI

struct SnapshotRadarChart: View {
    let snapshots: [SnapshotItem]

    private let chartSize: CGFloat = 200
    private let rings = 5
    private let startAngle: CGFloat = -.pi / 2

    /// Ordered categories for the pentagon axes (clockwise from top)
    private static let orderedCategories: [SnapshotCategory] = [
        .profitability,      // top
        .growth,             // upper-right
        .insidersOwnership,  // lower-right
        .financialHealth,    // lower-left
        .price               // upper-left
    ]

    /// Pre-built axis data from snapshots
    private var axes: [SnapshotAxisData] {
        Self.orderedCategories.compactMap { cat in
            guard let item = snapshots.first(where: { $0.category == cat }) else { return nil }
            return SnapshotAxisData(
                category: cat,
                rating: item.rating,
                normalized: Double(item.rating.rawValue) / 5.0
            )
        }
    }

    var body: some View {
        let axisData = axes
        let sides = axisData.count
        let values = axisData.map(\.normalized)
        let frameSize = chartSize + 100

        ZStack {
            radarGrid(sides: sides, frameSize: frameSize)
            dataPolygon(values: values, frameSize: frameSize)
            scoreDots(axisData: axisData, frameSize: frameSize)
            axisLabels(axisData: axisData, frameSize: frameSize)
        }
        .frame(width: frameSize, height: frameSize)
        .frame(maxWidth: .infinity)
    }

    // MARK: - Grid

    private func radarGrid(sides: Int, frameSize: CGFloat) -> some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let radius = chartSize / 2
            guard sides > 0 else { return }

            for ring in 1...rings {
                let r = radius * CGFloat(ring) / CGFloat(rings)
                let path = polygonPath(center: center, radius: r, sides: sides)
                context.stroke(path, with: .color(AppColors.textMuted.opacity(0.12)), lineWidth: 0.5)
            }

            for i in 0..<sides {
                let angle = angleForIndex(i, total: sides)
                let endPoint = CGPoint(
                    x: center.x + radius * cos(angle),
                    y: center.y + radius * sin(angle)
                )
                var spoke = Path()
                spoke.move(to: center)
                spoke.addLine(to: endPoint)
                context.stroke(spoke, with: .color(AppColors.textMuted.opacity(0.12)), lineWidth: 0.5)
            }
        }
        .frame(width: frameSize, height: frameSize)
    }

    // MARK: - Data Polygon

    private func dataPolygon(values: [Double], frameSize: CGFloat) -> some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let radius = chartSize / 2
            guard !values.isEmpty else { return }

            let path = dataPolygonPath(center: center, radius: radius, values: values)
            context.fill(path, with: .color(AppColors.primaryBlue.opacity(0.2)))
            context.stroke(path, with: .color(AppColors.primaryBlue), style: StrokeStyle(lineWidth: 2))
        }
        .frame(width: frameSize, height: frameSize)
    }

    // MARK: - Score Dots

    private func scoreDots(axisData: [SnapshotAxisData], frameSize: CGFloat) -> some View {
        let radius = chartSize / 2
        let total = axisData.count

        return ForEach(Array(axisData.enumerated()), id: \.element.id) { index, item in
            let angle = angleForIndex(index, total: total)
            let r = radius * CGFloat(item.normalized)

            Circle()
                .fill(AppColors.primaryBlue)
                .frame(width: 6, height: 6)
                .shadow(color: AppColors.primaryBlue.opacity(0.6), radius: 4)
                .offset(
                    x: r * cos(angle),
                    y: r * sin(angle)
                )
        }
    }

    // MARK: - Axis Labels

    private func axisLabels(axisData: [SnapshotAxisData], frameSize: CGFloat) -> some View {
        let labelRadius = chartSize / 2 + 36
        let total = axisData.count

        return ForEach(Array(axisData.enumerated()), id: \.element.id) { index, item in
            let angle = angleForIndex(index, total: total)

            VStack(spacing: 1) {
                Text(item.category.rawValue)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
                Text(item.rating.displayName)
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(item.rating.color)
            }
            .frame(width: 80)
            .offset(
                x: labelRadius * cos(angle),
                y: labelRadius * sin(angle)
            )
        }
    }

    // MARK: - Geometry Helpers

    private func angleForIndex(_ index: Int, total: Int) -> CGFloat {
        startAngle + CGFloat(index) * (2 * .pi / CGFloat(total))
    }

    private func polygonPath(center: CGPoint, radius: CGFloat, sides: Int) -> Path {
        var path = Path()
        for i in 0...sides {
            let angle = angleForIndex(i % sides, total: sides)
            let point = CGPoint(
                x: center.x + radius * cos(angle),
                y: center.y + radius * sin(angle)
            )
            if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        path.closeSubpath()
        return path
    }

    private func dataPolygonPath(center: CGPoint, radius: CGFloat, values: [Double]) -> Path {
        var path = Path()
        for i in 0...values.count {
            let idx = i % values.count
            let angle = angleForIndex(idx, total: values.count)
            let r = radius * min(max(CGFloat(values[idx]), 0), 1.0)
            let point = CGPoint(
                x: center.x + r * cos(angle),
                y: center.y + r * sin(angle)
            )
            if i == 0 { path.move(to: point) } else { path.addLine(to: point) }
        }
        path.closeSubpath()
        return path
    }
}

// MARK: - Axis Data Model

private struct SnapshotAxisData: Identifiable {
    let category: SnapshotCategory
    let rating: SnapshotRatingLevel
    let normalized: Double

    var id: String { category.rawValue }
}

#Preview {
    SnapshotRadarChart(snapshots: SnapshotItem.sampleData)
        .padding()
        .background(AppColors.cardBackground)
        .preferredColorScheme(.dark)
}
