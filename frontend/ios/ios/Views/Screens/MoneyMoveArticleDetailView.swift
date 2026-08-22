//
//  MoneyMoveArticleDetailView.swift
//  ios
//
//  Full article detail screen for Money Move articles
//  Displays hero header, content sections, statistics, comments, and related articles
//  Integrates with AudioManager for audio playback
//

import SwiftUI
import Combine

struct MoneyMoveArticleDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var audioManager: AudioManager
    @ObservedObject private var bookmarks = MoneyMoveBookmarkStore.shared
    @State private var audioCompletionCancellable: AnyCancellable?
    @State private var isFollowing: Bool = false
    @State private var showShareSheet: Bool = false
    @State private var scrollOffset: CGFloat = 0
    @State private var contentHeight: CGFloat = 0
    @State private var viewportHeight: CGFloat = 0
    @State private var aiInputText: String = ""
    /// Stable token keying this screen's compact-mode requests + audio overlay host registration.
    @State private var compactToken = UUID().uuidString
    /// Owns the chat conversation for this article so it resumes while the screen is open.
    @StateObject private var chatViewModel = ChatViewModel()
    @State private var showAIChat = false

    let article: MoneyMoveArticle

    /// Set when the reader follows a Related Articles card. Tapping SWAPS the article in
    /// place rather than presenting another cover: this screen is itself a `fullScreenCover`,
    /// so pushing a second one would nest presentations without bound, each keeping its own
    /// scroll and audio state. `nil` means "showing the article we were opened with".
    @State private var current: MoneyMoveArticle?

    /// The article actually on screen. Everything below reads this, never `article`.
    private var shown: MoneyMoveArticle { current ?? article }

    /// Saved state for the article ON SCREEN, or nil when it cannot be saved.
    ///
    /// Reads `shown`, not `article`: a Related-articles tap swaps the article in place, and
    /// keying this off `article` would leave the button reporting the topic the reader opened
    /// with. Nil for an empty slug — `createArticleFromMove` builds placeholder articles for
    /// unauthored cards and leaves `slug` blank, so there is no id to save under.
    private var bookmarkState: Bool? {
        shown.slug.isEmpty ? nil : bookmarks.isBookmarked(slug: shown.slug)
    }

    private func handleBookmarkTapped() {
        guard !shown.slug.isEmpty else { return }
        bookmarks.toggle(slug: shown.slug)
    }

    /// Convert article to AudioEpisode for playback
    private var audioEpisode: AudioEpisode {
        AudioEpisode(
            id: "article-\(shown.id)",
            title: shown.title,
            subtitle: shown.subtitle,
            artworkGradientColors: shown.heroGradientColors,
            artworkIcon: shown.category.iconName,
            duration: TimeInterval(shown.audioDurationSeconds ?? shown.readTimeMinutes * 60),
            category: .moneyMoves,
            authorName: shown.author.name,
            sourceId: shown.id.uuidString,
            audioUrl: shown.audioUrl
        )
    }

    /// Follow a Related Articles card. Resolves by title because `relatedArticles` entries
    /// carry no slug. A title with no article is ignored rather than clearing the screen —
    /// the content is server-driven, so a card can outlive the article it points at.
    private func openRelated(_ related: RelatedArticle) {
        guard let next = MoneyMovesContentStore.shared.article(forTitle: related.title),
              next.id != shown.id else { return }
        // Audio deliberately keeps playing: it is a global episode with its own mini player,
        // and `readAlongActiveTime` already returns nil once the episode no longer matches,
        // so the new article simply renders without read-along highlighting.
        current = next
        // ⚠️ Reset both hero edges to the offscreen sentinel. `.id(shown.id)` rebuilds the
        // scroll view so the new article opens at the top, but THIS screen's @State survives
        // the swap — without the reset, the outgoing article's (already scrolled past)
        // measurements would flash the mini header over the incoming article's hero.
        heroNavBottom = .greatestFiniteMagnitude
        heroTitleBottom = .greatestFiniteMagnitude
    }

    /// The narration playhead (seconds) when THIS article's audio is the active episode, else nil
    /// — drives per-sentence read-along highlighting. Mirrors BookCoreDetailView.readAlongActiveTime.
    private var readAlongActiveTime: Double? {
        guard audioManager.currentEpisode?.id == audioEpisode.id else { return nil }
        return audioManager.currentTime
    }

    /// Ramp length, in points of travel. One mini-bar height, which is what makes the
    /// crossfade read like the system's large-title → inline-title transition.
    private static let headerFadeDistance: CGFloat = 44

    /// Bottom edge of the hero's back/share row, reported in `MoneyMoveArticleSpace.screen`.
    ///
    /// ⚠️ `.greatestFiniteMagnitude`, not 0, is load-bearing. `fade(0) == 1`, so a
    /// default-zero `@State` would render the mini header fully opaque on top of an
    /// unscrolled hero for the frame before the first measurement lands.
    @State private var heroNavBottom: CGFloat = .greatestFiniteMagnitude

    /// Bottom edge of the hero's title. Same sentinel rule.
    @State private var heroTitleBottom: CGFloat = .greatestFiniteMagnitude

    /// 0 while `bottom` is below the bar, 1 once it has travelled a full ramp past the top.
    ///
    /// The `isFinite` guard is not decoration: `min`/`max` propagate NaN rather than absorbing
    /// it (Swift's are `>=`-based, and every comparison with NaN is false), so a single bad
    /// geometry reading would reach `.opacity(nan)`. Non-finite means "we have no usable
    /// measurement", and the safe reading of that is the same as offscreen — 0, hidden.
    private func fade(_ bottom: CGFloat) -> Double {
        guard bottom.isFinite else { return 0 }
        return Double(min(max(1 - bottom / Self.headerFadeDistance, 0), 1))
    }

    /// Gates the sticky bar itself. Ramps as the hero's OWN back button leaves, so there is
    /// never a stretch of article with no back affordance — the old fixed 200→280 window left
    /// ~150pt of exactly that, and keying the bar off the title instead would have widened it.
    private var chromeOpacity: Double { fade(heroNavBottom) }

    /// Gates only the sticky bar's title text. Reaches 1 exactly as the hero title's last line
    /// clears the top edge, so no scroll position shows two readable titles at once.
    ///
    /// This replaces a hardcoded 200→280 scroll window inherited from the old 380pt full-bleed
    /// hero. Under the current layout the title starts around 309pt on a 402pt device — but
    /// that number is not a constant to re-tune: artwork height is (width − 32) × 9/16 and
    /// AppTypography scales to 1.4×, so the title's top swings ~130pt across device widths and
    /// text sizes. That is wider than the entire fade window, i.e. any fixed pair is wrong for
    /// most (width × Dynamic Type) combinations. Measure it instead.
    private var titleOpacity: Double { fade(heroTitleBottom) }

    /// Fraction (0...1) of the article scrolled through — drives the top reading-progress bar.
    private var readingProgress: Double {
        let scrollable = contentHeight - viewportHeight
        guard scrollable > 0 else { return 0 }
        return Double(min(max(scrollOffset / scrollable, 0), 1))
    }

    var body: some View {
        ZStack {
            // Main content layer
            ZStack(alignment: .top) {
                // Background
                AppColors.background
                    .ignoresSafeArea()

                // Main scrollable content
                ScrollView(showsIndicators: false) {
                    VStack(spacing: 0) {
                        // Hero header
                        MoneyMoveArticleHeroHeader(
                            article: shown,
                            audioEpisode: audioEpisode,
                            onBackTapped: handleBackTapped,
                            onShareTapped: handleShareTapped,
                            isBookmarked: bookmarkState,
                            onBookmarkTapped: handleBookmarkTapped,
                            onNavBottomChange: { heroNavBottom = $0 },
                            onTitleBottomChange: { heroTitleBottom = $0 }
                        )

                        // Content
                        MoneyMoveArticleContent(
                            article: shown,
                            activeTime: readAlongActiveTime,
                            onRelatedTapped: openRelated
                        )
                        .padding(.top, AppSpacing.lg)

                        // Bottom padding (extra space for mini player). Completion is now an
                        // explicit toggle at the end of the article (and on narration finish),
                        // so reaching the end no longer auto-marks it read.
                        Color.clear
                            .frame(height: audioManager.hasActiveEpisode ? 120 : 40)
                    }
                    // Changing identity on swap gives the new article a FRESH scroll view, so it
                    // opens at the top instead of inheriting the previous article's offset
                    // mid-paragraph. Also rebinds read-along and the completion toggle.
                    .id(shown.id)
                }
                // Drive the sticky header + reading-progress bar straight from the scroll
                // view's live geometry (iOS 18+). More reliable than a GeometryReader +
                // PreferenceKey offset, which wasn't propagating in this layout.
                .onScrollGeometryChange(for: ScrollMetrics.self) { geo in
                    ScrollMetrics(
                        offset: geo.contentOffset.y + geo.contentInsets.top,
                        contentHeight: geo.contentSize.height,
                        viewportHeight: geo.containerSize.height
                    )
                } action: { _, metrics in
                    scrollOffset = metrics.offset
                    contentHeight = metrics.contentHeight
                    viewportHeight = metrics.viewportHeight
                }

                // Sticky mini header (appears on scroll)
                if chromeOpacity > 0 {
                    miniHeader
                        .opacity(chromeOpacity)
                        .zIndex(10)
                }

                // Reading-progress bar — pinned at the very top, fills as you scroll.
                VStack(spacing: 0) {
                    readingProgressBar
                    Spacer(minLength: 0)
                }
                .allowsHitTesting(false)
                .zIndex(11)
            }
            // ⚠️ This space MUST be established here — on the ZStack that hosts the mini
            // header — and NOT on the outer ZStack or on anything carrying .ignoresSafeArea().
            // Those extend above the status bar, so every edge the hero reports would come
            // back ~59pt larger and both ramps would fire a full bar-height early. It would
            // look almost right, which is the worst way for this to be wrong.
            .coordinateSpace(.named(MoneyMoveArticleSpace.screen))

            // Bottom bar: Mini Player + AI Chat
            VStack(spacing: 0) {
                Spacer()

                // Bottom mini player — hidden when collapsed to the top island (chat-bar focused).
                if audioManager.hasActiveEpisode && !audioManager.showFullScreenPlayer && !audioManager.isCompactMode {
                    GlobalMiniPlayer(bottomSpacing: AppSpacing.md / 2)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }

                // Tapping the chat bar collapses the player to the top status island (Wiser-only).
                CaydexAIChatBar(
                    inputText: $aiInputText,
                    onSend: {
                        let text = aiInputText.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !text.isEmpty else { return }
                        aiInputText = ""
                        // Backend fetches the article by slug — no client context string.
                        chatViewModel.startNewConversation(
                            firstMessage: text,
                            contextType: .moneyMovesArticle,
                            referenceId: shown.slug
                        )
                        // Release the focus-driven compact reason deterministically (the covered
                        // TextField's focus-off event is unreliable). AIChatScreen's own forceCompact
                        // keeps audio in the DI while the chat is open; on close this screen's mini
                        // player returns instead of staying stuck hidden.
                        audioManager.setCompactMode(false, reason: compactToken)
                        showAIChat = true
                    },
                    onFocusChange: { focused in
                        audioManager.setCompactMode(focused, reason: compactToken)
                    }
                )
            }
        }
        // Top status island + full-screen player + overlay-host registration (this screen is a
        // fullScreenCover above RootContainerView, whose own overlay would be hidden).
        .globalAudioOverlay(token: compactToken)
        .navigationBarHidden(true)
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
        .animation(.spring(response: 0.35, dampingFraction: 0.85), value: audioManager.hasActiveEpisode)
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.isCompactMode)
        .onAppear {
            // Finishing the narration also completes the article.
            audioCompletionCancellable = audioManager.playbackDidComplete
                .receive(on: DispatchQueue.main)
                .sink { completed in
                    if completed.id == audioEpisode.id {
                        MoneyMovesProgressStore.shared.markCompleted(slug: shown.slug)
                    }
                }
        }
        .onDisappear {
            audioCompletionCancellable?.cancel()
            audioCompletionCancellable = nil
        }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: [shown.title, shown.subtitle])
        }
        // Narration is Pro/Max: the audio ENGINE refuses a locked episode and asks for
        // an upgrade, so this presenter is what turns that into the plan sheet. Needed on
        // each screen because these are fullScreenCovers — a modifier on the presenter
        // does not reach them.
        .learnAudioPaywall()
    }

    // MARK: - Mini Header

    /// The sticky bar that replaces the hero chrome on scroll.
    ///
    /// ⚠️ Its chips are filled with `textPrimary.opacity(0.15)`, NOT `cardBackground` — this
    /// deliberately does NOT match the hero header, despite an older comment here claiming it
    /// "mirrors the hero header's capsule style". The hero's capsules sit on
    /// `AppColors.background`, where `cardBackground` separates cleanly. These sit on
    /// `.ultraThinMaterial`, where `cardBackground` disappears in BOTH appearances: its light
    /// arm (#FFFFFF) is the light frost, and its dark arm (#1E2330) is within a hair of the
    /// dark frost. A scrim chip derived from an adaptive ink is dark-on-light-frost and
    /// light-on-dark-frost by construction, and it survives Reduce Transparency (where the
    /// material collapses to an opaque fill). This is the codebase's established chip-on-
    /// material idiom — see LessonStoryProgressBar, FullScreenAudioPlayer, PortfolioHeaderBar.
    ///
    /// Not a token: `ThemeContrastAudit` measures every token against a fixed list of surfaces,
    /// and a material is not — cannot be — on that list. A `chipOnMaterial` token would have to
    /// declare a surface it does not sit on, which a launch guard would then certify as correct.
    private var miniHeader: some View {
        HStack(spacing: AppSpacing.md) {
            Button(action: handleBackTapped) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                    Text("Back")
                        .font(AppTypography.body)
                }
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    Capsule()
                        .fill(AppColors.textPrimary.opacity(0.15))
                )
            }
            .buttonStyle(PlainButtonStyle())

            // Title — on its OWN ramp, keyed to the hero title rather than the hero chrome.
            // The bar itself arrives ~250pt earlier; if the title came with it, it would sit
            // opaque over the still-visible hero title with only translucent material between.
            Text(shown.title)
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)
                .opacity(titleOpacity)

            Spacer(minLength: AppSpacing.sm)

            // Same scrim-chip reasoning as the back capsule above — deliberately NOT the hero's
            // `cardBackground`, which disappears on `.ultraThinMaterial` in both appearances.
            if let isBookmarked = bookmarkState {
                Button(action: handleBookmarkTapped) {
                    Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                        .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textPrimary)
                        .frame(width: 36, height: 36)
                        .background(
                            Circle()
                                .fill(AppColors.textPrimary.opacity(0.15))
                        )
                }
                .buttonStyle(PlainButtonStyle())
                .accessibilityLabel(isBookmarked ? "Remove saved topic" : "Save this topic")
            }

            Button(action: handleShareTapped) {
                Image(systemName: "square.and.arrow.up")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 36, height: 36)
                    .background(
                        Circle()
                            .fill(AppColors.textPrimary.opacity(0.15))
                    )
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.lg)
        // Top kept slightly larger than bottom so the row reads as vertically centered:
        // the reading-progress line overlaps the top edge and the drop shadow adds weight below.
        .padding(.top, AppSpacing.sm)
        .padding(.bottom, AppSpacing.xs)
        .background(.ultraThinMaterial)
        .shadow(color: AppColors.shadowAmbient, radius: 4, y: 2)
    }

    // MARK: - Reading Progress Bar

    /// Thin full-width line capping the top of the screen; fill tracks `readingProgress`.
    ///
    /// This is `ProgressBar` at `height: 2`, not a hand-rolled `ZStack{track;fill}` — four
    /// atoms already are that shape (ProgressBar, LevelProgressBar, StorageProgressBar,
    /// GradientProgressBar) and all four fill their track with `cardBackgroundLight`. The
    /// inline version here used a hardcoded `Color.white.opacity(0.12)`, which was correct
    /// only in dark: its comment claimed it "reads well over the orange hero", but that hero
    /// stopped existing when the headline moved off the artwork, so in light mode it was
    /// white-on-#F4F5F8 — an invisible rail.
    ///
    /// The track is deliberately decorative (1.05:1 light, 1.22:1 dark against the page).
    /// The FILL carries the meaning and is measured: `primaryBlue` is 4.7:1 light / 8.8:1
    /// dark. Same posture the palette already takes for chart gridlines.
    private var readingProgressBar: some View {
        ProgressBar(progress: readingProgress, height: 2, showPercentage: false)
            .animation(.linear(duration: 0.1), value: readingProgress)
    }

    // MARK: - Action Handlers

    private func handleBackTapped() {
        dismiss()
    }

    private func handleShareTapped() {
        showShareSheet = true
    }

    private func handleFollowTapped() {
        withAnimation(.spring(response: 0.3)) {
            isFollowing.toggle()
        }
    }

    private func handleAuthorTapped() {
        print("Navigate to author profile: \(shown.author.name)")
    }
}

// MARK: - Scroll Metrics

/// Snapshot of the scroll view's live geometry, fed to the sticky header + progress bar.
/// `offset` is normalized to 0 at the resting top; `contentHeight - viewportHeight` is the
/// total scrollable distance.
private struct ScrollMetrics: Equatable {
    let offset: CGFloat
    let contentHeight: CGFloat
    let viewportHeight: CGFloat
}

// MARK: - Preview

#Preview {
    MoneyMoveArticleDetailView(article: MoneyMoveArticle.sampleDigitalFinance)
        .environmentObject(AudioManager.shared)
}
