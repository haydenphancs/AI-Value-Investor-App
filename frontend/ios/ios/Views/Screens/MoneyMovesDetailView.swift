//
//  MoneyMovesDetailView.swift
//  ios
//
//  Money Moves Detail View - Full screen with hero card and categorized case studies
//  Serves as the main listing view for all Money Move articles
//

import SwiftUI

struct MoneyMovesDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var audioManager: AudioManager
    @State private var blueprints: [MoneyMove] = []
    @State private var valueTraps: [MoneyMove] = []
    @State private var battles: [MoneyMove] = []
    @State private var featured: MoneyMoveArticle?
    @State private var selectedArticle: MoneyMoveArticle?
    /// Observed so the rows re-sort live (completed moves slide to the end) when a move completes.
    @ObservedObject private var moneyMovesProgress = MoneyMovesProgressStore.shared
    /// Stable token keying this screen's audio overlay host registration.
    @State private var compactToken = UUID().uuidString

    var body: some View {
        ZStack {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main content
            VStack(spacing: 0) {
                // Header
                MoneyMovesDetailHeader(onBackTapped: {
                    dismiss()
                })

                // Scrollable content
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        // Hero Card - Featured Deep Dive. Dynamic: the isFeatured article served
                        // by the backend. Flipping isFeatured server-side swaps the hero (e.g. a
                        // weekly deep dive) with NO app update.
                        if let featured {
                            FeaturedDeepDiveHeroCard(
                                article: featured,
                                onTap: { selectedArticle = featured }
                            )
                            .padding(.horizontal, AppSpacing.lg)
                            .padding(.top, AppSpacing.md)
                        }

                        // Section 1: The Blueprints
                        MoneyMovesCategorySection(
                            category: .blueprints,
                            moves: incompleteFirst(blueprints),
                            onMoveTap: handleMoveTap
                        )

                        // Section 2: Value Traps
                        MoneyMovesCategorySection(
                            category: .valueTraps,
                            moves: incompleteFirst(valueTraps),
                            onMoveTap: handleMoveTap
                        )

                        // Section 3: Battles
                        MoneyMovesCategorySection(
                            category: .battles,
                            moves: incompleteFirst(battles),
                            onMoveTap: handleMoveTap
                        )

                        // Bottom padding for safe area
                        Color.clear.frame(height: AppSpacing.xxxl)
                    }
                }
            }
        }
        .navigationBarHidden(true)
        // Keep the audio player visible above this fullScreenCover (bottom mini player).
        .globalAudioOverlay(token: compactToken, showBottomMiniPlayer: true)
        .fullScreenCover(item: $selectedArticle) { article in
            MoneyMoveArticleDetailView(article: article)
                .environmentObject(audioManager)
        }
        .onAppear {
            loadSampleData()
        }
        .task {
            // Upgrade to fresh backend content (bundled content is already available
            // synchronously from the store's init), then rebuild the rows so any
            // server-side-only topics appear without an app update.
            await MoneyMovesContentStore.shared.prefetch()
            loadSampleData()
        }
        // Prevent accidental navigation gestures
        .interactiveDismissDisabled(false)
    }

    /// Build the card rows from authored content first (backend → bundled, served by
    /// MoneyMovesContentStore), then fill the rest with not-yet-authored placeholder
    /// cards. Adding an article server-side makes its card appear here with NO app
    /// update — the placeholders are only a fallback for unauthored topics.
    private func loadSampleData() {
        var cards = MoneyMovesContentStore.shared.cards()
        let authoredTitles = Set(cards.map { $0.title })
        cards += MoneyMove.sampleData.filter { !authoredTitles.contains($0.title) }

        featured = MoneyMovesContentStore.shared.featuredArticle()
        // Exclude ONLY the one card actually promoted to the hero (matched by slug). Filtering on
        // `isFeatured` instead would make a SECOND article flagged isFeatured (e.g. a new weekly
        // hero seeded before the old one is un-flagged) vanish entirely — not the hero, not in any
        // row. Keyed by the hero's slug, an extra featured article still shows in its category row.
        let heroSlug = featured?.slug ?? ""
        func isHero(_ move: MoneyMove) -> Bool { !heroSlug.isEmpty && move.slug == heroSlug }
        blueprints = cards.filter { $0.category == .blueprints && !isHero($0) }
        valueTraps = cards.filter { $0.category == .valueTraps && !isHero($0) }
        battles = cards.filter { $0.category == .battles && !isHero($0) }
    }

    /// Unread moves on the left, completed ones at the end (stable within each group). Recomputed
    /// each render; `moneyMovesProgress` observation makes the row re-sort live on completion.
    private func incompleteFirst(_ moves: [MoneyMove]) -> [MoneyMove] {
        let store = MoneyMovesProgressStore.shared
        return moves.filter { !store.isCompleted(slug: $0.slug) }
            + moves.filter { store.isCompleted(slug: $0.slug) }
    }

    private func handleMoveTap(_ move: MoneyMove) {
        // Resolve by slug first (canonical id — a shared title can't open the wrong article), then
        // by title, then fall back to generated placeholder content for cards not yet authored.
        let store = MoneyMovesContentStore.shared
        selectedArticle = store.article(forSlug: move.slug)
            ?? store.article(forTitle: move.title)
            ?? createArticleFromMove(move)
    }

    /// Stable pseudo-count in `range`, derived from a string (survives re-generation, unlike
    /// Int.random). A plain unicode-scalar sum — Swift's `hashValue` is per-run randomized.
    private static func stableCount(for key: String, in range: ClosedRange<Int>) -> Int {
        let span = range.upperBound - range.lowerBound + 1
        let sum = key.unicodeScalars.reduce(0) { ($0 &+ Int($1.value)) & 0x7fffffff }
        return range.lowerBound + (sum % span)
    }

    /// Creates a full MoneyMoveArticle from a MoneyMove card data
    private func createArticleFromMove(_ move: MoneyMove) -> MoneyMoveArticle {
        // Generate gradient colors based on category
        let gradientColors: [String]
        switch move.category {
        case .blueprints:
            gradientColors = ["059669", "047857", "064E3B"]
        case .valueTraps:
            gradientColors = ["DC2626", "991B1B", "7F1D1D"]
        case .battles:
            gradientColors = ["7C3AED", "5B21B6", "4C1D95"]
        }

        return MoneyMoveArticle(
            title: move.title,
            subtitle: move.subtitle,
            category: move.category,
            author: ArticleAuthor(
                name: "The Alpha",
                avatarName: nil,
                title: "Investment Research",
                isVerified: true,
                followerCount: "45.2k"
            ),
            publishedAt: Date(),
            readTimeMinutes: move.estimatedMinutes,
            viewCount: move.learnerCount,
            // Deterministic (not Int.random): the placeholder re-generates on every tap, so a random
            // count would flicker (e.g. "147 comments" then "58") above the 2 sample comments each
            // open. Derive a stable pseudo-count from the title instead.
            commentCount: Self.stableCount(for: move.title, in: 20...200),
            isBookmarked: false,
            hasAudioVersion: false,   // placeholder card: no narration audio (real articles carry audioUrl)
            heroGradientColors: gradientColors,
            tagLabel: move.category == .blueprints ? "BLUEPRINT" : (move.category == .valueTraps ? "CASE STUDY" : "VS"),
            isFeatured: false,
            keyHighlights: [
                ArticleHighlight(
                    icon: "lightbulb.fill",
                    title: "Key Insight",
                    description: "Understanding the core principles behind this investment case study."
                ),
                ArticleHighlight(
                    icon: "chart.line.uptrend.xyaxis",
                    title: "Market Impact",
                    description: "How this story influenced market dynamics and investor behavior."
                ),
                ArticleHighlight(
                    icon: "exclamationmark.triangle.fill",
                    title: "Lessons Learned",
                    description: "Critical takeaways for modern investors and portfolio managers."
                )
            ],
            sections: [
                ArticleSection(
                    title: "Overview",
                    icon: "doc.text.fill",
                    content: [
                        .paragraph("This case study explores the key factors that led to this notable investment story. Understanding these dynamics is crucial for making informed investment decisions in today's complex market environment."),
                        .paragraph("By analyzing the events, decisions, and market reactions, we can extract valuable lessons applicable to future investment opportunities and risk management strategies.")
                    ],
                    hasGlowEffect: true
                ),
                ArticleSection(
                    title: "Background & Context",
                    icon: "clock.fill",
                    content: [
                        .paragraph("To fully appreciate this case study, we must understand the market conditions and competitive landscape that shaped its trajectory."),
                        .callout(
                            icon: "info.circle.fill",
                            text: "The events discussed here occurred during a period of significant market transformation, making them particularly relevant for today's investors.",
                            style: .info
                        ),
                        .bulletList([
                            "Market conditions at the time",
                            "Key players and their motivations",
                            "Regulatory environment",
                            "Technological factors"
                        ])
                    ]
                ),
                ArticleSection(
                    title: "Key Takeaways",
                    icon: "star.fill",
                    content: [
                        .subheading("For Value Investors"),
                        .bulletList([
                            "Understanding market dynamics is essential for long-term success",
                            "Due diligence prevents costly mistakes and protects capital",
                            "Long-term thinking creates lasting value for shareholders",
                            "Risk management is non-negotiable in volatile markets"
                        ]),
                        .subheading("Practical Applications"),
                        .paragraph("These lessons can be directly applied to your investment process. Consider how each principle might have changed outcomes in your own portfolio decisions.")
                    ]
                ),
                ArticleSection(
                    title: "Conclusion",
                    icon: "checkmark.seal.fill",
                    content: [
                        .paragraph("This case study demonstrates the importance of fundamental analysis, proper due diligence, and maintaining a long-term perspective in investing."),
                        .callout(
                            icon: "quote.opening",
                            text: "The best investment you can make is in your own education and understanding of what drives business value.",
                            style: .highlight
                        )
                    ]
                )
            ],
            statistics: [
                ArticleStatistic(value: move.learnerCount, label: "Investors Learning", trend: .up, trendValue: "12%"),
                ArticleStatistic(value: "\(move.estimatedMinutes)m", label: "Read Time"),
                ArticleStatistic(value: "4.8", label: "Rating", trend: .up, trendValue: "0.3")
            ],
            comments: [
                ArticleComment(
                    authorName: "Michael Chen",
                    authorAvatar: nil,
                    content: "Excellent analysis! This really helped me understand the key factors at play.",
                    postedAt: Calendar.current.date(byAdding: .hour, value: -3, to: Date())!,
                    likeCount: 24,
                    replyCount: 5,
                    isVerified: false
                ),
                ArticleComment(
                    authorName: "Sarah Williams",
                    authorAvatar: nil,
                    content: "The section on risk management was particularly valuable. Would love to see more case studies like this.",
                    postedAt: Calendar.current.date(byAdding: .hour, value: -8, to: Date())!,
                    likeCount: 18,
                    replyCount: 2,
                    isVerified: true
                )
            ],
            relatedArticles: MoneyMoveArticle.sampleDigitalFinance.relatedArticles
        )
    }
}

