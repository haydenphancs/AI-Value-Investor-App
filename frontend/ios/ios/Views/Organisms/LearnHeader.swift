//
//  LearnHeader.swift
//  ios
//
//  Organism: Learn (Wiser) screen header — uses the standardized GlobalHeaderView
//  plus a tab control below.
//

import SwiftUI

struct LearnHeader: View {
    @Binding var selectedTab: LearnTab
    var onSearchTapped: (() -> Void)?
    var onProfileTapped: (() -> Void)?
    /// Called when the "Chat" tab is tapped — the Wiser screen presents the full-screen
    /// AIChatScreen cover instead of switching inline content.
    var onChatTapped: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Standardized global header row
            GlobalHeaderView(
                searchPlaceholder: "Search topics or ask AI...",
                onSearchTapped: onSearchTapped,
                onProfileTapped: onProfileTapped
            )

            // Tab control
            LearnTabControl(selectedTab: $selectedTab, onChatTapped: onChatTapped)
                .padding(.horizontal, AppSpacing.lg)
        }
        .padding(.bottom, AppSpacing.sm)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedTab = LearnTab.learn

        var body: some View {
            VStack {
                LearnHeader(selectedTab: $selectedTab)
                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
