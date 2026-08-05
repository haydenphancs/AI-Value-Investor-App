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

/// Full-screen brand moment, presented from the header logo on every main tab.
///
/// **This screen is deliberately DARK in both appearances.** The slogan asset
/// (`Frame 43 (4).jpg`) is a JPEG — it has no alpha channel and its background
/// is baked pure `#000000`. On `AppColors.background` in light mode it rendered
/// as a near-full-width black square on `#F4F5F8` (20.4:1), reading as a broken
/// image rather than a brand mark.
///
/// Since the artwork cannot be made transparent, the fix is to stop fighting it:
/// paint the backdrop to MATCH the asset so the square has no edge, and treat
/// the screen as a brand ident (the same call made for the launch screen, which
/// is pinned to a fixed dark colour for the same reason). The close button is
/// pinned to fixed light ink because it now always sits on black.
///
/// If a light-appearance slogan asset is ever produced, revert the backdrop to
/// `AppColors.background` and add the variant to the imageset — nothing else
/// here needs to change.
struct CaydexSloganView: View {
    @Environment(\.dismiss) private var dismiss

    /// Matches the baked background of the slogan artwork, so the image reads as
    /// full-bleed rather than as a pasted square.
    private let brandBackdrop = Color.black

    var body: some View {
        ZStack {
            brandBackdrop
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
                            // Fixed, not adaptive: this always sits on the black
                            // brand backdrop, so an adaptive token would turn the
                            // glyph near-black on black in light mode.
                            .foregroundStyle(Color.white.opacity(0.65), Color.white.opacity(0.15))
                    }
                    .buttonStyle(PlainButtonStyle())
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.lg)

                Spacer()
            }
        }
        // `.environment(\.colorScheme, .dark)`, NOT `.preferredColorScheme(.dark)`.
        //
        // `preferredColorScheme` is a PREFERENCE — it propagates UP to the
        // enclosing presentation and can retheme the whole window, which would
        // fight `AppearanceManager` and the root modifier in iosApp.swift. The
        // environment value flows DOWN only, which is all that is wanted here:
        // light ink on this screen's black brand backdrop.
        .environment(\.colorScheme, .dark)
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
}

#Preview("Slogan") {
    CaydexSloganView()
}