// MARK: - Header
private struct MoneyMovesDetailHeader: View {
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
                Text("Money Moves")
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textPrimary)

                Text("Real-world case studies & deep dives")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
        .padding(.bottom, AppSpacing.lg)
    }
}

// MARK: - Featured Deep Dive Hero Card
private struct FeaturedDeepDiveHeroCard: View {
    let article: MoneyMoveArticle
    var onTap: (() -> Void)?

    private var gradientColors: [Color] {
        // Always use orange gradient based on EA580C
        [
            Color(hex: "F97316"),
            Color(hex: "EA580C"),
            Color(hex: "C2410C")
        ]
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            ZStack(alignment: .topTrailing) {
                // Background with gradient and effects
                ZStack {
                    // Base gradient
                    LinearGradient(
                        colors: gradientColors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )

                    // Grainy texture overlay
                    GrainyTextureOverlay()

                    // Dark overlay for text readability
                    LinearGradient(
                        colors: [
                            Color.black.opacity(0.1),
                            Color.black.opacity(0.5)
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                }

                // Content overlay
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Spacer()

                    // Category label
                    Text("FEATURED DEEP DIVE")
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(.white.opacity(0.8))
                        .tracking(1.2)

                    // Title
                    Text(article.title)
                        .font(AppTypography.titleLarge)
                        .foregroundColor(.white)
                        .minimumScaleFactor(0.8)
                        .lineLimit(2)

                    // Description
                    Text(article.subtitle)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(.white.opacity(0.9))
                        .lineLimit(nil)
                        .fixedSize(horizontal: false, vertical: true)

                    // Meta info
                    HStack(spacing: AppSpacing.lg) {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "clock")
                                .font(AppTypography.iconXS).fontWeight(.medium)
                            Text("\(article.readTimeMinutes) min")
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(.white.opacity(0.8))

                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "person.2.fill")
                                .font(AppTypography.iconXS).fontWeight(.medium)
                            Text("\(article.viewCount) investors")
                                .font(AppTypography.caption)

                            if article.hasAudioVersion {
                                Image(systemName: "headphones")
                                    .font(AppTypography.iconXS).fontWeight(.medium)
                                    .padding(.leading, AppSpacing.md)
                            }
                        }
                        .foregroundColor(.white.opacity(0.8))
                    }
                    .padding(.top, AppSpacing.xs)
                }
                .padding(AppSpacing.xl)
                .frame(maxWidth: .infinity, alignment: .leading)

