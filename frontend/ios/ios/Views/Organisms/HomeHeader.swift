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
            searchPlaceholder: "Search ticker or ask AI...",
            onSearchTapped: onSearchTapped,
            onProfileTapped: onProfileTapped
        )
    }
}

// MARK: - Logo View
struct LogoView: View {
    var body: some View {
        Image("CaydexLogo")
            .resizable()
            .aspectRatio(contentMode: .fill)
            .frame(width: 36, height: 36)
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

#Preview {
    VStack {
        HomeHeader()
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
