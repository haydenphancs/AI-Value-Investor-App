//
//  KeyTakeawayRow.swift
//  ios
//
//  Molecule: Single key takeaway item with numbered badge
//

import SwiftUI

struct KeyTakeawayRow: View {
    let takeaway: KeyTakeaway

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Numbered Badge
            NumberedBadge(number: takeaway.index, size: 26)
                .padding(.top, 2)

            // Takeaway Text
            Text(takeaway.text)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .lineSpacing(4)
        }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.lg) {
        KeyTakeawayRow(
            takeaway: KeyTakeaway(
                index: 1,
                text: "Despite record Q4 results, missing expectations signals slowing growth and weaker-than-hoped execution."
            )
        )

        KeyTakeawayRow(
            takeaway: KeyTakeaway(
                index: 2,
                text: "A miss in a flagship quarter raises doubts about forward demand and near-term visibility."
            )
        )

        KeyTakeawayRow(
            takeaway: KeyTakeaway(
                index: 3,
                text: "Leadership transition at this scale introduces strategic and execution risk during a critical AI cycle."
            )
        )

        KeyTakeawayRow(
            takeaway: KeyTakeaway(
                index: 4,
                text: "With expectations priced for perfection, even a small miss could trigger outsized market pressure."
            )
        )
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
