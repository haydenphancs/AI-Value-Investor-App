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

    /// Convert article to AudioEpisode for playback
    private var audioEpisode: AudioEpisode {
        AudioEpisode(
            id: "article-\(article.id)",
            title: article.title,
            subtitle: article.subtitle,
            artworkGradientColors: article.heroGradientColors,
            artworkIcon: article.category.iconName,
            duration: TimeInterval(article.audioDurationSeconds ?? article.readTimeMinutes * 60),
            category: .moneyMoves,
            authorName: article.author.name,
            sourceId: article.id.uuidString,
            audioUrl: article.audioUrl
        )
    }

    /// The narration playhead (seconds) when THIS article's audio is the active episode, else nil
    /// — drives per-sentence read-along highlighting. Mirrors BookCoreDetailView.readAlongActiveTime.
    private var readAlongActiveTime: Double? {
        guard audioManager.currentEpisode?.id == audioEpisode.id else { return nil }
        return audioManager.currentTime
    }

    // Computed property for header opacity based on scroll
    private var headerOpacity: Double {
        let fadeStart: CGFloat = 200
        let fadeEnd: CGFloat = 280
        if scrollOffset < fadeStart { return 0 }
        if scrollOffset > fadeEnd { return 1 }
        return Double((scrollOffset - fadeStart) / (fadeEnd - fadeStart))
    }

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
                            article: article,
                            audioEpisode: audioEpisode,
                            onBackTapped: handleBackTapped,
                            onShareTapped: handleShareTapped
                        )

                        // Content
                        MoneyMoveArticleContent(article: article, activeTime: readAlongActiveTime)
                        .padding(.top, AppSpacing.lg)

                        // Bottom padding (extra space for mini player). Completion is now an
                        // explicit toggle at the end of the article (and on narration finish),
                        // so reaching the end no longer auto-marks it read.
                        Color.clear
                            .frame(height: audioManager.hasActiveEpisode ? 120 : 40)
                    }
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
                if headerOpacity > 0 {
                    miniHeader
                        .opacity(headerOpacity)
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

            // Bottom bar: Mini Player + AI Chat
            VStack(spacing: 0) {
                Spacer()

                // Bottom mini player — hidden when collapsed to the top island (chat-bar focused).
                if audioManager.hasActiveEpisode && !audioManager.showFullScreenPlayer && !audioManager.isCompactMode {
                    GlobalMiniPlayer()
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
                            referenceId: article.slug
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
        .preferredColorScheme(.dark)
        .animation(.spring(response: 0.35, dampingFraction: 0.85), value: audioManager.hasActiveEpisode)
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.isCompactMode)
        .onAppear {
            // Finishing the narration also completes the article.
            audioCompletionCancellable = audioManager.playbackDidComplete
                .receive(on: DispatchQueue.main)
                .sink { completed in
                    if completed.id == audioEpisode.id {
                        MoneyMovesProgressStore.shared.markCompleted(slug: article.slug)
                    }
                }
        }
        .onDisappear {
            audioCompletionCancellable?.cancel()
            audioCompletionCancellable = nil
        }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: [article.title, article.subtitle])
        }
    }

    // MARK: - Mini Header

    private var miniHeader: some View {
        HStack(spacing: AppSpacing.md) {
            // Back button — mirrors the hero header's capsule style
            Button(action: handleBackTapped) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                    Text("Back")
                        .font(AppTypography.body)
                }
                .foregroundColor(.white)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    Capsule()
                        .fill(Color.white.opacity(0.15))
                )
            }
            .buttonStyle(PlainButtonStyle())

            // Title
            Text(article.title)
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(.white)
                .lineLimit(1)

            Spacer(minLength: AppSpacing.sm)

            // Share button — mirrors the hero header's circular style
            Button(action: handleShareTapped) {
                Image(systemName: "square.and.arrow.up")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(.white)
                    .frame(width: 36, height: 36)
                    .background(
                        Circle()
                            .fill(Color.white.opacity(0.15))
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
        .shadow(color: Color.black.opacity(0.15), radius: 4, y: 2)
    }

    // MARK: - Reading Progress Bar

    /// Thin full-width line capping the top of the screen; fill tracks `readingProgress`.
    /// Reads well over both the orange hero (top of article) and the dark sticky bar.
    private var readingProgressBar: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Rectangle()
                    .fill(Color.white.opacity(0.12))
                Rectangle()
                    .fill(AppColors.primaryBlue)
                    .frame(width: geo.size.width * CGFloat(readingProgress))
            }
        }
        .frame(height: 2)
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
        print("Navigate to author profile: \(article.author.name)")
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
