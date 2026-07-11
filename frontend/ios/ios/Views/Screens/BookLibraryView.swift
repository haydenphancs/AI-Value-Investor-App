//
//  BookLibraryView.swift
//  ios
//
//  Book Library View - Gamified Curriculum with progress tracking
//

import SwiftUI

struct BookLibraryView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var audioManager: AudioManager
    @ObservedObject private var progress = BookProgressStore.shared
    @ObservedObject private var bookmarks = BookmarkStore.shared
    @State private var books: [LibraryBook] = []
    @State private var selectedBook: LibraryBook?

    private var orderedBooks: [LibraryBook] {
        books.sorted { $0.curriculumOrder < $1.curriculumOrder }
    }

    private var masteredCount: Int {
        books.filter { progress.isMastered(order: $0.curriculumOrder, totalCores: $0.chapterCount) }.count
    }

    private var totalCount: Int {
        books.count
    }

    private var progressPercentage: Double {
        guard totalCount > 0 else { return 0 }
        return Double(masteredCount) / Double(totalCount)
    }

    /// The most-recently bookmarked book resolved to its LibraryBook (or nil).
    private var bookmarkedBook: LibraryBook? {
        guard let title = bookmarks.mostRecent else { return nil }
        return books.first(where: { $0.title == title })
    }

    /// "Core N · <title>" for the bookmarked book's first UNFINISHED core — the resume position.
    /// Follows curriculum order: stays on the earliest incomplete core even if a later one is
    /// finished out of order, and advances only once that core itself is completed.
    private var bookmarkedCoreLabel: String? {
        guard let book = bookmarkedBook else { return nil }
        // A fully-mastered book has no "first unfinished core" — resumeCore returns the last
        // (completed) core, so surfacing it as a resume target is wrong. Show completion instead.
        if progress.isMastered(order: book.curriculumOrder, totalCores: book.chapterCount) {
            return "All cores complete"
        }
        let n = progress.resumeCore(order: book.curriculumOrder, totalCores: book.chapterCount)
        if let coreTitle = book.coreChapters.first(where: { $0.number == n })?.title, !coreTitle.isEmpty {
            return "Core \(n) · \(coreTitle)"
        }
        return "Core \(n)"
    }

    var body: some View {
        ZStack {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main content
            VStack(spacing: 0) {
                // Header with back button and title
                BookLibraryHeader(onBackTapped: {
                    dismiss()
                })

                // Scrollable content
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.lg) {
                        // Hero Progress Card
                        ProgressDashboardCard(
                            masteredCount: masteredCount,
                            totalCount: totalCount,
                            progressPercentage: progressPercentage,
                            bookmarkedBookTitle: bookmarks.mostRecent,
                            bookmarkedCoreLabel: bookmarkedCoreLabel,
                            onOpenBookmarked: openBookmarkedBook
                        )
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.sm)

                        // Curriculum Label
                        HStack {
                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                Text("Your Curriculum")
                                    .font(AppTypography.heading)
                                    .foregroundColor(AppColors.textPrimary)

                                Text("Complete in order for maximum learning")
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textSecondary)
                            }
                            Spacer()
                        }
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.md)

                        // Book List
                        ForEach(orderedBooks) { book in
                            LibraryBookCard(
                                book: book,
                                isMastered: progress.isMastered(order: book.curriculumOrder, totalCores: book.chapterCount),
                                isBookmarked: bookmarks.isBookmarked(book.title),
                                onChatWithBook: { handleChatWithBook(book) },
                                onToggleBookmark: { bookmarks.toggle(book.title) },
                                onReview: { handleReview(book) }
                            )
                            .padding(.horizontal, AppSpacing.lg)
                            .onTapGesture {
                                selectedBook = book
                            }
                        }

                        // Bottom padding for safe area
                        Color.clear.frame(height: AppSpacing.xxxl)
                    }
                }
            }
        }
        .navigationBarHidden(true)
        .onAppear {
            loadBooks()
        }
        .task {
            // Pull server-side progress + bookmarks and union them into the local caches (best-effort).
            await progress.hydrate()
            await bookmarks.hydrate()
        }
        .fullScreenCover(item: $selectedBook) { book in
            BookDetailView(book: book)
                .environmentObject(audioManager)
        }
    }

    private func loadBooks() {
        books = LibraryBook.sampleData
    }

    /// Open the most-recently bookmarked book (the hero-card shortcut). No-op if none.
    private func openBookmarkedBook() {
        guard let title = bookmarks.mostRecent,
              let book = books.first(where: { $0.title == title }) else { return }
        selectedBook = book
    }

    private func handleChatWithBook(_ book: LibraryBook) {
        print("Chat with book: \(book.title)")
    }

    private func handleReview(_ book: LibraryBook) {
        print("Review book: \(book.title)")
    }
}

