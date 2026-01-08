//
//  TickerDetailSnapshotsSection.swift
//  ios
//
//  Organism: Snapshots section for Ticker Detail with expandable cards
//

import SwiftUI

struct TickerDetailSnapshotsSection: View {
    let snapshots: [SnapshotItem]
    var onDeepResearchTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header with info button
            HStack {
                Text("Snapshots")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                // What's Snapshots? link
                Button(action: {
                    // Show info about Snapshots
                }) {
                    Text("What's Snapshots?")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(.horizontal, AppSpacing.lg)

            // Snapshot cards
            VStack(spacing: 0) {
                ForEach(snapshots) { snapshot in
                    SnapshotCard(snapshot: snapshot)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // AI Deep Research button
            AIDeepResearchButton {
                onDeepResearchTap?()
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        TickerDetailSnapshotsSection(snapshots: SnapshotItem.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
