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
    @State private var selectedArticle: MoneyMoveArticle?

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
                        // Hero Card - Featured Deep Dive
                        FeaturedDeepDiveHeroCard(
                            article: MoneyMoveArticle.sampleDigitalFinance,
                            onTap: {
                                // Create a special version with featured flag for the orange gradient
                                selectedArticle = MoneyMoveArticle.featuredDigitalFinance
                            }
                        )
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.md)

                        // Section 1: The Blueprints
                        MoneyMovesCategorySection(
                            category: .blueprints,
                            moves: blueprints,
                            onMoveTap: handleMoveTap,
                            onBookmark: handleBookmark
                        )

                        // Section 2: Value Traps
                        MoneyMovesCategorySection(
                            category: .valueTraps,
                            moves: valueTraps,
                            onMoveTap: handleMoveTap,
                            onBookmark: handleBookmark
                        )

                        // Section 3: Battles
                        MoneyMovesCategorySection(
                            category: .battles,
                            moves: battles,
                            onMoveTap: handleMoveTap,
                            onBookmark: handleBookmark
                        )

                        // Bottom padding for safe area
                        Color.clear.frame(height: AppSpacing.xxxl)
                    }
                }
            }
        }
        .navigationBarHidden(true)
        .fullScreenCover(item: $selectedArticle) { article in
            MoneyMoveArticleDetailView(article: article)
                .environmentObject(audioManager)
        }
        .onAppear {
            loadSampleData()
        }
        // Prevent accidental navigation gestures
        .interactiveDismissDisabled(false)
    }

    private func loadSampleData() {
        // Filter sample data by category
        let allMoves = MoneyMove.sampleData
        blueprints = allMoves.filter { $0.category == .blueprints }
        valueTraps = allMoves.filter { $0.category == .valueTraps }
        battles = allMoves.filter { $0.category == .battles }

        // Add more sample data for each category
        blueprints.append(contentsOf: [
            MoneyMove(
                title: "Apple's Services Revolution",
                subtitle: "How Apple transformed from hardware to ecosystem.",
                category: .blueprints,
                estimatedMinutes: 14,
                learnerCount: "1.6k",
                isBookmarked: false
            ),
            MoneyMove(
                title: "Costco's Membership Magic",
                subtitle: "The power of customer loyalty economics.",
                category: .blueprints,
                estimatedMinutes: 9,
                learnerCount: "1.2k",
                isBookmarked: true
            )
        ])

        valueTraps.append(contentsOf: [
            MoneyMove(
                title: "The FTX Collapse",
                subtitle: "Crypto's biggest fraud unraveled.",
                category: .valueTraps,
                estimatedMinutes: 18,
                learnerCount: "3.2k",
                isBookmarked: false
            ),
            MoneyMove(
                title: "Theranos: Blood & Lies",
                subtitle: "The $9 billion medical fraud.",
                category: .valueTraps,
                estimatedMinutes: 16,
                learnerCount: "2.8k",
                isBookmarked: false
            )
        ])

        battles.append(contentsOf: [
            MoneyMove(
                title: "Visa vs. Mastercard",
                subtitle: "The payment network duopoly.",
                category: .battles,
                estimatedMinutes: 12,
                learnerCount: "1.7k",
                isBookmarked: false
            ),
            MoneyMove(
                title: "Google vs. Microsoft: AI Wars",
                subtitle: "The battle for AI supremacy.",
                category: .battles,
                estimatedMinutes: 15,
                learnerCount: "2.5k",
                isBookmarked: true
            )
        ])
    }

    private func handleMoveTap(_ move: MoneyMove) {
        // Create article from move and show detail
        selectedArticle = createArticleFromMove(move)
    }

    private func handleBookmark(_ move: MoneyMove) {
        print("Bookmark toggled for: \(move.title)")
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
            commentCount: Int.random(in: 20...200),
            isBookmarked: move.isBookmarked,
            hasAudioVersion: true,
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
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()
            }

            // Title section
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                Text("Money Moves")
                    .font(AppTypography.largeTitle)
                    .foregroundColor(AppColors.textPrimary)

                Text("Real-world case studies & deep dives")
                    .font(AppTypography.callout)
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

                    // Animated glow orbs
                    GeometryReader { geometry in
                        Circle()
                            .fill(
                                RadialGradient(
                                    colors: [
                                        Color(hex: "3B82F6").opacity(0.3),
                                        Color.clear
                                    ],
                                    center: .center,
                                    startRadius: 0,
                                    endRadius: geometry.size.width * 0.35
                                )
                            )
                            .frame(width: geometry.size.width * 0.5)
                            .offset(x: -geometry.size.width * 0.15, y: geometry.size.height * 0.3)

                        Circle()
                            .fill(
                                RadialGradient(
                                    colors: [
                                        Color(hex: "8B5CF6").opacity(0.25),
                                        Color.clear
                                    ],
                                    center: .center,
                                    startRadius: 0,
                                    endRadius: geometry.size.width * 0.3
                                )
                            )
                            .frame(width: geometry.size.width * 0.4)
                            .offset(x: geometry.size.width * 0.6, y: geometry.size.height * 0.1)
                    }

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
                        .font(AppTypography.captionBold)
                        .foregroundColor(.white.opacity(0.8))
                        .tracking(1.2)

                    // Title
                    Text(article.title)
                        .font(.system(size: 28, weight: .bold))
                        .foregroundColor(.white)
                        .minimumScaleFactor(0.8)
                        .lineLimit(2)

                    // Description
                    Text(article.subtitle)
                        .font(AppTypography.callout)
                        .foregroundColor(.white.opacity(0.9))
                        .lineLimit(nil)
                        .fixedSize(horizontal: false, vertical: true)

                    // Meta info
                    HStack(spacing: AppSpacing.lg) {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "clock")
                                .font(.system(size: 12, weight: .medium))
                            Text("\(article.readTimeMinutes) min")
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(.white.opacity(0.8))

                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "person.2.fill")
                                .font(.system(size: 12, weight: .medium))
                            Text("\(article.viewCount) investors")
                                .font(AppTypography.caption)

                            if article.hasAudioVersion {
                                Image(systemName: "headphones")
                                    .font(.system(size: 12, weight: .medium))
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
    var onBookmark: ((MoneyMove) -> Void)?

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
                            onTap: { onMoveTap?(move) },
                            onBookmark: { onBookmark?(move) }
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
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(.white)
            }

            // Title and subtitle
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(category.rawValue)
                    .font(AppTypography.title3)
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