// MARK: - Header
private struct BookLibraryHeader: View {
    var onBackTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Navigation bar
            HStack {
                Button(action: {
                    onBackTapped?()
                }) {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconMedium).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()
            }

            // Title section
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text("Book Library")
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textPrimary)

                Text("10 essential books to master value investing")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
        .padding(.bottom, AppSpacing.lg)
    }
}

// MARK: - Progress Dashboard Hero Card
private struct ProgressDashboardCard: View {
    let masteredCount: Int
    let totalCount: Int
    let progressPercentage: Double
    /// Most-recently bookmarked book title; when set, the card shows a tappable shortcut to it.
    let bookmarkedBookTitle: String?
    /// "Core N · <title>" the learner will resume in that book (first unfinished core, in order).
    let bookmarkedCoreLabel: String?
    var onOpenBookmarked: (() -> Void)?

    var body: some View {
        ZStack(alignment: .topTrailing) {
            // Background with gradient
            ZStack {
                // Base gradient (#11998E → #38EF7D)
                LinearGradient(
                    colors: [
                        Color(hex: "11998E"),
                        Color(hex: "38EF7D")
                    ],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )

                // Grainy texture overlay
                ProgressCardGrainyOverlay()

                // Subtle dark overlay for depth
                LinearGradient(
                    colors: [
                        Color.black.opacity(0.05),
                        Color.black.opacity(0.2)
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }

            // Content
            HStack(alignment: .center, spacing: AppSpacing.xl) {
                // Left: Text content
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    // Label
                    Text("YOUR PROGRESS")
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(.white.opacity(0.8))
                        .tracking(1.2)

                    // Main stat
                    VStack(alignment: .leading, spacing: AppSpacing.xs) {
                        Text("\(masteredCount) of \(totalCount)")
                            .font(AppTypography.titleHero)
                            .foregroundColor(.white)

                        Text("Books Mastered")
                            .font(AppTypography.headingSmall)
                            .foregroundColor(.white.opacity(0.9))
                    }

                    // A bookmarked-book shortcut when one exists; otherwise the motivational
                    // message. Same slot ⇒ the card keeps its size either way.
                    if let bookmarkedBookTitle {
                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            HStack(spacing: AppSpacing.xs) {
                                Image(systemName: "bookmark.fill")
                                    .font(AppTypography.iconTiny)

                                Text(bookmarkedBookTitle)
                                    .font(AppTypography.bodySmall).fontWeight(.semibold)
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.75)

                                Image(systemName: "chevron.right")
                                    .font(AppTypography.iconTiny)
                                    .opacity(0.8)
                            }

                            // The core the learner will resume — first unfinished core, in order.
                            if let bookmarkedCoreLabel {
                                Text(bookmarkedCoreLabel)
                                    .font(AppTypography.labelSmall)
                                    .foregroundColor(.white.opacity(0.8))
                                    .lineLimit(1)
                                    .truncationMode(.tail)
                            }
                        }
                        .foregroundColor(.white)
                    } else {
                        Text(motivationalMessage)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(.white.opacity(0.85))
                            .lineLimit(2)
                    }
                }
                // Fill the available width so the bookmarked-book + core lines run right up to
                // (just shy of) the progress ring, truncating with "…" only when genuinely too long.
                .frame(maxWidth: .infinity, alignment: .leading)

                // Right: Progress ring
                ProgressRingView(progress: progressPercentage)
                    .frame(width: 90, height: 90)
            }
            .padding(.horizontal, AppSpacing.xl)
            .padding(.top, AppSpacing.xl)
            // Extra room at the bottom so the bookmarked-book + resume-core lines have space.
            .padding(.bottom, AppSpacing.xxl)

            // Trophy badge
            if masteredCount > 0 {
                ProgressBadgePill(count: masteredCount)
                    .padding(AppSpacing.lg)
            }
        }
        // Hug the content (with the xl vertical padding above) instead of a fixed 16:9 ratio,
        // which left a large empty band below the text. Card height now tracks its content.
        .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
        // Whole card opens the bookmarked book; no-op when nothing is bookmarked.
        .contentShape(Rectangle())
        .onTapGesture { onOpenBookmarked?() }
    }

    private var motivationalMessage: String {
        switch masteredCount {
        case 0:
            return "Start your journey to financial wisdom"
        case 1...2:
            return "Great start! Keep the momentum going"
        case 3...5:
            return "You're building a solid foundation"
        case 6...8:
            return "Almost there! You're becoming an expert"
        case 9:
            return "One more book to complete mastery!"
        case 10:
            return "Congratulations! You've mastered all books"
        default:
            return "Keep learning and growing"
        }
    }
}

