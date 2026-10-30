//
//  EarningsResultChips.swift
//  ios
//
//  Molecule: the earnings card's trailing results — one small capsule per metric, "EPS ▲ 7.3%"
//  and "REV ▼ 2.1%", replacing the date badge once any figure has landed. Each metric is
//  independent: whichever is out first shows first, the other reads "…" until it lands.
//
//  Green / red from `EarningsMetricOutcome.color` (text-safe `gain` / `loss`), and never
//  colour alone: the ▲ / ▼ glyph and, for VoiceOver, the words "beat" / "missed".
//

import SwiftUI

struct EarningsResultChips: View {
    let eps: EarningsMetricOutcome
    let revenue: EarningsMetricOutcome

    var body: some View {
        VStack(alignment: .trailing, spacing: AppSpacing.xs) {
            chip(label: "EPS", outcome: eps)
            chip(label: "REV", outcome: revenue)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
    }

    private func chip(label: String, outcome: EarningsMetricOutcome) -> some View {
        HStack(spacing: 3) {
            Text(label)
                .font(AppTypography.captionSmallEmphasis)
                .foregroundColor(AppColors.textMuted)
            if let arrow = outcome.arrowSymbol {
                Image(systemName: arrow)
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(outcome.color)
            }
            Text(value(outcome))
                .font(AppTypography.captionEmphasis)
                .foregroundColor(outcome.hasLanded ? outcome.color : AppColors.textMuted)
                .lineLimit(1)
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(AppColors.cardBackgroundLight)
        .clipShape(Capsule())
    }

    /// The percentage when there is one; "GAAP" / "In" when the figure is out but not
    /// comparable; "…" while it is not out (or held for confirmation).
    private func value(_ outcome: EarningsMetricOutcome) -> String {
        guard outcome.hasLanded else { return "…" }
        if outcome.isGAAP { return "GAAP" }
        if let text = outcome.surpriseText { return text }
        return outcome.word ?? "In"
    }

    private var spoken: String {
        func say(_ name: String, _ o: EarningsMetricOutcome) -> String {
            guard o.hasLanded else { return "\(name) not out yet" }
            if o.isGAAP { return "\(name) reported on a GAAP basis" }
            switch o.result {
            case .beat: return "\(name) beat \(o.surpriseText.map { "by \($0)" } ?? "")"
            case .missed: return "\(name) missed \(o.surpriseText.map { "by \($0)" } ?? "")"
            case .matched: return "\(name) in line"
            case .pending, .noEstimate: return "\(name) reported"
            }
        }
        return say("Earnings per share", eps) + ". " + say("Revenue", revenue) + "."
    }
}

#Preview {
    VStack(spacing: 16) {
        EarningsResultChips(
            eps: EarningsMetricOutcome(estimate: 0.41, actual: 0.44, surprisePct: 7.32),
            revenue: EarningsMetricOutcome(estimate: 16.6e9, actual: nil, surprisePct: nil)
        )
        EarningsResultChips(
            eps: EarningsMetricOutcome(estimate: 0.41, actual: 0.30, surprisePct: -26.8),
            revenue: EarningsMetricOutcome(estimate: 16.6e9, actual: 17.1e9, surprisePct: 3.01)
        )
    }
    .padding()
    .background(AppColors.cardBackground)
}
