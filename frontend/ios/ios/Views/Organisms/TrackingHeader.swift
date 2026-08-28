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

    /// Read HERE rather than passed in from the screen. `TrackingView.swift` holds two copies of
    /// this call (`TrackingContentView` is dead; `TrackingContentViewWithBinding` is the live one
    /// `ContentView` builds), so a parameter would have to be threaded twice and could silently be
    /// added to only one. Reading the environment inside the organism covers both by construction.
    ///
    /// `\.appState` and not `@Environment(AppState.self)`: the key declares a default, so the
    /// `#Preview` below still renders instead of trapping on a missing value.
    @Environment(\.appState) private var appState

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

            // Segmented Tab Control.
            //
            // The Alerts segment carries the unread-notification badge — the same count the
            // Tracking item in the bottom tab bar shows, through the same `UnreadCountBadge`.
            // A TestFlight tester asked for it: the count was previously only visible on the
            // tab bar, and only ever refreshed BY the Alerts tab, which clears it on sight.
            //
            // It is therefore visible only while Assets/Whales is selected, which is the point —
            // opening Alerts marks everything read and the badge goes away.
            SegmentedTabControl(
                tabs: TrackingTab.allCases,
                selectedTab: $selectedTab,
                badges: [TrackingTab.alerts.rawValue: appState.unreadNotificationCount]
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
