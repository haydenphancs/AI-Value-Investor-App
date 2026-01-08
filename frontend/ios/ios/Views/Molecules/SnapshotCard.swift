//
//  SnapshotCard.swift
//  ios
//
//  Molecule: Expandable snapshot card showing rating category with metrics
//

import SwiftUI

struct SnapshotCard: View {
    let snapshot: SnapshotItem
    @State private var isExpanded: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header row
            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    // Rating indicator icon
                    SnapshotRatingIndicator(
                        category: snapshot.category,
                        rating: snapshot.rating
                    )

                    // Rating label and category
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: AppSpacing.xs) {
                            Text(snapshot.rating.displayName)
                                .font(AppTypography.calloutBold)
                                .foregroundColor(snapshot.rating.color)

                            Text(snapshot.category.rawValue)
                                .font(AppTypography.callout)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        // Star rating
                        SnapshotStarRating(rating: snapshot.rating, starSize: 10)
                    }

                    Spacer()

                    // Expand/collapse chevron
                    Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.vertical, AppSpacing.md)
            }
            .buttonStyle(PlainButtonStyle())

            // Metrics list (when expanded)
            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    ForEach(snapshot.metrics) { metric in
                        HStack {
                            Text(metric.name)
                                .font(AppTypography.footnote)
                                .foregroundColor(AppColors.textSecondary)

                            Spacer()

                            Text(metric.value)
                                .font(AppTypography.footnoteBold)
                                .foregroundColor(AppColors.textPrimary)
                        }
                    }

                }
                .padding(.bottom, AppSpacing.md)
            }

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
