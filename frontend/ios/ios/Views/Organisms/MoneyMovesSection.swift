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

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text("Money Moves")
                        .font(AppTypography.heading)
                        .foregroundColor(AppColors.textPrimary)

                    // "Most Recent", and the row is sorted to match: LearnViewModel orders
                    // purely by date now. It used to say "Most Read" while sorting unread-first
                    // — nothing counts reads, and the order changed under the reader as they
                    // completed things, so the label was wrong twice over.
                    Text("Most Recent")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                Button(action: {
                    onSeeAll?()
                }) {
                    Text("See All")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll of money move cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.sm) {
                    ForEach(concepts) { moneyMove in
                        MoneyMoveCard(
                            moneyMove: moneyMove,
                            onTap: { onConceptTap?(moneyMove) }
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
}
