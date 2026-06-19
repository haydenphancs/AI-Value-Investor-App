//
//  DiversificationCard.swift
//  ios
//
//  Molecule: Portfolio diversification health card.
//  Score + letter grade → effective-holdings headline → breakdown donut
//  (sector / size / region) → per-dimension guardrail bars → actionable nudges.
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
        case region = "Region"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            scoreHeader

            if let coverageNote {
                Text(coverageNote)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Text(score.message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            divider
            breakdownSection

            if !score.subScores.isEmpty {
                divider
                guardrailSection
            }

            if !score.nudges.isEmpty {
                divider
                nudgeSection
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Score Header

    private var scoreHeader: some View {
        HStack(alignment: .center, spacing: AppSpacing.lg) {
            VStack(spacing: 0) {
                Text(score.formattedScore)
                    .font(AppTypography.dataHero)
                    .foregroundColor(zoneColor(score.zone))
                Text("/ 100")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            .frame(minWidth: 76)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                HStack(spacing: AppSpacing.sm) {
                    Text("Diversification Score")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                    gradePill
                }
                Text("Behaves like ~\(score.effectiveHoldingsText) independent holdings")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)
        }
    }

    private var gradePill: some View {
        Text(score.grade)
            .font(AppTypography.captionEmphasis)
            .foregroundColor(.white)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, 2)
            .background(zoneColor(score.zone))
            .cornerRadius(AppCornerRadius.pill)
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

    // MARK: - Guardrail bars

    private var guardrailSection: some View {
        VStack(spacing: AppSpacing.sm) {
            ForEach(score.subScores) { sub in
                GuardrailBar(
                    label: sub.label,
                    score: sub.score,
                    color: zoneColor(sub.zone)
                )
            }
        }
    }

    // MARK: - Nudges

    private var nudgeSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Suggestions")
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textMuted)

            ForEach(score.nudges) { nudge in
                NudgeRow(
                    nudge: nudge,
                    color: severityColor(nudge.severity),
                    icon: severityIcon(nudge.severity)
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
        case .region: return score.regionAllocations
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

    private func severityColor(_ severity: String) -> Color {
        switch severity {
        case "critical": return AppColors.bearish
        case "warning":  return AppColors.alertOrange
        default:         return AppColors.primaryBlue
        }
    }

    private func severityIcon(_ severity: String) -> String {
        switch severity {
        case "critical": return "exclamationmark.triangle.fill"
        case "warning":  return "exclamationmark.circle.fill"
        default:         return "lightbulb.fill"
        }
    }
}

// MARK: - Guardrail Bar

private struct GuardrailBar: View {
    let label: String
    let score: Int
    let color: Color

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(width: 120, alignment: .leading)

            GradientProgressBar(
                progress: Double(score) / 100.0,
                height: 6,
                gradientColors: [color, color.opacity(0.5)]
            )

            Text("\(score)")
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .frame(width: 28, alignment: .trailing)
        }
    }
}

// MARK: - Nudge Row

private struct NudgeRow: View {
    let nudge: DiversificationNudge
    let color: Color
    let icon: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: icon)
                .font(AppTypography.iconSmall)
                .foregroundColor(color)
                .padding(.top, 1)

            VStack(alignment: .leading, spacing: 2) {
                Text(nudge.title)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                Text(nudge.detail)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: 0)
        }
        .padding(AppSpacing.sm)
        .background(color.opacity(0.08))
        .cornerRadius(AppCornerRadius.medium)
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
