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
    @State private var searchText = ""
    @State private var books: [LibraryBook] = []
    @State private var selectedBook: LibraryBook?

    private var filteredBooks: [LibraryBook] {
        if searchText.isEmpty {
            return books.sorted { $0.curriculumOrder < $1.curriculumOrder }
        }
        return books.filter { book in
            book.title.localizedCaseInsensitiveContains(searchText) ||
            book.author.localizedCaseInsensitiveContains(searchText)
        }.sorted { $0.curriculumOrder < $1.curriculumOrder }
    }

    private var masteredCount: Int {
        books.filter { $0.isMastered }.count
    }

    private var totalCount: Int {
        books.count
    }

    private var progressPercentage: Double {
        guard totalCount > 0 else { return 0 }
        return Double(masteredCount) / Double(totalCount)
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

                // Sticky search bar
                BookLibrarySearchBar(searchText: $searchText)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.md)

                // Scrollable content
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.lg) {
                        // Hero Progress Card
                        ProgressDashboardCard(
                            masteredCount: masteredCount,
                            totalCount: totalCount,
                            progressPercentage: progressPercentage
                        )
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.sm)

                        // Curriculum Label
                        HStack {
                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                Text("Your Curriculum")
                                    .font(AppTypography.title3)
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
                        ForEach(filteredBooks) { book in
                            LibraryBookCard(
                                book: book,
                                onChatWithBook: { handleChatWithBook(book) },
                                onReadKeyIdeas: { handleReadKeyIdeas(book) },
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
        .fullScreenCover(item: $selectedBook) { book in
            BookDetailView(book: book)
                .environmentObject(audioManager)
        }
    }

    private func loadBooks() {
        books = LibraryBook.sampleData
    }

    private func handleChatWithBook(_ book: LibraryBook) {
        print("Chat with book: \(book.title)")
    }

    private func handleReadKeyIdeas(_ book: LibraryBook) {
        print("Read key ideas: \(book.title)")
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
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()
            }

            // Title section
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text("Book Library")
                    .font(AppTypography.largeTitle)
                    .foregroundColor(AppColors.textPrimary)

                Text("10 essential books to master value investing")
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
        .padding(.bottom, AppSpacing.lg)
    }
}

// MARK: - Search Bar
private struct BookLibrarySearchBar: View {
    @Binding var searchText: String

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(AppColors.textMuted)

            TextField("Search by title or author...", text: $searchText)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .autocapitalization(.none)
                .disableAutocorrection(true)

            if !searchText.isEmpty {
                Button(action: {
                    searchText = ""
                }) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 16))
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Progress Dashboard Hero Card
private struct ProgressDashboardCard: View {
    let masteredCount: Int
    let totalCount: Int
    let progressPercentage: Double

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
                        .font(AppTypography.captionBold)
                        .foregroundColor(.white.opacity(0.8))
                        .tracking(1.2)

                    // Main stat
                    VStack(alignment: .leading, spacing: AppSpacing.xs) {
                        Text("\(masteredCount) of \(totalCount)")
                            .font(.system(size: 32, weight: .bold))
                            .foregroundColor(.white)

                        Text("Books Mastered")
                            .font(AppTypography.headline)
                            .foregroundColor(.white.opacity(0.9))
                    }

                    // Motivational message
                    Text(motivationalMessage)
                        .font(AppTypography.callout)
                        .foregroundColor(.white.opacity(0.85))
                        .lineLimit(2)
                }

                Spacer()

                // Right: Progress ring
                ProgressRingView(progress: progressPercentage)
                    .frame(width: 90, height: 90)
            }
            .padding(.horizontal, AppSpacing.xl)
            .padding(.vertical, AppSpacing.xl)

            // Trophy badge
            if masteredCount > 0 {
                ProgressBadgePill(count: masteredCount)
                    .padding(AppSpacing.lg)
            }
        }
        .aspectRatio(16/9, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
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
                    .font(.system(size: 22, weight: .bold))
                    .foregroundColor(.white)

                Text("Complete")
                    .font(.system(size: 9, weight: .medium))
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
                .font(.system(size: 10, weight: .semibold))

            Text("\(count) MASTERED")
                .font(AppTypography.captionBold)
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
