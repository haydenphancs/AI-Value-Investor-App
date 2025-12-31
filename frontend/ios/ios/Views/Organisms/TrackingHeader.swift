//
//  TrackingHeader.swift
//  ios
//
//  Organism: Header for Tracking screen with search, tabs, and profile
//

import SwiftUI

struct TrackingHeader: View {
    @Binding var searchText: String
    @Binding var selectedTab: TrackingTab
    var onProfileTapped: (() -> Void)?
    var onSearchSubmit: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Top Row: Logo, Search, Profile
            HStack(spacing: AppSpacing.md) {
                // Logo placeholder
                Circle()
                    .fill(AppColors.cardBackground)
                    .frame(width: 32, height: 32)
                    .overlay(
                        Text("logo")
                            .font(.system(size: 8))
                            .foregroundColor(AppColors.textMuted)
                    )

                // Search Bar
                SearchBar(
                    text: $searchText,
                    placeholder: "Search ticker or whale...",
                    onSubmit: onSearchSubmit
                )

                // Profile Button
                Button {
                    onProfileTapped?()
                } label: {
                    ZStack {
                        Circle()
                            .fill(AppColors.cardBackground)
                            .frame(width: 36, height: 36)

                        Image(systemName: "person.circle")
                            .font(.system(size: 22))
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, AppSpacing.lg)

            // Segmented Tab Control
            SegmentedTabControl(
                tabs: TrackingTab.allCases,
                selectedTab: $selectedTab
            )
            .padding(.horizontal, AppSpacing.lg)
        }
        .padding(.top, AppSpacing.sm)
        .padding(.bottom, AppSpacing.md)
        .background(AppColors.background)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var searchText = ""
        @State private var selectedTab = TrackingTab.assets

        var body: some View {
            VStack {
                TrackingHeader(
                    searchText: $searchText,
                    selectedTab: $selectedTab
                )
                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
}
