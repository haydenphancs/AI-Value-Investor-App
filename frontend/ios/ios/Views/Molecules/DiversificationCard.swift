//
//  DiversificationCard.swift
//  ios
//
//  Molecule: Portfolio diversification card.
//  One overall "Diversification" bar with its score → an optional one-line hint →
//  breakdown donut (Sector / Size) → three additive point-bars whose points add up to
//  the score.
//

import SwiftUI

struct DiversificationCard: View {
    let score: DiversificationScore
    var coverageNote: String? = nil
    /// One informational line under the score row — "Scored on 2 holdings — add more
    /// tickers…" — derived on the client by `DiversificationHint` (TestFlight 1.0 (7):
    /// "add something like 'need to add new tickers'"). Nil when the book is large enough
    /// and fully entered. A plain caption, never advice and never a button: the "Edit
    /// holdings" link sits directly under the card.
    var hint: String? = nil

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

    /// Segment height of the Sector / Size picker: the height `RecentActivitiesTabSelector`'s
    /// segments come out at (a `bodyEmphasis` line ≈ 18pt + 2 × `AppSpacing.sm`), so the two
    /// in-card selectors read as one family. Pinned by test_ios_tap_target_guards.py.
    static let segmentMinHeight: CGFloat = 18 + 2 * AppSpacing.sm
    /// The picker's outer track: a segment plus the 2pt inset on each side (`.padding(2)`
    /// below). Pins the GeometryReader in `breakdownSection` to exactly that height.
    static let pickerTrackHeight: CGFloat = segmentMinHeight + 2 * 2

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
        .cardSurface(cornerRadius: AppCornerRadius.large)
    }

    // MARK: - Overall (bar + score + coverage + hint)

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

            if let hint {
                Text(hint)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }

    // MARK: - Breakdown (donut + switcher)

    private var breakdownSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            if availableBreakdowns.count > 1 {
                // Half the card's content width, centred (developer's call: the full-width
                // track read as heavy for a two-word choice). A GeometryReader pinned to the
                // picker's own height so it reports the width without claiming the card's
                // remaining height; the inner frame spans the full width so the half-width
                // track centres inside it.
                GeometryReader { geo in
                    breakdownPicker
                        .frame(width: geo.size.width / 2)
                        .frame(maxWidth: .infinity)
                }
                .frame(height: Self.pickerTrackHeight)
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
                    // A Button hit-tests its label's frame, and the 11pt caption plus 4pt
                    // of padding gave ~21pt to tap — reported from TestFlight 1.0 (6) as
                    // hard to hit. `.hitSlop()` cannot help on a Button (slop is clipped to
                    // the label frame), so the frame itself grows to the SAME height as the
                    // app's other in-card selectors (`RecentActivitiesTabSelector`: a
                    // bodyEmphasis line plus 2 × AppSpacing.sm) — the developer's call over
                    // the 44pt HIG figure, which read as oversized beside them. minHeight,
                    // never a fixed height, because captionEmphasis scales 1.4x with
                    // Dynamic Type. The fill comes AFTER the frame so the active pill spans
                    // the whole target, and the shape last so the whole segment, not just
                    // its glyphs, is live.
                    Text(option.rawValue)
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(isActive ? AppColors.textOnAccent : AppColors.textSecondary)
                        .frame(maxWidth: .infinity, minHeight: Self.segmentMinHeight)
                        .background(isActive ? AppColors.primaryFill : Color.clear)
                        .cornerRadius(AppCornerRadius.medium)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                // VoiceOver announced the active segment by colour only.
                .accessibilityAddTraits(isActive ? [.isSelected] : [])
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
        VStack(spacing: AppSpacing.lg) {
            DiversificationCard(score: DiversificationScore.sampleData)
            DiversificationCard(
                score: DiversificationScore.sampleData,
                coverageNote: "Based on 2 of 3 tickers",
                hint: DiversificationHint.make(scoredHoldings: 2, enteredTickers: 2, totalTickers: 3)
            )
        }
        .padding()
    }
    .background(AppColors.background)
}
