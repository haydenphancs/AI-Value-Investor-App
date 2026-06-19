//
//  BookDetailView.swift
//  ios
//
//  Book Detail View - Detailed view for each book in the library
//  Displays book information, audio playback, and content tabs
//

import SwiftUI

// MARK: - Book Detail Tab
enum BookDetailTab: String, CaseIterable {
    case core = "Core"
    case about = "About"
}

// MARK: - Book Detail View
struct BookDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var audioManager: AudioManager
    @ObservedObject private var progress = BookProgressStore.shared
    @ObservedObject private var bookmarks = BookmarkStore.shared
    @State private var selectedTab: BookDetailTab = .core
    @State private var showShareSheet: Bool = false
    @State private var scrollOffset: CGFloat = 0
    @State private var aiInputText: String = ""

    let book: LibraryBook

    /// Bookmark state for this book (BookmarkStore, keyed by title) — shared with every card.
    private var isBookmarked: Bool { bookmarks.isBookmarked(book.title) }

    // Computed property for header opacity based on scroll. Fades the sticky header in right as the
    // hero's own nav bar scrolls away (~50px), so a header is pinned throughout scrolling.
    private var headerOpacity: Double {
        let fadeStart: CGFloat = 70
        let fadeEnd: CGFloat = 150
        if scrollOffset < fadeStart { return 0 }
        if scrollOffset > fadeEnd { return 1 }
        return Double((scrollOffset - fadeStart) / (fadeEnd - fadeStart))
    }

    var body: some View {
        ZStack(alignment: .top) {
            // Background
            AppColors.background
                .ignoresSafeArea()
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            // Main scrollable content
            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {
                    // Hero section with book cover and info
                    BookDetailHeroSection(
                        book: book,
                        onBackTapped: { dismiss() },
                        isBookmarked: isBookmarked,
                        onBookmarkTapped: { bookmarks.toggle(book.title) },
                        onShareTapped: { showShareSheet = true }
                    )

                    // Tab selector and content
                    VStack(spacing: 0) {
                        // Tab selector
                        BookDetailTabSelector(selectedTab: $selectedTab)
                            .padding(.top, AppSpacing.lg)

                        // Tab content
                        switch selectedTab {
                        case .about:
                            BookDetailAboutContent(book: book)
                        case .core:
                            BookDetailCoreContent(book: book)
                        }
                    }

                    // Bottom padding for Ask AI bar
                    Color.clear.frame(height: 100)
                }
                .background(
                    GeometryReader { proxy in
                        Color.clear
                            .preference(
                                key: BookDetailScrollOffsetKey.self,
                                value: -proxy.frame(in: .named("bookDetailScroll")).origin.y
                            )
                    }
                )
            }
            .coordinateSpace(name: "bookDetailScroll")
            .onPreferenceChange(BookDetailScrollOffsetKey.self) { value in
                scrollOffset = value
            }

            // Sticky mini header — always present, fading in as the hero scrolls away so a header is
            // pinned at the top throughout scrolling (invisible + non-interactive at the very top, so
            // the hero's own nav bar receives taps there).
            BookDetailMiniHeader(
                book: book,
                isBookmarked: isBookmarked,
                onBackTapped: { dismiss() },
                onBookmarkTapped: { bookmarks.toggle(book.title) },
                onShareTapped: { showShareSheet = true }
            )
            .opacity(headerOpacity)
            .allowsHitTesting(headerOpacity > 0.5)

            // Bottom Ask AI bar
            VStack {
                Spacer()

                // Global Mini Player (for fullScreenCover presentation)
                if audioManager.hasActiveEpisode && !audioManager.showFullScreenPlayer {
                    GlobalMiniPlayer()
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }

                CaydexAIChatBar(inputText: $aiInputText)
            }
            .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.hasActiveEpisode)

            // Full Screen Player (modal overlay) — presented here because this screen is shown as a
            // fullScreenCover above RootContainerView, whose own full-screen player would be hidden.
            if audioManager.showFullScreenPlayer {
                FullScreenAudioPlayer()
                    .transition(.move(edge: .bottom))
                    .zIndex(100)
            }
        }
        .animation(.spring(response: 0.4, dampingFraction: 0.85), value: audioManager.showFullScreenPlayer)
        .navigationBarHidden(true)
        .sheet(isPresented: $showShareSheet) {
            if let url = URL(string: "https://app.example.com/book/\(book.id)") {
                ShareSheet(items: [book.title, "by \(book.author)", url])
            }
        }
        .onDisappear {
            // Reset scroll hiding when leaving to ensure main screen player shows
            audioManager.resetScrollHiding()
        }
        .onChange(of: audioManager.currentTime) { oldTime, newTime in
            // Auto-complete cores (the numbered badges) as the narration plays through them, so the
            // learner doesn't have to tap "Complete & Continue" while listening.
            guard audioManager.currentEpisode?.id == book.audioEpisode.id,
                  let info = book.bookAudioInfo else { return }
            var newly: [Int] = []
            withAnimation(.spring(response: 0.3, dampingFraction: 0.7)) {
                newly = progress.markListenedThrough(
                    order: book.curriculumOrder, from: oldTime, to: newTime,
                    coreStarts: info.coreStartSeconds, totalSeconds: info.totalSeconds)
            }
            if !newly.isEmpty {
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            }
        }
    }
}

