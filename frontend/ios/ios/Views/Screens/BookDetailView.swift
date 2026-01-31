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
    case about = "About"
    case core = "Core"
}

// MARK: - Book Detail View
struct BookDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var audioManager: AudioManager
    @State private var selectedTab: BookDetailTab = .about
    @State private var isBookmarked: Bool = false
    @State private var showShareSheet: Bool = false
    @State private var scrollOffset: CGFloat = 0

    let book: LibraryBook

    // Computed property for header opacity based on scroll
    private var headerOpacity: Double {
        let fadeStart: CGFloat = 280
        let fadeEnd: CGFloat = 360
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
                        onBookmarkTapped: { isBookmarked.toggle() },
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

            // Sticky mini header (appears on scroll)
            if headerOpacity > 0 {
                BookDetailMiniHeader(
                    book: book,
                    isBookmarked: isBookmarked,
                    onBackTapped: { dismiss() },
                    onBookmarkTapped: { isBookmarked.toggle() },
                    onShareTapped: { showShareSheet = true }
                )
                .opacity(headerOpacity)
            }

            // Bottom Ask AI bar
            VStack {
                Spacer()
                BookDetailAskAIBar()
            }
        }
        .navigationBarHidden(true)
        .sheet(isPresented: $showShareSheet) {
            if let url = URL(string: "https://app.example.com/book/\(book.id)") {
                ShareSheet(items: [book.title, "by \(book.author)", url])
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
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 44, height: 44)
                }

                Spacer()

                HStack(spacing: AppSpacing.md) {
                    Button(action: onBookmarkTapped) {
                        Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                            .font(.system(size: 18, weight: .medium))
                            .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textPrimary)
                            .frame(width: 44, height: 44)
                    }

                    Button(action: onShareTapped) {
                        Image(systemName: "square.and.arrow.up")
                            .font(.system(size: 18, weight: .medium))
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
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.white)
                        .multilineTextAlignment(.center)
                        .lineLimit(4)
                        .padding(.horizontal, AppSpacing.lg)

                    Rectangle()
                        .fill(Color.white.opacity(0.3))
                        .frame(width: 40, height: 1)

                    Text(book.author.uppercased())
                        .font(.system(size: 8, weight: .medium))
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
            .font(AppTypography.calloutBold)
            .foregroundColor(color)
    }
}

// MARK: - Listen Row
private struct BookDetailListenRow: View {
    @EnvironmentObject private var audioManager: AudioManager
    let book: LibraryBook

    private var isCurrentEpisode: Bool {
        audioManager.currentEpisode?.id == book.audioEpisode.id
    }

    private var isPlaying: Bool {
        isCurrentEpisode && audioManager.isPlaying
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

                        Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                            .font(.system(size: 18, weight: .bold))
                            .foregroundColor(AppColors.background)
                            .offset(x: isPlaying ? 0 : 2)
                    }

                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text(isPlaying ? "Now Playing" : "Listen Now")
                            .font(AppTypography.bodyBold)
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
                            .font(.system(size: 11, weight: .medium))
                            .foregroundColor(AppColors.textMuted)

                        Text(book.formattedReadTime)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }

                    // Views
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "eye")
                            .font(.system(size: 11, weight: .medium))
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
        if isCurrentEpisode {
            audioManager.togglePlayPause()
        } else {
            audioManager.play(book.audioEpisode)
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
                        .font(AppTypography.calloutBold)
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
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Text(book.whyThisBook)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
            }

            // Author section
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                Text("The Author")
                    .font(AppTypography.headline)
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
                        .font(.system(size: 22, weight: .bold))
                        .foregroundColor(.white)
                }

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(author.name)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    Text(author.title)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }

            // Bio
            Text(author.bio)
                .font(AppTypography.callout)
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

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Core Chapters Section (with progress tracking)
            CoreChaptersSection(chapters: book.coreChapters, currentChapter: book.currentChapter)

            // Discussion Section
            DiscussionSection(discussions: book.discussions)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.xl)
    }
}

