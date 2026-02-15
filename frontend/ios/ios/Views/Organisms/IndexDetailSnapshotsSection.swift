//
//  IndexDetailSnapshotsSection.swift
//  ios
//
//  Organism: Snapshots section for Index Detail with categories (no ranking)
//

import SwiftUI

struct IndexDetailSnapshotsSection: View {
    let snapshots: [IndexSnapshotItem]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title
            Text("Snapshots")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            // Snapshot cards
            VStack(spacing: 0) {
                ForEach(snapshots) { snapshot in
                    IndexSnapshotCard(snapshot: snapshot)
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Index Snapshot Card
struct IndexSnapshotCard: View {
    let snapshot: IndexSnapshotItem
    @State private var isExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header row
            Button(action: {
                withAnimation(.easeInOut(duration: 0.2)) {
                    isExpanded.toggle()
                }
            }) {
                HStack(spacing: AppSpacing.md) {
                    // Category icon
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.cardBackgroundLight)
                            .frame(width: 36, height: 36)

                        Image(systemName: snapshot.category.iconName)
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(AppColors.textSecondary)
                    }

                    // Category name
                    Text(snapshot.category.rawValue)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    // Coming soon badge
                    if snapshot.metrics.isEmpty {
                        Text("Coming Soon")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .padding(.horizontal, AppSpacing.sm)
                            .padding(.vertical, AppSpacing.xxs)
                            .background(AppColors.cardBackgroundLight)
                            .cornerRadius(AppCornerRadius.small)
                    }

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
                if snapshot.metrics.isEmpty {
                    // Placeholder for future content
                    VStack(spacing: AppSpacing.sm) {
                        Text("Content will be available soon.")
                            .font(AppTypography.footnote)
                            .foregroundColor(AppColors.textMuted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.bottom, AppSpacing.md)
                } else {
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
            }

            // Divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
    }
}

#Preview {
    ScrollView {
        IndexDetailSnapshotsSection(snapshots: IndexSnapshotItem.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
