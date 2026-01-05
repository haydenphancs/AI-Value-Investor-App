//
//  KeyTakeawaysSection.swift
//  ios
//
//  Organism: Complete key takeaways section with header and items
//

import SwiftUI

struct KeyTakeawaysSection: View {
    let takeaways: [KeyTakeaway]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header
            sectionHeader

            // Takeaway Items
            VStack(alignment: .leading, spacing: AppSpacing.xl) {
                ForEach(takeaways) { takeaway in
                    KeyTakeawayRow(takeaway: takeaway)
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    private var sectionHeader: some View {
        HStack(spacing: AppSpacing.sm) {
            // List Icon
            Image(systemName: "list.bullet.rectangle.portrait.fill")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(AppColors.primaryBlue)

            Text("Key Takeaways")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview {
    ScrollView {
        KeyTakeawaysSection(
            takeaways: [
                KeyTakeaway(
                    index: 1,
                    text: "Despite record Q4 results, missing expectations signals slowing growth and weaker-than-hoped execution."
                ),
                KeyTakeaway(
                    index: 2,
                    text: "A miss in a flagship quarter raises doubts about forward demand and near-term visibility."
                ),
                KeyTakeaway(
                    index: 3,
                    text: "Leadership transition at this scale introduces strategic and execution risk during a critical AI cycle."
                ),
                KeyTakeaway(
                    index: 4,
                    text: "With expectations priced for perfection, even a small miss could trigger outsized market pressure."
                )
            ]
        )
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
