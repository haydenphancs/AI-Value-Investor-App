//
//  DiversificationCard.swift
//  ios
//
//  Molecule: Portfolio diversification card.
//  One overall "Diversification" bar (no number) → breakdown donut
//  (Sector / Size) → four additive point-bars whose points add up to the score.
//

import SwiftUI

struct DiversificationCard: View {
    let score: DiversificationScore
    var coverageNote: String? = nil

    @State private var breakdown: Breakdown = .sector

    // Color palette for breakdown donut segments.
    private static let palette: [Color] = [
        AppColors.primaryBlue,
        AppColors.bullish,
        AppColors.alertOrange,
        AppColors.accentCyan,
        AppColors.accentYellow,
        AppColors.bearish,
        AppColors.neutral,
    ]

    enum Breakdown: String, CaseIterable {
        case sector = "Sector"
        case size = "Size"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            overallSection

            divider
            breakdownSection

            if !score.subScores.isEmpty {
                divider
                pointBars
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Overall (one bar, no number)

    private var overallSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Text("Diversification")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                Spacer()
                Text(score.message)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(zoneColor(score.zone))
            }

            GradientProgressBar(
                progress: score.progressValue,
                height: 10,
                gradientColors: [zoneColor(score.zone), zoneColor(score.zone).opacity(0.6)]
            )

            HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {
                // Total = sum of the four bars' points, colored by zone.
                // Matches the whale card's "$162K" amount (bodySmallEmphasis).
                Text("\(score.score)/100")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(zoneColor(score.zone))
                if let coverageNote {
                    Spacer(minLength: AppSpacing.sm)
                    Text(coverageNote)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
    }

    // MARK: - Breakdown (donut + switcher)

    private var breakdownSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            if availableBreakdowns.count > 1 {
                breakdownPicker
            }
            DonutChartView(
                segments: segments(for: allocations(for: activeBreakdown)),
                lineWidth: 20,
                showLabels: true
            )
            .frame(maxWidth: .infinity)
        }
    }

    private var breakdownPicker: some View {
        HStack(spacing: 0) {
            ForEach(availableBreakdowns, id: \.self) { option in
                let isActive = option == activeBreakdown
                Button {
                    breakdown = option
                } label: {
                    Text(option.rawValue)
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(isActive ? .white : AppColors.textSecondary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.xs)
                        .background(isActive ? AppColors.primaryBlue : Color.clear)
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(2)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }

    // MARK: - Point bars (each contributes points to the whole)

    private var pointBars: some View {
        VStack(spacing: AppSpacing.sm) {
            ForEach(score.subScores) { sub in
                PointBar(
                    label: sub.label,
                    progress: sub.progressValue,
                    pointsText: sub.pointsText,
                    color: zoneColor(sub.zone)
                )
            }
        }
    }

    // MARK: - Helpers

    private var divider: some View {
        Rectangle()
            .fill(AppColors.textMuted.opacity(0.15))
            .frame(height: 1)
    }

    private func allocations(for breakdown: Breakdown) -> [SectorAllocation] {
        switch breakdown {
        case .sector: return score.sectorAllocations
        case .size:   return score.marketcapAllocations
        }
    }

    private var availableBreakdowns: [Breakdown] {
        Breakdown.allCases.filter { !allocations(for: $0).isEmpty }
    }

    /// The selected breakdown if it has data, else the first available one.
    private var activeBreakdown: Breakdown {
        availableBreakdowns.contains(breakdown) ? breakdown : (availableBreakdowns.first ?? .sector)
    }

    private func segments(for allocations: [SectorAllocation]) -> [DonutChartSegment] {
        allocations.enumerated().map { index, allocation in
            DonutChartSegment(
                value: allocation.percentage,
                color: Self.palette[index % Self.palette.count],
                label: allocation.name
            )
        }
    }

    private func zoneColor(_ zone: String) -> Color {
        switch zone {
        case "green":  return AppColors.bullish
        case "yellow": return AppColors.alertOrange
        case "red":    return AppColors.bearish
        default:       return AppColors.neutral
        }
    }
}

// MARK: - Point Bar

private struct PointBar: View {
    let label: String
    let progress: Double
    let pointsText: String
    let color: Color

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(width: 120, alignment: .leading)

            GradientProgressBar(
                progress: progress,
                height: 6,
                gradientColors: [color, color.opacity(0.5)]
            )

            Text(pointsText)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .frame(width: 44, alignment: .trailing)
        }
    }
}

#Preview {
    ScrollView {
        DiversificationCard(score: DiversificationScore.sampleData)
            .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
