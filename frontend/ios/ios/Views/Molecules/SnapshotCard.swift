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
                withAnimation(.easeInOut(duration: 0.25)) {
                    isExpanded.toggle()
                }
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
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            // Metrics list
            if isExpanded {
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    ForEach(snapshot.metrics) { metric in
                        HStack {
                            // `displayName`: an industry median reads "industry avg"
                            // (the wire name always says "sector avg").
                            Text(metric.displayName)
                                .font(AppTypography.labelSmall)
                                .foregroundColor(AppColors.textSecondary)

                            Spacer()

                            Text(metric.value)
                                .font(AppTypography.labelSmallEmphasis)
                                .foregroundColor(AppColors.textPrimary)
                        }
                    }
                }
                .padding(.bottom, AppSpacing.md)
                .transition(.opacity)
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
}

#Preview("Industry and sector medians") {
    // Illustrative values. The first row's median is the INDUSTRY's ("industry avg"), the
    // second the sector's, the third has no peer comparison (shown as sent).
    ScrollView {
        SnapshotCard(snapshot: SnapshotItem(
            category: .price,
            rating: .average,
            metrics: [
                SnapshotMetric(name: "P/E (1.30x sector avg 22.4)", value: "29.12", peerLevel: "industry"),
                SnapshotMetric(name: "P/S (7.50x sector avg 0.98)", value: "7.35", peerLevel: "sector"),
                SnapshotMetric(name: "EV/EBITDA", value: "18.20"),
            ]
        ))
        .padding(.horizontal, AppSpacing.lg)
    }
    .background(AppColors.background)
}
