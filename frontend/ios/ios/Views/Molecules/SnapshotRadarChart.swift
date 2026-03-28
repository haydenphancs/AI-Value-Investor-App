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
    private let sides = 5
    private let startAngle: CGFloat = -.pi / 2

    /// Ordered categories for the pentagon axes (clockwise from top)
    private static let orderedCategories: [SnapshotCategory] = [
        .profitability,      // top
        .growth,             // upper-right
        .insidersOwnership,  // lower-right
        .financialHealth,    // lower-left
        .price               // upper-left
    ]

    /// All axis data (including unavailable) — used for grid and labels
    private var allAxes: [SnapshotAxisData] {
        Self.orderedCategories.map { cat in
            if let item = snapshots.first(where: { $0.category == cat }) {
                return SnapshotAxisData(
                    category: cat,
                    rating: item.rating,
                    normalized: Double(item.rating.rawValue) / 5.0
                )
            } else {
                return SnapshotAxisData(
                    category: cat,
                    rating: .unavailable,
                    normalized: 0
                )
            }
        }
    }

    var body: some View {
        let allAxisData = allAxes
        let frameSize = chartSize + 100
        ZStack {
            radarGrid(frameSize: frameSize)
            dataPolygon(allAxisData: allAxisData, frameSize: frameSize)
            scoreDots(allAxisData: allAxisData, frameSize: frameSize)
            axisLabels(axisData: allAxisData, frameSize: frameSize)
        }
        .frame(width: frameSize, height: frameSize)
        .frame(maxWidth: .infinity)
    }

    // MARK: - Grid

    private func radarGrid(frameSize: CGFloat) -> some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let radius = chartSize / 2

            // Alternating pale gray bands between rings
            for ring in stride(from: rings, through: 1, by: -1) {
                guard ring % 2 == 1 else { continue }
                let outerR = radius * CGFloat(ring) / CGFloat(rings)
                var bandPath = polygonPath(center: center, radius: outerR, sides: sides)
                if ring > 1 {
                    let innerR = radius * CGFloat(ring - 1) / CGFloat(rings)
                    bandPath.addPath(polygonPath(center: center, radius: innerR, sides: sides))
                }
                context.fill(bandPath, with: .color(AppColors.textSecondary.opacity(0.03)), style: FillStyle(eoFill: true))
            }

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

    private func dataPolygon(allAxisData: [SnapshotAxisData], frameSize: CGFloat) -> some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let radius = chartSize / 2
            let available = allAxisData.filter { $0.rating.isAvailable }
            guard !available.isEmpty else { return }

            // Build polygon using each available axis at its fixed angle position
            let path = dataPolygonPathForAvailable(center: center, radius: radius, allAxisData: allAxisData)
            context.fill(path, with: .color(AppColors.primaryBlue.opacity(0.2)))
            context.stroke(path, with: .color(AppColors.primaryBlue), style: StrokeStyle(lineWidth: 2))
        }
        .frame(width: frameSize, height: frameSize)
    }

    // MARK: - Score Dots

    private func scoreDots(allAxisData: [SnapshotAxisData], frameSize: CGFloat) -> some View {
        let radius = chartSize / 2
        return ForEach(Array(allAxisData.enumerated()), id: \.element.id) { index, item in
            if item.rating.isAvailable {
                let angle = angleForIndex(index, total: sides)
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
    }

    // MARK: - Axis Labels

    private func axisLabels(axisData: [SnapshotAxisData], frameSize: CGFloat) -> some View {
        let labelRadius = chartSize / 2 + 36
        return ForEach(Array(axisData.enumerated()), id: \.element.id) { index, item in
            let angle = angleForIndex(index, total: sides)

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
        guard total > 0 else { return 0 }
        return startAngle + CGFloat(index) * (2 * .pi / CGFloat(total))
    }

    private func polygonPath(center: CGPoint, radius: CGFloat, sides: Int) -> Path {
        var path = Path()
        guard sides > 0 else { return path }
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

    /// Build polygon path connecting only available axes at their fixed positions
    private func dataPolygonPathForAvailable(center: CGPoint, radius: CGFloat, allAxisData: [SnapshotAxisData]) -> Path {
        var path = Path()
        var firstPoint = true
        for (index, item) in allAxisData.enumerated() {
            guard item.rating.isAvailable else { continue }
            let angle = angleForIndex(index, total: sides)
            let r = radius * min(max(CGFloat(item.normalized), 0), 1.0)
            let point = CGPoint(
                x: center.x + r * cos(angle),
                y: center.y + r * sin(angle)
            )
            if firstPoint {
                path.move(to: point)
                firstPoint = false
            } else {
                path.addLine(to: point)
            }
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
