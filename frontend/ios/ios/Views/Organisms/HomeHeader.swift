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
        ZStack {
            Circle()
                .fill(AppColors.cardBackground)
                .frame(width: 36, height: 36)

            Text("logo")
                .font(.system(size: 8, weight: .medium))
                .foregroundColor(AppColors.textMuted)
        }
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
