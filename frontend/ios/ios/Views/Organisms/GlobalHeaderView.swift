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

/// How the avatar is cut. The two are not interchangeable — see `ProfileAvatarView`.
enum ProfileAvatarShape {
    /// Shares `CaydexLogoMark.iconCornerRatio`. For a header, where the avatar sits across
    /// from the logo mark.
    case squircle
    /// A plain circle. For a standalone hero with no logo opposite it.
    case circle
}

/// Loads the user's external avatar URL. Falls back to a default silhouette icon.
///
/// The default is a ROUNDED SQUARE, not a circle, and only because of WHERE it usually sits:
/// in both headers the avatar is directly across from `CaydexLogoMark`, and a circle opposite
/// the icon-shaped logo read as two unrelated marks. It shares the logo's `iconCornerRatio` so
/// the pair keeps one silhouette at every size.
///
/// That argument does NOT extend to the Account screen's 80pt hero, which has no logo opposite
/// it — squaring it there was collateral from a header change. Pass `.circle` for a standalone
/// avatar.
struct ProfileAvatarView: View {
    let avatarUrl: String?
    var size: CGFloat = 36
    var shape: ProfileAvatarShape = .squircle

    /// `.circle` is `RoundedRectangle` at half the side — same shape, one code path, so the
    /// image clip and the fallback glyph can never disagree about which one is in use.
    private var cornerRadius: CGFloat {
        switch shape {
        case .squircle: return size * CaydexLogoMark.iconCornerRatio
        case .circle:   return size / 2
        }
    }

    var body: some View {
        if let urlString = avatarUrl, let url = URL(string: urlString) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(width: size, height: size)
                        .clipShape(
                            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                        )
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

    /// Follows `shape`. It has to: most users have no avatar image, so the fallback IS the
    /// avatar for them — a squared glyph under a `.circle` request would make the parameter
    /// look like it did nothing.
    private var fallbackAvatar: some View {
        Image(systemName: shape == .circle ? "person.crop.circle.fill" : "person.crop.square.fill")
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
/// the screen as a brand ident. The close button is pinned to fixed light ink
/// because it now always sits on black.
///
/// (This used to cite the launch screen as precedent, "pinned to a fixed dark colour
/// for the same reason". It is not pinned: `LaunchBackground.colorset` carries a real
/// light variant, #F4F5F8. The claim was stale and was being used to justify the hard
/// black here, so it is corrected rather than repeated.)
///
/// STATUS BAR: `.environment(\.colorScheme, .dark)` below styles the view tree and
/// NOTHING ELSE — the status bar is driven by the window's TRAIT, not by a SwiftUI
/// environment value. On a black backdrop in Light mode the clock and battery
/// therefore rendered dark-on-black. `.toolbarColorScheme(.dark, for: .navigationBar)`
/// does not reach it either (there is no navigation bar here), so the cover hides the
/// bar outright: nothing in this ident needs the time, and hiding is the one fix that
/// cannot be undone by `AppearanceManager.apply()` re-stamping the presented chain on
/// the next `didBecomeActive`.
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
                .accessibilityIgnoresInvertColors()
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
                            .foregroundStyle(AppColors.textOnAccent.opacity(0.65), AppColors.textOnAccent.opacity(0.15))
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
        .statusBarHidden(true)
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