// MARK: - Key Highlights Section
private struct KeyHighlightsSection: View {
    let highlights: [BookKeyHighlight]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Key Highlights")
                .font(AppTypography.headline)
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
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(Color(hex: highlight.iconColor))
            }

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(highlight.title)
                    .font(AppTypography.bodyBold)
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
    let chapters: [BookCoreChapter]
    let currentChapter: Int

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Timeline layout
            VStack(spacing: 0) {
                ForEach(Array(chapters.enumerated()), id: \.element.id) { index, chapter in
                    CoreChapterTimelineRow(
                        chapter: chapter,
                        isLast: index == chapters.count - 1,
                        isCompleted: chapter.number <= currentChapter
                    )
                }
            }
        }
    }
}

// MARK: - Core Chapter Timeline Row
private struct CoreChapterTimelineRow: View {
    let chapter: BookCoreChapter
    let isLast: Bool
    let isCompleted: Bool

    private let completedColor = Color(hex: "14B8A6") // Teal color for completed
    private let uncompletedColor = Color(hex: "3F4A5A") // Muted color for outline
    private let lineColor = Color(hex: "2A3441") // Subtle dark line
    private let badgeSize: CGFloat = 32

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.lg) {
            // Timeline column with badge and connecting line
            ZStack {
                // Connecting line (behind the badge, centered horizontally)
                if !isLast {
                    Rectangle()
                        .fill(lineColor)
                        .frame(width: 2)
                        .padding(.top, 16) // Start from center of badge
                }

                // Number badge - filled or outline based on completion
                VStack {
                    ZStack {
                        if isCompleted {
                            // Filled badge for completed/current chapters
                            Circle()
                                .fill(completedColor)
                                .frame(width: 32, height: 32)
                        } else {
                            // Outline-only badge for unread chapters
                            Circle()
                                .strokeBorder(uncompletedColor, lineWidth: 2)
                                .frame(width: 32, height: 32)
                        }

                        Text("\(chapter.number)")
                            .font(.system(size: 13, weight: .bold))
                            .foregroundColor(isCompleted ? .white : uncompletedColor)
                    }
                    Spacer()
                }
            }
            .frame(width: 32)

            // Content column
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text(chapter.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(chapter.description)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.bottom, isLast ? 0 : AppSpacing.xxl)
        }
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
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {}) {
                    Text("See All")
                        .font(AppTypography.calloutBold)
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
                        .font(.system(size: 14, weight: .bold))
                        .foregroundColor(.white)
                }

                // Name and date
                VStack(alignment: .leading, spacing: 2) {
                    Text(discussion.authorName)
                        .font(AppTypography.calloutBold)
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
                            .font(.system(size: 10))
                            .foregroundColor(star <= discussion.rating ? Color(hex: "F59E0B") : AppColors.textMuted)
                    }
                }
            }

            // Content
            Text(discussion.content)
                .font(AppTypography.callout)
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
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(AppColors.textPrimary)
            }
            .buttonStyle(PlainButtonStyle())

            // Title
            Text(book.title)
                .font(AppTypography.bodyBold)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)

            Spacer()

            // Actions
            HStack(spacing: AppSpacing.lg) {
                Button(action: onBookmarkTapped) {
                    Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                Button(action: onShareTapped) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.system(size: 16, weight: .medium))
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

// MARK: - Ask AI Bar
private struct BookDetailAskAIBar: View {
    @State private var inputText: String = ""

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // AI icon
            Image(systemName: "sparkles")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(AppColors.accentCyan)

            // Text field
            TextField("Ask Caudex AI...", text: $inputText)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)

            Spacer()

            // Send button
            Button(action: handleSend) {
                Image(systemName: "paperplane.fill")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(inputText.isEmpty ? AppColors.textMuted : AppColors.primaryBlue)
            }
            .buttonStyle(PlainButtonStyle())
            .disabled(inputText.isEmpty)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.extraLarge)
        .padding(.horizontal, AppSpacing.lg)
        .padding(.bottom, AppSpacing.lg)
        .background(
            LinearGradient(
                colors: [
                    AppColors.background.opacity(0),
                    AppColors.background.opacity(0.9),
                    AppColors.background
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
        )
    }

    private func handleSend() {
        guard !inputText.isEmpty else { return }
        print("Ask AI: \(inputText)")
        inputText = ""
    }
}

// MARK: - Preview
#Preview {
    BookDetailView(book: LibraryBook.sampleData[0])
        .environmentObject(AudioManager.shared)
}
