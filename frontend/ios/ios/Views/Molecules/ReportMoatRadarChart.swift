//
//  ReportMoatRadarChart.swift
//  ios
//
//  Molecule: Custom pentagon radar chart for competitive moat dimensions.
//  Draws two overlapping polygons: company (filled) and peers (outline).
//

import SwiftUI

struct ReportMoatRadarChart: View {
    let dimensions: [MoatDimension]

    private let chartSize: CGFloat = 200
    private let rings = 5

    var body: some View {
        ZStack {
            // Grid rings + axis lines
            radarGrid

            // Peer polygon (outline)
            radarPolygon(
                values: dimensions.map(\.normalizedPeerScore),
                fillColor: AppColors.textMuted.opacity(0.08),
                strokeColor: AppColors.textMuted.opacity(0.4),
                strokeStyle: StrokeStyle(lineWidth: 1, dash: [4, 3])
            )

            // Company polygon (filled)
            radarPolygon(
                values: dimensions.map(\.normalizedScore),
                fillColor: AppColors.primaryBlue.opacity(0.2),
                strokeColor: AppColors.primaryBlue,
                strokeStyle: StrokeStyle(lineWidth: 2)
            )

            // Score dots on company polygon
            scoreDots

            // Axis labels
            axisLabels
        }
        .frame(width: chartSize + 80, height: chartSize + 80)
    }

    // MARK: - Grid

    private var radarGrid: some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let radius = chartSize / 2

            // Concentric rings
            for ring in 1...rings {
                let r = radius * CGFloat(ring) / CGFloat(rings)
                let ringPath = polygonPath(
                    center: center,
                    radius: r,
                    sides: dimensions.count
                )
                context.stroke(
                    ringPath,
                    with: .color(AppColors.textMuted.opacity(0.12)),
                    lineWidth: 0.5
                )
            }

            // Axis spokes
            for i in 0..<dimensions.count {
                let angle = angleForIndex(i, total: dimensions.count) - .pi / 2
                let endPoint = CGPoint(
                    x: center.x + radius * cos(angle),
                    y: center.y + radius * sin(angle)
                )
                var spoke = Path()
                spoke.move(to: center)
                spoke.addLine(to: endPoint)
                context.stroke(
                    spoke,
                    with: .color(AppColors.textMuted.opacity(0.12)),
                    lineWidth: 0.5
                )
            }
        }
        .frame(width: chartSize + 80, height: chartSize + 80)
    }

    // MARK: - Polygon

    private func radarPolygon(
        values: [Double],
        fillColor: Color,
        strokeColor: Color,
        strokeStyle: StrokeStyle
    ) -> some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let radius = chartSize / 2
            let path = dataPolygonPath(center: center, radius: radius, values: values)

            context.fill(path, with: .color(fillColor))
            context.stroke(path, with: .color(strokeColor), style: strokeStyle)
        }
        .frame(width: chartSize + 80, height: chartSize + 80)
    }

    // MARK: - Score Dots

    private var scoreDots: some View {
        let center = CGPoint(x: (chartSize + 80) / 2, y: (chartSize + 80) / 2)
        let radius = chartSize / 2

        return ForEach(Array(dimensions.enumerated()), id: \.element.id) { index, dimension in
            let angle = angleForIndex(index, total: dimensions.count) - .pi / 2
            let r = radius * dimension.normalizedScore
            let x = center.x + r * cos(angle)
            let y = center.y + r * sin(angle)

            Circle()
                .fill(AppColors.primaryBlue)
                .frame(width: 6, height: 6)
                .shadow(color: AppColors.primaryBlue.opacity(0.6), radius: 4)
                .position(x: x, y: y)
        }
    }

    // MARK: - Axis Labels

    private var axisLabels: some View {
        let center = CGPoint(x: (chartSize + 80) / 2, y: (chartSize + 80) / 2)
        let labelRadius = chartSize / 2 + 32

        return ForEach(Array(dimensions.enumerated()), id: \.element.id) { index, dimension in
            let angle = angleForIndex(index, total: dimensions.count) - .pi / 2
            let x = center.x + labelRadius * cos(angle)
            let y = center.y + labelRadius * sin(angle)

            VStack(spacing: 1) {
                Text(dimension.name)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
                Text(String(format: "%.1f", dimension.score))
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .frame(width: 72)
            .position(x: x, y: y)
        }
    }

    // MARK: - Geometry Helpers

    private func angleForIndex(_ index: Int, total: Int) -> CGFloat {
        CGFloat(index) * (2 * .pi / CGFloat(total))
    }

    private func polygonPath(center: CGPoint, radius: CGFloat, sides: Int) -> Path {
        var path = Path()
        for i in 0...sides {
            let angle = angleForIndex(i % sides, total: sides) - .pi / 2
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
            let angle = angleForIndex(idx, total: values.count) - .pi / 2
            let r = radius * min(max(values[idx], 0), 1.0)
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

#Preview {
    ReportMoatRadarChart(
        dimensions: TickerReportData.sampleOracle.moatCompetition.dimensions
    )
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