// MARK: - Scroll Offset Preference Key
private struct BookDetailScrollOffsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

// MARK: - Hero Section
private struct BookDetailHeroSection: View {
    @EnvironmentObject private var audioManager: AudioManager
    let book: LibraryBook
    let onBackTapped: () -> Void
    let isBookmarked: Bool
    let onBookmarkTapped: () -> Void
    let onShareTapped: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            // Navigation header
            HStack {
                Button(action: onBackTapped) {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconMedium).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 44, height: 44)
                }

                Spacer()

                HStack(spacing: AppSpacing.md) {
                    Button(action: onBookmarkTapped) {
                        Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                            .font(AppTypography.iconMedium).fontWeight(.medium)
                            .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textPrimary)
                            .frame(width: 44, height: 44)
                    }

                    Button(action: onShareTapped) {
                        Image(systemName: "square.and.arrow.up")
                            .font(AppTypography.iconMedium).fontWeight(.medium)
                            .foregroundColor(AppColors.textPrimary)
                            .frame(width: 44, height: 44)
                    }
                }
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.top, AppSpacing.sm)

            // Book cover
            BookDetailCoverImage(book: book)
                .padding(.top, AppSpacing.xl)
                .padding(.bottom, AppSpacing.xxl)

            // Title and author
            VStack(spacing: AppSpacing.sm) {
                Text(book.title)
                    .font(AppTypography.title)
                    .foregroundColor(AppColors.textPrimary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)

                Text(book.author)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
            }
            .padding(.horizontal, AppSpacing.lg)

            // Category badges
            BookDetailBadgeRow(book: book)
                .padding(.top, AppSpacing.lg)

            // Listen Now row
            BookDetailListenRow(book: book)
                .padding(.top, AppSpacing.xl)
                .padding(.horizontal, AppSpacing.lg)
        }
    }
}

// MARK: - Book Cover Image
private struct BookDetailCoverImage: View {
    let book: LibraryBook

    var body: some View {
        ZStack {
            // Shadow layer
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(
                    LinearGradient(
                        colors: [
                            Color(hex: book.coverGradientStart),
                            Color(hex: book.coverGradientEnd)
                        ],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
                .frame(width: 160, height: 220)
                .shadow(color: Color(hex: book.coverGradientStart).opacity(0.4), radius: 20, y: 10)

            // Book cover
            ZStack {
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(
                        LinearGradient(
                            colors: [
                                Color(hex: book.coverGradientStart),
                                Color(hex: book.coverGradientEnd)
                            ],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )

                // Book spine effect
                HStack(spacing: 0) {
                    Rectangle()
                        .fill(Color.black.opacity(0.15))
                        .frame(width: 4)
                    Spacer()
                }

                // Book title on cover
                VStack(spacing: AppSpacing.sm) {
                    Text(book.title.uppercased())
                        .font(AppTypography.bodySmall).fontWeight(.bold)
                        .foregroundColor(.white)
                        .multilineTextAlignment(.center)
                        .lineLimit(4)
                        .padding(.horizontal, AppSpacing.lg)

                    Rectangle()
                        .fill(Color.white.opacity(0.3))
                        .frame(width: 40, height: 1)

                    Text(book.author.uppercased())
                        .font(AppTypography.captionTiny).fontWeight(.medium)
                        .foregroundColor(.white.opacity(0.8))
                        .tracking(1)
                }
            }
            .frame(width: 160, height: 220)
        }
    }
}

// MARK: - Badge Row
private struct BookDetailBadgeRow: View {
    let book: LibraryBook

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Level badge
            BookDetailBadge(
                text: book.level.rawValue,
                color: book.level.color
            )

            // Separator
            Text("|")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            // Chapters badge
            BookDetailBadge(
                text: book.formattedChapters,
                color: AppColors.accentCyan
            )

            // Separator
            Text("|")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            // Primary category tag
            if let firstTag = book.categoryTags.first {
                BookDetailBadge(
                    text: firstTag.rawValue,
                    color: AppColors.accentCyan
                )
            }
        }
    }
}

// MARK: - Badge Component
private struct BookDetailBadge: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(AppTypography.bodySmallEmphasis)
            .foregroundColor(color)
    }
}

