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

    var searchPlaceholder: String = "Search"
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

            // Center: search (tickers only — the "or ask Cay AI" half moved out to the button
            // below, and the search screen dropped its chat entry with it).
            TappableSearchBar(
                placeholder: searchPlaceholder,
                onTap: onSearchTapped
            )

            // The global Cay AI door, its own card so it reads as a control rather than as an
            // ornament on the field.
            //
            // Routed through `AppState` rather than a closure parameter so the four headers that
            // embed this view (Home / Updates / Tracking / Learn) need no signature change and no
            // binding threaded down from `ContentView`, which owns the cover. Same reasoning as
            // `requestSignIn`. Research is deliberately excluded — `ResearchHeader` renders a
            // title instead of a search bar and the tab has its own AI surface.
            AskCayAIButton { appState.requestAIChat() }

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
        .globalHeaderRowHeight()
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .fullScreenCover(isPresented: $showSloganSheet) {
            CaydexSloganView()
        }
    }
}

// MARK: - Shared Header Row Height

/// One row height for EVERY tab header, so the header does not move between tabs.
///
/// Four of the five headers embed `GlobalHeaderView`, whose row is driven by
/// `TappableSearchBar` (~42pt). `ResearchHeader` has no search bar — just a logo, a mark and the
/// avatar — so its tallest element was a 36pt icon and its row came out several points shorter.
/// Switching to Research pulled the whole header up, and switching away dropped it back down;
/// the segmented control underneath moved with it. Nothing in either file named a height, so
/// there was nothing to notice.
///
/// `@ScaledMetric` rather than a plain constant because the search bar grows with Dynamic Type:
/// a fixed 44 would hold the rows level at the default text size and let them drift apart at
/// accessibility sizes, which is the same bug one step further out.
struct GlobalHeaderRowHeight: ViewModifier {
    @ScaledMetric(relativeTo: .subheadline) private var minHeight: CGFloat = 44

    func body(content: Content) -> some View {
        content.frame(minHeight: minHeight)
    }
}

extension View {
    /// Pin a tab header's top row to the shared height. Apply to the row's `HStack`, BEFORE its
    /// padding — otherwise the padding is inside the measured height and the rows differ again.
    func globalHeaderRowHeight() -> some View {
        modifier(GlobalHeaderRowHeight())
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
/// here needs to change (`BrandCoverQuote`'s fixed ink would then need the same revert).
///
/// QUOTE OF THE WEEK (TestFlight 1.0(6), 2026-09-23): beneath the slogan, one of 52 bundled,
/// primary-sourced investor quotes, chosen by ISO week (`WeeklyQuotePicker`) so a given week
/// shows the same quote every year. It is captured ONCE per presentation (`@State`), so a
/// re-render after Monday 00:00 cannot swap the text while it is being read. A real investor's
/// name now appears here, so this cover must stay out of App Store screenshots — launch with
/// `SIMCTL_CHILD_CAYDEX_QUOTE_WEEK=off` (DEBUG) for a clean capture.
struct CaydexSloganView: View {
    @Environment(\.dismiss) private var dismiss

    @State private var quote: InvestorQuote?

    init(quote: InvestorQuote? = BundledInvestorQuotes.quoteOfTheWeek()) {
        _quote = State(initialValue: quote)
    }

    /// Matches the baked background of the slogan artwork, so the image reads as
    /// full-bleed rather than as a pasted square.
    private let brandBackdrop = Color.black

    var body: some View {
        ZStack {
            brandBackdrop
                .ignoresSafeArea()

            VStack(spacing: AppSpacing.lg) {
                Image("CaydexSlogan")
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .accessibilityIgnoresInvertColors()
                    .accessibilityLabel("Caydex. Absorbing knowledge. Growing wealth.")

                // Priority 1: the QUOTE takes its ideal height first and the square art
                // takes what is left, so if space ever runs out (Bold Text, a raised type cap)
                // the art gives way, never the words. At today's 1.4x reading cap a 180-char
                // quote fits without shrinking the art on every supported iPhone.
                if let quote {
                    BrandCoverQuote(quote: quote)
                        .layoutPriority(1)
                }
            }
            .padding(.horizontal, AppSpacing.xxxl)
            .padding(.vertical, AppSpacing.xxxl)
            .frame(maxWidth: .infinity, maxHeight: .infinity)

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
                    .accessibilityLabel("Close")
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

/// The weekly quote on the brand cover. FILE-PRIVATE on purpose: its fixed `textOnAccent` ink
/// is only correct on `CaydexSloganView`'s permanently black backdrop — on an adaptive surface
/// it would be white on white in light mode (the Journey card has its own `InvestorQuoteCard`).
///
/// `Text(verbatim:)`, not `Text("…")`: the latter is a `LocalizedStringKey` and renders
/// Markdown, so a `*` or `_` inside a quotation would restyle it. The curly marks are added
/// here; the JSON text carries none. No `lineLimit`/`minimumScaleFactor`: a quotation is
/// never truncated or shrunk — the art above yields space instead (`layoutPriority`).
/// Contrast on #000: quote 0.90 ≈ 16.8:1, author 0.75 ≈ 11.4:1, citation 0.55 ≈ 6.2:1.
private struct BrandCoverQuote: View {
    let quote: InvestorQuote

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            Text(verbatim: "\u{201C}\(quote.text)\u{201D}")
                .font(AppTypography.body)
                .italic()
                .foregroundStyle(AppColors.textOnAccent.opacity(0.9))
                .lineSpacing(4)

            VStack(spacing: AppSpacing.xxs) {
                Text(verbatim: "\u{2014} \(quote.author)")
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundStyle(AppColors.textOnAccent.opacity(0.75))

                if let citation = quote.citation {
                    Text(verbatim: citation)
                        .font(AppTypography.caption)
                        .foregroundStyle(AppColors.textOnAccent.opacity(0.55))
                }
            }
        }
        .multilineTextAlignment(.center)
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(quote.accessibilityLabel)
    }
}

#Preview {
    VStack {
        GlobalHeaderView()
        GlobalHeaderView(searchPlaceholder: "Add tickers")
        Spacer()
    }
    .environment(AppState())
    .background(AppColors.background)
}

#Preview("Slogan") {
    CaydexSloganView()
}

#Preview("Slogan · longest quote") {
    CaydexSloganView(quote: BundledInvestorQuotes.all.max { $0.text.count < $1.text.count })
}

#Preview("Slogan · no quote") {
    CaydexSloganView(quote: nil)
}
