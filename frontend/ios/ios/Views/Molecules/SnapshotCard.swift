//
//  SnapshotCard.swift
//  ios
//
//  Molecule: Expandable snapshot card showing rating category with metrics
//

import SwiftUI

struct SnapshotCard: View {
    let snapshot: SnapshotItem
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {

            // Header row
            Button(action: {
                isExpanded.toggle()
            }) {
                HStack(spacing: AppSpacing.md) {
                    // Rating indicator icon
                    SnapshotRatingIndicator(
                        category: snapshot.category,
                        rating: snapshot.rating
                    )

                    // Category and star rating
                    VStack(alignment: .leading, spacing: 2) {
                        Text(snapshot.category.rawValue)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textPrimary)

                        // Star rating
                        SnapshotStarRating(rating: snapshot.rating, starSize: 10)
                    }

                    Spacer()

                    // Expand/collapse chevron
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(AppTypography.iconXS).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.vertical, AppSpacing.md)
            }
            .buttonStyle(PlainButtonStyle())

            // Metrics list (when expanded)
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ForEach(snapshot.metrics) { metric in
                    HStack {
                        Text(metric.name)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textSecondary)

                        Spacer()

                        Text(metric.value)
                            .font(AppTypography.labelSmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                    }
                }
            }
            .padding(.bottom, isExpanded ? AppSpacing.md : 0)
            .frame(maxHeight: isExpanded ? .none : 0, alignment: .top)
            .clipped()

            // Divider (except for last item)
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

#Preview {
    ScrollView {
        VStack(spacing: 0) {
            ForEach(SnapshotItem.sampleData) { snapshot in
                SnapshotCard(snapshot: snapshot)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
