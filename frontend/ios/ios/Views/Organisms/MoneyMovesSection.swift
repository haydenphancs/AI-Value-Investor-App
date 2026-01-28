//
//  MoneyMovesSection.swift
//  ios
//
//  Organism: Horizontal scrolling section of money moves
//

import SwiftUI

struct MoneyMovesSection: View {
    let concepts: [MoneyMove]
    var onSeeAll: (() -> Void)?
    var onConceptTap: ((MoneyMove) -> Void)?
    var onBookmark: ((MoneyMove) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Money Moves")
                        .font(AppTypography.title3)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Most Read")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                Button(action: {
                    onSeeAll?()
                }) {
                    Text("See All")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll of money move cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ForEach(concepts) { moneyMove in
                        MoneyMoveCard(
                            moneyMove: moneyMove,
                            onTap: { onConceptTap?(moneyMove) },
                            onBookmark: { onBookmark?(moneyMove) }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.xxl) {
            MoneyMovesSection(concepts: MoneyMove.sampleData)
            Spacer()
        }
        .padding(.top, AppSpacing.md)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
