//
//  TrackingHeader.swift
//  ios
//
//  Organism: Tracking screen header — uses the standardized GlobalHeaderView
//  plus a segmented tab control below.
//

import SwiftUI

struct TrackingHeader: View {
    @Binding var selectedTab: TrackingTab
    var onSearchTapped: (() -> Void)?
    var onProfileTapped: (() -> Void)?

    var body: some View {
        // `md`, not `lg`: this is the gap between the header row and the segmented control.
        // The row already contributes its own `.padding(.vertical, .sm)` underneath, so `lg`
        // here read as ~24pt of dead space above the toggle. Kept identical in ResearchHeader
        // and TrackingHeader — they sit at the same height and must stay in step.
        VStack(spacing: AppSpacing.md) {
            // Standardized global header row
            GlobalHeaderView(
                searchPlaceholder: "Add tickers",
                onSearchTapped: onSearchTapped,
                onProfileTapped: onProfileTapped
            )

            // Segmented Tab Control
            SegmentedTabControl(
                tabs: TrackingTab.allCases,
                selectedTab: $selectedTab
            )
            .padding(.horizontal, AppSpacing.lg)
        }
        .padding(.bottom, AppSpacing.md)
        .background(AppColors.background)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedTab = TrackingTab.assets

        var body: some View {
            VStack {
                TrackingHeader(selectedTab: $selectedTab)
                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
}