// MARK: - Listen Row
private struct BookDetailListenRow: View {
    @EnvironmentObject private var audioManager: AudioManager
    @ObservedObject private var progress = BookProgressStore.shared
    let book: LibraryBook

    // Check if this is the current book being played (any core)
    private var isCurrentBookPlaying: Bool {
        guard let currentEpisode = audioManager.currentEpisode else { return false }
        return currentEpisode.sourceId == book.id.uuidString && audioManager.isPlaying
    }

    // Check if this book's narration is currently playing (one file for the whole book)
    private var isResumeCorePlaying: Bool {
        guard let currentEpisode = audioManager.currentEpisode else { return false }
        return currentEpisode.id == bookAudioEpisode.id && audioManager.isPlaying
    }

    // User has progress if they've completed at least one core.
    private var hasProgress: Bool {
        progress.hasProgress(order: book.curriculumOrder)
    }

    // The core to resume from: the first one they haven't finished yet.
    private var resumeCoreNumber: Int {
        progress.resumeCore(order: book.curriculumOrder, totalCores: book.chapterCount)
    }

    // The whole book plays as ONE narration file; we just resume at the right core's start offset.
    private var bookAudioEpisode: AudioEpisode { book.audioEpisode }

    private var resumeStartSeconds: TimeInterval {
        TimeInterval(book.coreStartSeconds(resumeCoreNumber) ?? 0)
    }

    // Button label based on state
    private var buttonLabel: String {
        if isResumeCorePlaying {
            return "Now Playing"
        } else if hasProgress {
            return "Continue Core \(resumeCoreNumber)"
        } else {
            return "Listen Now"
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            // Play button
            Button(action: handlePlayTapped) {
                HStack(spacing: AppSpacing.md) {
                    ZStack {
                        Circle()
                            .fill(AppColors.textPrimary)
                            .frame(width: 48, height: 48)

                        Image(systemName: isResumeCorePlaying ? "pause.fill" : "play.fill")
                            .font(AppTypography.iconMedium).fontWeight(.bold)
                            .foregroundColor(AppColors.background)
                            .offset(x: isResumeCorePlaying ? 0 : 2)
                    }

                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text(buttonLabel)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)

                        Text(book.formattedAudioDuration)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            .buttonStyle(PlainButtonStyle())

            Spacer()

            // Stats
            VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                HStack(spacing: AppSpacing.md) {
                    // Read time
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "clock")
                            .font(AppTypography.iconXS).fontWeight(.medium)
                            .foregroundColor(AppColors.textMuted)

                        Text(book.formattedReadTime)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }

                    // Views
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "eye")
                            .font(AppTypography.iconXS).fontWeight(.medium)
                            .foregroundColor(AppColors.textMuted)

                        Text(book.formattedViewCount)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }

                // Date
                Text(book.formattedLastUpdated)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    private func handlePlayTapped() {
        if isResumeCorePlaying {
            audioManager.togglePlayPause()
        } else {
            // Play the whole-book narration, seeking to the resume core's start (Core 1 = 0:00).
            audioManager.play(bookAudioEpisode, startAt: resumeStartSeconds)
        }
    }
}

// MARK: - Tab Selector
private struct BookDetailTabSelector: View {
    @Binding var selectedTab: BookDetailTab

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            ForEach(BookDetailTab.allCases, id: \.self) { tab in
                Button(action: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedTab = tab
                    }
                }) {
                    Text(tab.rawValue)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(selectedTab == tab ? AppColors.textPrimary : AppColors.textMuted)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            selectedTab == tab
                                ? AppColors.cardBackgroundLight
                                : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.pill)
                }
                .buttonStyle(PlainButtonStyle())
            }

            Spacer()
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - About Content
private struct BookDetailAboutContent: View {
    let book: LibraryBook

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Why this book section
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                Text("Why this book?")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Text(book.whyThisBook)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
            }

            // Author section
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                Text("The Author")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                BookDetailAuthorCard(author: book.authorDetail)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.xl)
    }
}