// MARK: - Progress Ring View
private struct ProgressRingView: View {
    let progress: Double

    var body: some View {
        ZStack {
            // Background ring
            Circle()
                .stroke(
                    Color.white.opacity(0.3),
                    lineWidth: 8
                )

            // Progress ring
            Circle()
                .trim(from: 0, to: progress)
                .stroke(
                    Color.white,
                    style: StrokeStyle(
                        lineWidth: 8,
                        lineCap: .round
                    )
                )
                .rotationEffect(.degrees(-90))
                .animation(.easeInOut(duration: 0.5), value: progress)

            // Percentage text
            VStack(spacing: 0) {
                Text("\(Int(progress * 100))%")
                    .font(AppTypography.title)
                    .foregroundColor(.white)

                Text("Complete")
                    .font(AppTypography.captionTiny).fontWeight(.medium)
                    .foregroundColor(.white.opacity(0.8))
            }
        }
    }
}

// MARK: - Progress Badge Pill
private struct ProgressBadgePill: View {
    let count: Int

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: "trophy.fill")
                .font(AppTypography.iconTiny).fontWeight(.semibold)

            Text("\(count) MASTERED")
                .font(AppTypography.captionEmphasis)
        }
        .foregroundColor(.white)
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.xs)
        .background(
            Capsule()
                .fill(Color.black.opacity(0.3))
                .overlay(
                    Capsule()
                        .strokeBorder(Color.white.opacity(0.3), lineWidth: 1)
                )
        )
    }
}

// MARK: - Grainy Texture Overlay for Progress Card
private struct ProgressCardGrainyOverlay: View {
    var body: some View {
        Canvas { context, size in
            for _ in 0..<Int(size.width * size.height / 50) {
                let x = CGFloat.random(in: 0..<size.width)
                let y = CGFloat.random(in: 0..<size.height)
                let opacity = Double.random(in: 0.02...0.08)

                context.fill(
                    Path(ellipseIn: CGRect(x: x, y: y, width: 1, height: 1)),
                    with: .color(.white.opacity(opacity))
                )
            }
        }
    }
}

#Preview {
    BookLibraryView()
        .environmentObject(AudioManager.shared)
        .preferredColorScheme(.dark)
}
