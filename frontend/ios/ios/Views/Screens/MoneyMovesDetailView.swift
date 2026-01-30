//
//  MoneyMovesDetailView.swift
//  ios
//
//  Money Moves Detail View - Full screen with hero card and categorized case studies
//

import SwiftUI

struct MoneyMovesDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var blueprints: [MoneyMove] = []
    @State private var valueTraps: [MoneyMove] = []
    @State private var battles: [MoneyMove] = []

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
                        FeaturedDeepDiveHeroCard()
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
        .onAppear {
            loadSampleData()
        }
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
        print("Money move tapped: \(move.title)")
    }

    private func handleBookmark(_ move: MoneyMove) {
        print("Bookmark toggled for: \(move.title)")
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
    var body: some View {
        ZStack(alignment: .topTrailing) {
            // Background with grainy gradient
            ZStack {
                // Base gradient (Red/Orange for FTX)
                LinearGradient(
                    colors: [
                        Color(hex: "DC2626"),
                        Color(hex: "EA580C"),
                        Color(hex: "F97316")
                    ],
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
                    .font(AppTypography.captionBold)
                    .foregroundColor(.white.opacity(0.8))
                    .tracking(1.2)

                // Title
                Text("The Future of Digital Finance")
                    .font(.system(size: 28, weight: .bold))
                    .foregroundColor(.white)
                    .minimumScaleFactor(0.8)
                    .lineLimit(2)

                // Description
                Text("Exploring the intersection of fintech innovation, cryptocurrency adoption, and traditional banking transformation.")
                    .font(AppTypography.callout)
                    .foregroundColor(.white.opacity(0.9))
                    .lineLimit(nil)
                    .fixedSize(horizontal: false, vertical: true)

                // Meta info
                HStack(spacing: AppSpacing.lg) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "clock")
                            .font(.system(size: 12, weight: .medium))
                        Text("18 min")
                            .font(AppTypography.caption)
                    }
                    .foregroundColor(.white.opacity(0.8))

                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "person.2.fill")
                            .font(.system(size: 12, weight: .medium))
                        Text("3.2k investors")
                            .font(AppTypography.caption)
                        Image(systemName: "headphones")
                            .font(.system(size: 12, weight: .medium))
                            .padding(.leading, AppSpacing.md)
                    }
                    .foregroundColor(.white.opacity(0.8))
                }
                .padding(.top, AppSpacing.xs)
            }
            .padding(AppSpacing.xl)
            .frame(maxWidth: .infinity, alignment: .leading)

            // Must Read pill tag
            MustReadPill()
                .padding(AppSpacing.lg)
        }
        .aspectRatio(16/9, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
    }
}

// MARK: - Must Read Pill
private struct MustReadPill: View {
    var body: some View {
        Text("MUST READ")
            .font(AppTypography.captionBold)
            .foregroundColor(.white)
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.xs)
            .background(
                Capsule()
                    .fill(Color.black.opacity(0.4))
                    .overlay(
                        Capsule()
                            .strokeBorder(Color.white.opacity(0.3), lineWidth: 1)
                    )
            )
    }
}

// MARK: - Grainy Texture Overlay
private struct GrainyTextureOverlay: View {
    var body: some View {
        Canvas { context, size in
            // Create noise pattern
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
        .preferredColorScheme(.dark)
}
