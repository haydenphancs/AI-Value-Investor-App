//
//  ReportMoatRadarChart.swift
//  ios
//
//  Molecule: Custom pentagon radar chart for competitive moat dimensions.
//  Draws two overlapping polygons: company (filled) and peers (outline).
//  Every company vertex draws alike, whoever scored it (owner 2026-10-29: "no need to label
//  AI" — the verdict above the chart already ignores unmeasured pillars). A peer point on the
//  5.0 placeholder is hollow and takes the peer fill away.
//

import SwiftUI

struct ReportMoatRadarChart: View {
    /// Only read to bust the `.drawingGroup()` raster cache — see below.
    @Environment(\.colorScheme) private var colorScheme

    let dimensions: [MoatDimension]

    private let chartSize: CGFloat = 200
    // Outer frame gets generous padding around the chart so the side
    // axis labels ("Intangible Assets" / "Network Effects") sit at
    // labelRadius=132 with a 72pt-wide text frame don't get clipped by
    // the .drawingGroup() rasterization bounds. 70pt per side > 68pt
    // worst-case horizontal label extent (132·cos18° + 36).
    private let frameSize: CGFloat = 340
    private let rings = 5

    /// The Peer Avg outline and its legend swatch. A chart stroke needs 3:1 (WCAG 1.4.11):
    /// `textMuted` at 0.8 measures 3.67 on the white card / 3.35 on `cardBackgroundLight` in
    /// light and 4.47 / 4.12 in dark. The old 0.4 was ~1.8:1 — the line all but vanished.
    static let peerStrokeColor = AppColors.textMuted.opacity(0.8)

    /// One placeholder peer point and the shaded peer area would imply a measured average.
    private var hasPlaceholderPeer: Bool { dimensions.contains(where: \.peerIsPlaceholder) }

    /// A radar needs at least a triangle. Below that the geometry is undefined —
    /// and `polygonPath`/`dataPolygonPath` reduce an empty array to `i % 0`, which
    /// is a Swift runtime TRAP (integer remainder by zero), not a NaN. That is a
    /// hard crash, and it is reachable: `MoatCompetitionResponse.dimensions` has
    /// no minimum length, and `research_reports.ticker_report_data` rows are user
    /// HISTORY that `CACHE_SCHEMA_FLOOR` never invalidates — so a report saved
    /// before the deterministic moat scorer existed can still carry `[]` today.
    /// Opening it and expanding "Industry & Competitive Moat" killed the app.
    private var hasPlottableGeometry: Bool { dimensions.count >= 3 }

    var body: some View {
        if hasPlottableGeometry {
            chart
        } else {
            unavailableState
        }
    }

    /// Honest empty state — the same principle as the "—" CAGR and the null
    /// analyst targets: say the pillars are missing rather than draw a shape that
    /// implies scores we do not have.
    private var unavailableState: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "chart.pie")
                .font(.system(size: 28))
                .foregroundColor(AppColors.textMuted)
            Text("Moat pillar scores unavailable for this report")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xl)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Moat pillar scores unavailable for this report")
    }

    private var chart: some View {
        ZStack {
            // Grid rings + axis lines
            radarGrid

            // Peer polygon (outline; the faint fill only when every peer point is real)
            radarPolygon(
                values: dimensions.map(\.normalizedPeerScore),
                fillColor: hasPlaceholderPeer ? .clear : AppColors.textMuted.opacity(0.08),
                strokeColor: Self.peerStrokeColor,
                strokeStyle: StrokeStyle(lineWidth: 1, dash: [4, 3])
            )

            // Peer points whose provenance is known
            peerDots

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
        .frame(width: frameSize, height: frameSize)
        .drawingGroup()
        .accessibilityIgnoresInvertColors()
        .id(colorScheme)
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
        .frame(width: frameSize, height: frameSize)
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
        .frame(width: frameSize, height: frameSize)
    }

    // MARK: - Score Dots

    private var scoreDots: some View {
        let center = CGPoint(x: frameSize / 2, y: frameSize / 2)
        let radius = chartSize / 2

        return ForEach(Array(dimensions.enumerated()), id: \.element.id) { index, dimension in
            let angle = angleForIndex(index, total: dimensions.count) - .pi / 2
            // Clamped like the polygon, so an out-of-range score never floats off its vertex.
            let r = radius * clampedUnit(dimension.normalizedScore)
            let x = center.x + r * cos(angle)
            let y = center.y + r * sin(angle)

            Circle()
                .fill(AppColors.primaryBlue)
                .frame(width: 6, height: 6)
                .shadow(color: AppColors.primaryBlue.opacity(0.6), radius: 4)
                .position(x: x, y: y)
        }
    }

    // MARK: - Peer Dots

    /// Hollow for the 5.0 placeholder, filled for any other tagged peer point. Reports from
    /// before 2026-10-28 carry no `peer_source` and keep their old drawing (no point at all).
    private var peerDots: some View {
        let center = CGPoint(x: frameSize / 2, y: frameSize / 2)
        let radius = chartSize / 2

        return ForEach(Array(dimensions.enumerated()), id: \.element.id) { index, dimension in
            let angle = angleForIndex(index, total: dimensions.count) - .pi / 2
            let r = radius * clampedUnit(dimension.normalizedPeerScore)
            let x = center.x + r * cos(angle)
            let y = center.y + r * sin(angle)

            if dimension.peerIsPlaceholder {
                Circle()
                    .strokeBorder(Self.peerStrokeColor, lineWidth: 1)
                    .frame(width: 6, height: 6)
                    .position(x: x, y: y)
            } else if dimension.peerSource != nil {
                Circle()
                    .fill(Self.peerStrokeColor)
                    .frame(width: 4, height: 4)
                    .position(x: x, y: y)
            }
        }
    }

    // MARK: - Axis Labels

    private var axisLabels: some View {
        let center = CGPoint(x: frameSize / 2, y: frameSize / 2)
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
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .frame(width: 72)
            .accessibilityElement(children: .combine)
            .position(x: x, y: y)
        }
    }

    // MARK: - Geometry Helpers

    /// 0...1, and 0 for a non-finite value (`max`/`min` pass NaN straight through).
    private func clampedUnit(_ value: Double) -> CGFloat {
        guard value.isFinite else { return 0 }
        return CGFloat(min(max(value, 0), 1.0))
    }

    private func angleForIndex(_ index: Int, total: Int) -> CGFloat {
        guard total > 0 else { return 0 }
        return CGFloat(index) * (2 * .pi / CGFloat(total))
    }

    private func polygonPath(center: CGPoint, radius: CGFloat, sides: Int) -> Path {
        var path = Path()
        // `i % sides` traps on sides == 0. `body` already gates on >= 3, but the
        // helper must be safe on its own terms so a future caller cannot crash.
        guard sides > 0 else { return path }
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
        // Same trap as `polygonPath`, plus `values[idx]` would be out of range.
        guard !values.isEmpty else { return path }
        for i in 0...values.count {
            let idx = i % values.count
            let angle = angleForIndex(idx, total: values.count) - .pi / 2
            let r = radius * clampedUnit(values[idx])
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
}

#Preview("Report from before provenance") {
    ReportMoatRadarChart(
        dimensions: TickerReportData.sampleOracle.moatCompetition.dimensions.map {
            MoatDimension(name: $0.name, score: $0.score, peerScore: $0.peerScore)
        }
    )
    .padding()
    .background(AppColors.cardBackground)
}
