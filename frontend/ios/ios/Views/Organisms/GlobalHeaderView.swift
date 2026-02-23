//
//  GlobalHeaderView.swift
//  ios
//
//  Organism: Standardized global header used across all main tabs.
//  Layout: App Logo (left) | Smart Search bar (center) | Profile Avatar (right)
//

import SwiftUI

struct GlobalHeaderView: View {
    @Environment(AppState.self) private var appState

    var searchPlaceholder: String = "Search ticker or ask AI..."
    var onSearchTapped: (() -> Void)?
    var onProfileTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Left: App Logo
            LogoView()

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

#Preview {
    VStack {
        GlobalHeaderView()
        GlobalHeaderView(searchPlaceholder: "Search market news...")
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
