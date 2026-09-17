//
//  ValuationMeter.swift
//  ios
//
//  Molecule: the Analysis tab's valuation gauge — the SAME arc, zones, needle and 1–5
//  scale as the Technical Meter, driven by the valuation snapshot's sector-relative
//  rating (P/E, P/B, P/S, P/FCF, EV/EBITDA against sector medians). That rating is what
//  the Overview tab's Valuation card already shows, so the two tabs cannot disagree.
//
//  Replaced the Street Estimates card on 2026-09-17 (TestFlight E9): forward consensus
//  was the only licensed analyst dataset left and the developer did not want it on this
//  tab. Multiples are entitled and already computed; the DCF row lives in the section.
//

import SwiftUI

struct ValuationMeter: View {
    let rating: SnapshotRatingLevel

    /// Left-to-right the gauge runs red → green, so 1 = most expensive vs peers and
    /// 5 = cheapest — the snapshot's own orientation (`_valuation_score`: lower multiples
    /// score higher). Level 3 is "within 20% of the sector".
    static let scaleLabels = ["Expensive", "Pricey", "Fair", "Cheap", "Bargain"]

    /// Centre label for the rating. Vocabulary shared with `ValuationLevel` (Bargain /
    /// Expensive) so the app says one thing about cheapness everywhere.
    static func label(for rating: SnapshotRatingLevel) -> String {
        switch rating {
        case .unavailable: return "—"
        case .poor:        return "Expensive"
        case .weak:        return "Pricey"
        case .average:     return "Fair"
        case .strong:      return "Cheap"
        case .excellent:   return "Bargain"
        }
    }

    /// Needle position: the centre of the rating's zone (five zones of 0.2). An
    /// unavailable rating parks the needle at the middle, as the technical gauge does
    /// when it has no indicators.
    static func gaugeValue(for rating: SnapshotRatingLevel) -> Double {
        guard rating != .unavailable else { return 0.5 }
        return (Double(rating.rawValue) - 1.0) * 0.2 + 0.1
    }

    private var labelColor: Color {
        switch rating {
        case .unavailable: return AppColors.textMuted
        case .poor:        return AppColors.loss
        case .weak:        return AppColors.bearish
        case .average:     return AppColors.caution
        case .strong:      return AppColors.gain
        case .excellent:   return AppColors.bullish
        }
    }

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            VStack(spacing: AppSpacing.xs) {
                Text("Valuation Meter")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text("Multiples vs sector peers")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            MeterGauge(
                label: Self.label(for: rating),
                labelColor: labelColor,
                gaugeValue: Self.gaugeValue(for: rating)
            )

            TechnicalLevelIndicatorsRow(
                activeLevel: rating == .unavailable ? 0 : rating.rawValue,
                labels: Self.scaleLabels
            )
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Valuation Meter: \(Self.label(for: rating))")
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        VStack(spacing: AppSpacing.xl) {
            ValuationMeter(rating: .weak)
            ValuationMeter(rating: .unavailable)
        }
        .padding()
    }
}
