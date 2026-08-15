//
//  PlanFeatureListSection.swift
//  ios
//
//  Organism: the feature list for ONE selected plan — the paid rows first, then a muted
//  "Included on every plan" block.
//
//  WHY ONE LIST AND NOT A COMPARISON TABLE
//  ---------------------------------------
//  Three value columns inside a 393pt screen leaves ~110pt each, and "Track unlimited
//  investors" cannot fit there at the accessibility text sizes without truncating — a
//  truncated comparison table is worse than none. Three per-plan cards, each with its own
//  eleven rows, puts the Max card thousands of points below the fold.
//
//  Comparison is preserved rather than lost: switching plans animates the SAME rows'
//  values (1 → 15 → 30, "Track 1" → "Track 10" → "Track unlimited"), which reads as a diff
//  more directly than a table does. It is also what makes Max's real shape legible — its
//  only advantages over Pro are three numbers, and watching exactly three rows change says
//  that more honestly than a column of repeated checkmarks.
//
//  The row the user was locked out of is hoisted to the top and keeps its ring across
//  selections, so they watch that specific thing change as they flip between plans.
//

import SwiftUI

struct PlanFeatureListSection: View {
    let planName: String
    let features: [PlanFeature]
    /// Server feature `key` of the row that triggered the paywall, if any.
    var highlightedKey: String?

    private var paidRows: [PlanFeature] {
        let rows = features.filter { !$0.isAlwaysIncluded }
        guard let highlightedKey,
              let index = rows.firstIndex(where: { $0.key == highlightedKey }) else { return rows }
        var hoisted = rows
        hoisted.insert(hoisted.remove(at: index), at: 0)
        return hoisted
    }

    private var alwaysRows: [PlanFeature] { features.filter(\.isAlwaysIncluded) }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            if !paidRows.isEmpty {
                Text("What \(planName) gives you")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)

                VStack(spacing: AppSpacing.sm) {
                    ForEach(paidRows) { feature in
                        PlanFeatureRowView(
                            feature: feature,
                            isHighlighted: feature.key == highlightedKey
                        )
                    }
                }
            }

            if !alwaysRows.isEmpty {
                // Named, not silent. This block is where the Investor Journey's free
                // narration lives — it is free on every tier, so listing it as a paid
                // unlock would be false and omitting it would be false by silence. It is
                // also the screen's own statement of where the paid line actually falls,
                // which a reader deciding whether to pay deserves rather than having to
                // infer that everything unlisted is behind the plan.
                Text("Included on every plan")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textSecondary)
                    .padding(.top, AppSpacing.sm)
                    .fixedSize(horizontal: false, vertical: true)

                VStack(spacing: AppSpacing.sm) {
                    ForEach(alwaysRows) { feature in
                        PlanFeatureRowView(feature: feature)
                    }
                }
                .opacity(0.85)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    ScrollView {
        PlanFeatureListSection(
            planName: "Pro",
            features: PlanFeature.bundled(for: .pro, monthlyCredits: 1200,
                                          reportCost: 20, chatCost: 1),
            highlightedKey: "learn_audio"
        )
        .padding()
    }
    .background(AppColors.background)
}
