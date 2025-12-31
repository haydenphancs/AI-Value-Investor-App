//
//  HomeHeader.swift
//  ios
//
//  Organism: Home screen header with logo, search, and profile
//

import SwiftUI

struct HomeHeader: View {
    @Binding var searchText: String
    var onProfileTapped: (() -> Void)?
    var onSearchSubmit: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Logo placeholder
            LogoView()

            // Search Bar
            SearchBar(text: $searchText, onSubmit: onSearchSubmit)

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
        HomeHeader(searchText: .constant(""))
        Spacer()
    }
    .background(AppColors.background)
}
