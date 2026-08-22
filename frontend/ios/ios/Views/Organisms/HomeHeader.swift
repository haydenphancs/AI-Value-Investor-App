//
//  HomeHeader.swift
//  ios
//
//  Organism: Home screen header — uses the standardized GlobalHeaderView
//

import SwiftUI

struct HomeHeader: View {
    var onProfileTapped: (() -> Void)?
    var onSearchTapped: (() -> Void)?

    var body: some View {
        GlobalHeaderView(
            searchPlaceholder: "Search",
            onSearchTapped: onSearchTapped,
            onProfileTapped: onProfileTapped
        )
    }
}

// MARK: - Logo View
struct LogoView: View {
    /// Thin alias over `CaydexLogoMark` — this view's clipped treatment was the
    /// only correct one in the app, so it became the atom. Kept for its two
    /// existing call sites (GlobalHeaderView, ResearchHeader).
    var body: some View {
        CaydexLogoMark(size: 36)
    }
}

#Preview {
    VStack {
        HomeHeader()
        Spacer()
    }
    .background(AppColors.background)
}
