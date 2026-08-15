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
    // NOTE: this screen deliberately does NOT observe MoneyMovesProgressStore. It used to, so
    // the rows could re-sort as completed moves slid to the end; ordering is purely by date
    // now, so the observation drove nothing but extra body passes. Each MoneyMoveCard observes
    // the store itself, so completion checkmarks still update live.
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
                            moves: newestFirst(blueprints),
                            onMoveTap: handleMoveTap
                        )

                        // Section 2: Value Traps
                        MoneyMovesCategorySection(
                            category: .valueTraps,
                            moves: newestFirst(valueTraps),
                            onMoveTap: handleMoveTap
                        )

                        // Section 3: Battles
                        MoneyMovesCategorySection(
                            category: .battles,
                            moves: newestFirst(battles),
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
        // Narration is Pro/Max: the audio ENGINE refuses a locked episode and asks for
        // an upgrade, so this presenter is what turns that into the plan sheet. Needed on
        // each screen because these are fullScreenCovers — a modifier on the presenter
        // does not reach them.
        .learnAudioPaywall()
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

    /// Newest first, via the SHARED sorter — the Wiser row and this screen must never drift
    /// into disagreeing about the order of the very same cards.
    ///
    /// This wrapped `newestFirst` in an unread-first partition until the Wiser section was
    /// relabelled "Most Recent"; see `LearnViewModel.newestFirst` for why that partition went.
    private func newestFirst(_ moves: [MoneyMove]) -> [MoneyMove] {
        LearnViewModel.newestFirst(moves)
    }

    private func handleMoveTap(_ move: MoneyMove) {
        // Resolve by slug first (canonical id — a shared title can't open the wrong article), then
        // by title, then fall back to generated placeholder content for cards not yet authored.
        let store = MoneyMovesContentStore.shared
        selectedArticle = store.article(forSlug: move.slug)
            ?? store.article(forTitle: move.title)
            ?? createArticleFromMove(move)
    }

    /// Creates a full MoneyMoveArticle from a MoneyMove card data
    private func createArticleFromMove(_ move: MoneyMove) -> MoneyMoveArticle {
        // Was an inline switch duplicated verbatim in LearnView — see
        // MoneyMoveCategory.gradientColors.
        let gradientColors = move.category.gradientColors

        return MoneyMoveArticle(
            title: move.title,
            subtitle: move.subtitle,
            category: move.category,
            author: ArticleAuthor(
                name: "Caydex Research",
                avatarName: nil,
                title: "Editorial",
                isVerified: false,
                followerCount: ""
            ),
            publishedAt: Date(),
            readTimeMinutes: move.estimatedMinutes,
            // No invented engagement metrics. `stableCount` made the fake comment count
            // consistent across taps, which fixed the flicker but not the fabrication.
            viewCount: "",
            commentCount: 0,
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
            // Read Time is the only truthfully measurable statistic here.
            statistics: [
                ArticleStatistic(value: "\(move.estimatedMinutes)m", label: "Read Time")
            ],
            comments: [],
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
//
// THE TYPE SITS BELOW THE ARTWORK, for the same reason it does in MoneyMoveArticleHeroHeader.
// This card used to draw the title, subtitle, meta row and tag pill over the image behind a
// black 0.1 -> 0.5 scrim. That was survivable over the flat orange gradient it always used,
// and is not survivable over cover artwork: eight of the thirteen plates have a near-white
// ground, and clearing 4.5:1 for white type over a 0.99-luminance plate needs roughly 0.82
// alpha of black — which does not dim the picture, it erases it.
//
// `ArticleTagPill(.standard)` moved down with the rest: it is white text on `black.opacity(0.4)`,
// which measures about 1.6:1 on a light plate.
private struct FeaturedDeepDiveHeroCard: View {
    let article: MoneyMoveArticle
    var onTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            MoneyMoveCoverImage(
                url: article.imageUrl,
                // Falls back to the article's OWN authored gradient now. The previous card
                // hardcoded flat orange for every featured article and ignored
                // `heroGradientColors` entirely, so the hero disagreed with the article it
                // opened — visible the moment you tapped it.
                gradientColors: article.heroGradientColors,
                cornerRadius: AppCornerRadius.large,
                aspectRatio: 16 / 9
            )
            .padding(.horizontal, AppSpacing.md)

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                HStack(spacing: AppSpacing.sm) {
                    Text("FEATURED DEEP DIVE")
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(AppColors.caution)
                        .tracking(1.2)

                    if let tagLabel = article.tagLabel {
                        ArticleTagPill(text: tagLabel, style: .featured)
                    }
                }

                Text(article.title)
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textPrimary)
                    .minimumScaleFactor(0.8)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Text(article.subtitle)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(nil)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: AppSpacing.lg) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "clock")
                            .font(AppTypography.iconXS).fontWeight(.medium)
                        Text("\(article.readTimeMinutes) min")
                            .font(AppTypography.caption)
                    }

                    HStack(spacing: AppSpacing.xs) {
                        // Only shown when we have a real count. Empty means we don't
                        // measure it — better to show nothing than to invent a number.
                        if !article.viewCount.isEmpty {
                            Image(systemName: "person.2.fill")
                                .font(AppTypography.iconXS).fontWeight(.medium)
                            Text("\(article.viewCount) investors")
                                .font(AppTypography.caption)
                        }

                        if article.hasAudioVersion {
                            Image(systemName: "headphones")
                                .font(AppTypography.iconXS).fontWeight(.medium)
                                .padding(.leading, article.viewCount.isEmpty ? 0 : AppSpacing.md)
                        }
                    }
                }
                .foregroundColor(AppColors.textSecondary)
                .padding(.top, AppSpacing.xs)
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.bottom, AppSpacing.md)
        }
        // The hero is a CARD, not loose page content. Once the headline moved off the artwork
        // the block had nothing holding it together — the plate and its type just floated on
        // the page background, indistinguishable from the category rows below. `.cardSurface()`
        // is the app's standard container and does the right thing per appearance on its own:
        // light gets `cardEdge` (a #FFFFFF card on the #F4F5F8 page is 1.09:1, so light cannot
        // separate them by luminance), dark draws no border at all and separates by fill.
        .padding(.top, AppSpacing.md)
        .cardSurface(cornerRadius: AppCornerRadius.extraLarge)
        .contentShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
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
                HStack(spacing: AppSpacing.sm) {
                    ForEach(moves) { move in
                        MoneyMoveCard(
                            moneyMove: move,
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
                    .fill(category.iconFillColor)
                    .frame(width: 36, height: 36)

                Image(systemName: category.iconName)
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(category.iconFillInk)
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
}
