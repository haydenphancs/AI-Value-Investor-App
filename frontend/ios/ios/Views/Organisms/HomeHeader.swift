//
//  HomeHeader.swift
//  ios
//
//  Organism: Home screen header with logo, search, and profile
//

import SwiftUI

struct HomeHeader: View {
    var onProfileTapped: (() -> Void)?
    var onSearchTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Logo placeholder
            LogoView()

            // Tappable Search Bar - navigates to SearchView
            TappableSearchBar(
                placeholder: "Search ticker or ask AI...",
                onTap: onSearchTapped
            )

            // Profile Button
            Button(action: {
                onProfileTapped?()
            }) {
                Image(systemName: "person.crop.circle.fill")
                    .font(.system(size: 32))
                    .foregroundColor(AppColors.primaryBlue)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
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