// MARK: - Author Card
private struct BookDetailAuthorCard: View {
    let author: BookAuthor

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Author header
            HStack(spacing: AppSpacing.md) {
                // Avatar
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: author.avatarGradientColors.map { Color(hex: $0) },
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 56, height: 56)

                    Text(author.name.prefix(1))
                        .font(AppTypography.title)
                        .foregroundColor(.white)
                }

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(author.name)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text(author.title)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }

            // Bio
            Text(author.bio)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Core Content
private struct BookDetailCoreContent: View {
    let book: LibraryBook
    @State private var selectedChapter: BookCoreChapter?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Core Chapters Section (with progress tracking)
            CoreChaptersSection(
                chapters: book.coreChapters,
                curriculumOrder: book.curriculumOrder,
                onChapterTapped: { chapter in
                    selectedChapter = chapter
                }
            )

            // Discussion Section
            DiscussionSection(discussions: book.discussions)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.xl)
        .fullScreenCover(item: $selectedChapter) { chapter in
            if let content = chapter.getDetailContent(for: book) {
                BookCoreDetailView(content: content, book: book)
                    .environmentObject(AudioManager.shared)
            }
        }
    }
}

// MARK: - Key Highlights Section
private struct KeyHighlightsSection: View {
    let highlights: [BookKeyHighlight]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Key Highlights")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                ForEach(highlights) { highlight in
                    KeyHighlightCard(highlight: highlight)
                }
            }
        }
    }
}

// MARK: - Key Highlight Card
private struct KeyHighlightCard: View {
    let highlight: BookKeyHighlight

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Icon
            ZStack {
                Circle()
                    .fill(Color(hex: highlight.iconColor).opacity(0.15))
                    .frame(width: 40, height: 40)

                Image(systemName: highlight.iconName)
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(Color(hex: highlight.iconColor))
            }

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(highlight.title)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(highlight.description)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(2)
            }

            Spacer(minLength: 0)
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - Core Chapters Section (Timeline Style)
private struct CoreChaptersSection: View {
    @ObservedObject private var progress = BookProgressStore.shared
    let chapters: [BookCoreChapter]
    let curriculumOrder: Int
    var onChapterTapped: ((BookCoreChapter) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Timeline layout
            VStack(spacing: 0) {
                ForEach(Array(chapters.enumerated()), id: \.element.id) { index, chapter in
                    CoreChapterTimelineRow(
                        chapter: chapter,
                        startTimeLabel: startTimeLabel(for: chapter),
                        isLast: index == chapters.count - 1,
                        isCompleted: progress.isCompleted(order: curriculumOrder, core: chapter.number),
                        onTapped: {
                            onChapterTapped?(chapter)
                        }
                    )
                }
            }
        }
    }

    /// Where this core starts within the single book narration ("M:SS"), or nil if the book has
    /// no narration yet. Shown under the core number in the timeline.
    private func startTimeLabel(for chapter: BookCoreChapter) -> String? {
        guard let secs = BookAudioInfo.byOrder[curriculumOrder]?.coreStartSeconds[chapter.number] else {
            return nil
        }
        return String(format: "%d:%02d", secs / 60, secs % 60)
    }
}

// MARK: - Core Chapter Timeline Row
private struct CoreChapterTimelineRow: View {
    let chapter: BookCoreChapter
    var startTimeLabel: String? = nil
    let isLast: Bool
    let isCompleted: Bool
    var onTapped: (() -> Void)?

    private let completedColor = Color(hex: "14B8A6") // Teal color for completed
    private let uncompletedColor = Color(hex: "2DD4BF").opacity(0.5) // Muted color for outline
    private let lineColor = Color(hex: "2DD4BF").opacity(0.5) // Subtle dark line
    private let badgeSize: CGFloat = 32

