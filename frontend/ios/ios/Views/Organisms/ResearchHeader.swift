//
//  ResearchHeader.swift
//  ios
//
//  Organism: Research screen header — the same row metrics as TrackingHeader,
//  with the Cay AI mark in place of the search bar.
//

import SwiftUI

struct ResearchHeader: View {
    @Environment(\.appState) private var appState
    @State private var showSloganSheet = false
    @Binding var selectedTab: ResearchTab
    var onProfileTapped: (() -> Void)?

    var body: some View {
        // `md`, not `lg`: this is the gap between the header row and the segmented control.
        // The row already contributes its own `.padding(.vertical, .sm)` underneath, so `lg`
        // here read as ~24pt of dead space above the toggle. Kept identical in ResearchHeader
        // and TrackingHeader — they sit at the same height and must stay in step.
        VStack(spacing: AppSpacing.md) {
            // Standardized header row — same metrics as GlobalHeaderView (see
            // `globalHeaderRowHeight`), with the Cay AI mark where the search bar sits.
            HStack(spacing: AppSpacing.md) {
                // Left: App Logo
                Button(action: {
                    showSloganSheet = true
                }) {
                    LogoView()
                }
                .buttonStyle(PlainButtonStyle())

                // Center: the Cay AI mark, alone.
                //
                // This was `sparkles.2` + "AI Research Analysis". The words are redundant —
                // the tab is labelled Research and the segmented control right below says
                // Research / Reports — so the mark carries it. Sized up from `iconSmall`,
                // which was chosen to sit beside text it no longer has.
                //
                // DECORATIVE, and deliberately not a button: the boxed sparkle on the other
                // four tabs opens Cay AI chat, so this one is kept visually distinct (bare
                // glyph, centred, no tile) and hidden from VoiceOver rather than announcing
                // itself as something to tap.
                Image(systemName: AppSymbols.ai)
                    .font(AppTypography.iconLarge).fontWeight(.medium)
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(maxWidth: .infinity)
                    .accessibilityHidden(true)

                // Right: Profile Avatar
                Button(action: {
                    onProfileTapped?()
                }) {
                    ProfileAvatarView(
                        avatarUrl: appState.user.profile?.avatarUrl,
                        size: 36
                    )
                }
                .buttonStyle(PlainButtonStyle())
            }
            // Without this the row is only as tall as the 36pt avatar, while every other tab's
            // row is set by its ~42pt search bar — so the whole header, segmented control
            // included, jumped up on entering Research and back down on leaving.
            .globalHeaderRowHeight()
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.sm)

            // Segmented Tab Control (same as TrackingHeader)
            SegmentedTabControl(
                tabs: ResearchTab.allCases,
                selectedTab: $selectedTab
            )
            .padding(.horizontal, AppSpacing.lg)
        }
        .padding(.bottom, AppSpacing.sm)
        .fullScreenCover(isPresented: $showSloganSheet) {
            CaydexSloganView()
        }
    }
}

#Preview {
    VStack {
        ResearchHeader(selectedTab: .constant(.research))
        Spacer()
    }
    .environment(AppState())
    .background(AppColors.background)
}
