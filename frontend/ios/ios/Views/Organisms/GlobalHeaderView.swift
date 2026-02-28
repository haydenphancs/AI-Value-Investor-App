//
//  GlobalHeaderView.swift
//  ios
//
//  Organism: Standardized global header used across all main tabs.
//  Layout: App Logo (left) | Smart Search bar (center) | Profile Avatar (right)
//

import SwiftUI

struct GlobalHeaderView: View {
    @Environment(\.appState) private var appState
    @State private var showSloganSheet = false

    var searchPlaceholder: String = "Search ticker or ask AI..."
    var onSearchTapped: (() -> Void)?
    var onProfileTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Left: App Logo
            Button(action: {
                showSloganSheet = true
            }) {
                LogoView()
            }
            .buttonStyle(PlainButtonStyle())

            // Center: Smart Search Bar (flexible)
            TappableSearchBar(
                placeholder: searchPlaceholder,
                onTap: onSearchTapped
            )

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
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .fullScreenCover(isPresented: $showSloganSheet) {
            CaydexSloganView()
        }
    }
}

// MARK: - Profile Avatar View
/// Loads the user's external avatar URL. Falls back to a default silhouette icon.
struct ProfileAvatarView: View {
    let avatarUrl: String?
    var size: CGFloat = 36

    var body: some View {
        if let urlString = avatarUrl, let url = URL(string: urlString) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(width: size, height: size)
                        .clipShape(Circle())
                case .failure:
                    fallbackAvatar
                case .empty:
                    fallbackAvatar
                @unknown default:
                    fallbackAvatar
                }
            }
        } else {
            fallbackAvatar
        }
    }

    private var fallbackAvatar: some View {
        Image(systemName: "person.crop.circle.fill")
            .font(.system(size: size))
            .foregroundColor(AppColors.primaryBlue)
    }
}

// MARK: - Caydex Slogan View
struct CaydexSloganView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            Image("CaydexSlogan")
                .resizable()
                .aspectRatio(contentMode: .fit)
                .padding(.horizontal, AppSpacing.xxxl)

            // Close button
            VStack {
                HStack {
                    Spacer()
                    Button(action: {
                        dismiss()
                    }) {
                        Image(systemName: "xmark.circle.fill")
                            .font(AppTypography.titleLarge)
                            .foregroundStyle(AppColors.textSecondary, AppColors.cardBackground)
                    }
                    .buttonStyle(PlainButtonStyle())
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.lg)

                Spacer()
            }
        }
        .preferredColorScheme(.dark)
    }
}

#Preview {
    VStack {
        GlobalHeaderView()
        GlobalHeaderView(searchPlaceholder: "Search market news...")
        Spacer()
    }
    .environment(AppState())
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}

#Preview("Slogan") {
    CaydexSloganView()
}