                // Tag pill
                if let tagLabel = article.tagLabel {
                    ArticleTagPill(text: tagLabel)
                        .padding(AppSpacing.lg)
                }
            }
            .aspectRatio(16/9, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
        }
        .onTapGesture {
            onTap?()
        }
    }
}

// MARK: - Category Section
private struct MoneyMovesCategorySection: View {
    let category: MoneyMoveCategory
    let moves: [MoneyMove]
    var onMoveTap: ((MoneyMove) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            MoneyMovesCategorySectionHeader(category: category)
                .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll of cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ForEach(moves) { move in
                        MoneyMoveCard(
                            moneyMove: move,
                            showIcon: false,
                            onTap: { onMoveTap?(move) }
                        )
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

// MARK: - Category Section Header
private struct MoneyMovesCategorySectionHeader: View {
    let category: MoneyMoveCategory

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Icon with colored background
            ZStack {
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(category.iconBackgroundColor)
                    .frame(width: 36, height: 36)

                Image(systemName: category.iconName)
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(.white)
            }

            // Title and subtitle
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(category.rawValue)
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Text(category.tagline)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()
        }
    }
}



#Preview {
    MoneyMovesDetailView()
        .environmentObject(AudioManager.shared)
        .preferredColorScheme(.dark)
}