    var body: some View {
        Button(action: { onTapped?() }) {
            HStack(alignment: .top, spacing: AppSpacing.lg) {
                // Timeline column: number badge, its start timestamp, then the connecting line.
                VStack(spacing: AppSpacing.xxs) {
                    // Number badge - filled or outline based on completion
                    ZStack {
                        if isCompleted {
                            // Filled badge for completed/current chapters
                            Circle()
                                .fill(completedColor)
                                .frame(width: badgeSize, height: badgeSize)
                        } else {
                            // Outline-only badge for unread chapters
                            Circle()
                                .strokeBorder(uncompletedColor, lineWidth: 1)
                                .frame(width: badgeSize, height: badgeSize)
                        }

                        Text("\(chapter.number)")
                            .font(AppTypography.label).fontWeight(.bold)
                            .foregroundColor(isCompleted ? .white : uncompletedColor)
                    }
                    .frame(width: badgeSize, height: badgeSize)

                    // Where this core starts in the book narration (e.g. "2:37")
                    if let startTimeLabel {
                        Text(startTimeLabel)
                            .font(AppTypography.captionTiny)
                            .monospacedDigit()
                            .foregroundColor(isCompleted ? completedColor : AppColors.textMuted)
                    }

                    // Connecting line fills the remaining height down to the next badge
                    if !isLast {
                        Rectangle()
                            .fill(lineColor)
                            .frame(width: 1)
                            .frame(maxHeight: .infinity)
                    }
                }
                .frame(width: 44)

                // Content column
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    HStack {
                        Text(chapter.title)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)

                        Spacer()

                        // Chevron indicator
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconXS).fontWeight(.semibold)
                            .foregroundColor(AppColors.textMuted)
                    }

                    Text(chapter.description)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                .padding(.bottom, isLast ? 0 : AppSpacing.xxl)
            }
        }
        .buttonStyle(PlainButtonStyle())
    }
}

// MARK: - Discussion Section
private struct DiscussionSection: View {
    let discussions: [BookDiscussion]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            HStack {
                Text("Discussion")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {}) {
                    Text("See All")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.accentCyan)
                }
                .buttonStyle(PlainButtonStyle())
            }

            // Discussion cards
            VStack(spacing: AppSpacing.md) {
                ForEach(discussions) { discussion in
                    DiscussionCard(discussion: discussion)
                }
            }
        }
    }
}

// MARK: - Discussion Card
private struct DiscussionCard: View {
    let discussion: BookDiscussion

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header with avatar, name, date, and rating
            HStack(spacing: AppSpacing.md) {
                // Avatar
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: discussion.authorAvatarGradient.map { Color(hex: $0) },
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 36, height: 36)

                    Text(discussion.authorName.prefix(1))
                        .font(AppTypography.bodySmall).fontWeight(.bold)
                        .foregroundColor(.white)
                }

                // Name and date
                VStack(alignment: .leading, spacing: 2) {
                    Text(discussion.authorName)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text(discussion.formattedDate)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                // Star rating
                HStack(spacing: 2) {
                    ForEach(1...5, id: \.self) { star in
                        Image(systemName: star <= discussion.rating ? "star.fill" : "star")
                            .font(AppTypography.iconTiny)
                            .foregroundColor(star <= discussion.rating ? Color(hex: "F59E0B") : AppColors.textMuted)
                    }
                }
            }

            // Content
            Text(discussion.content)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Mini Header
private struct BookDetailMiniHeader: View {
    let book: LibraryBook
    let isBookmarked: Bool
    let onBackTapped: () -> Void
    let onBookmarkTapped: () -> Void
    let onShareTapped: () -> Void

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Back button
            Button(action: onBackTapped) {
                Image(systemName: "chevron.left")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
            }
            .buttonStyle(PlainButtonStyle())

            // Title
            Text(book.title)
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)

            Spacer()

            // Actions
            HStack(spacing: AppSpacing.lg) {
                Button(action: onBookmarkTapped) {
                    Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                        .font(AppTypography.iconDefault).fontWeight(.medium)
                        .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                Button(action: onShareTapped) {
                    Image(systemName: "square.and.arrow.up")
                        .font(AppTypography.iconDefault).fontWeight(.medium)
                        .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(
            AppColors.background
                .shadow(color: Color.black.opacity(0.2), radius: 4, y: 2)
        )
    }
}

// MARK: - Safe Array Subscript
private extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

// MARK: - Preview
#Preview {
    @Previewable @StateObject var audioManager = AudioManager.shared

    BookDetailView(book: LibraryBook.sampleData[0])
        .environmentObject(audioManager)
        .preferredColorScheme(.dark)
}
