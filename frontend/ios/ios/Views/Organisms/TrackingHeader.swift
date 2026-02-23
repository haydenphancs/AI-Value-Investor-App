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
        VStack(spacing: AppSpacing.lg) {
            // Standardized global header row
            GlobalHeaderView(
                searchPlaceholder: "Search to add to watchlist...",
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
        .preferredColorScheme(.dark)
}
