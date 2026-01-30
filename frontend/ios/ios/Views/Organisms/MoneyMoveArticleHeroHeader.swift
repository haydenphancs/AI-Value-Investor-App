//
//  MoneyMoveArticleHeroHeader.swift
//  ios
//
//  Organism: Hero header section for money move article with gradient background
//

import SwiftUI

struct MoneyMoveArticleHeroHeader: View {
    let article: MoneyMoveArticle
    var onBackTapped: (() -> Void)?
    var onShareTapped: (() -> Void)?

    private var gradientColors: [Color] {
        article.heroGradientColors.map { Color(hex: $0) }
    }

    var body: some View {
        ZStack(alignment: .top) {
            // Background gradient with effects
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
                                    Color(hex: "3B82F6").opacity(0.4),
                                    Color.clear
                                ],
                                center: .center,
                                startRadius: 0,
                                endRadius: geometry.size.width * 0.4
                            )
                        )
                        .frame(width: geometry.size.width * 0.6)
                        .offset(x: -geometry.size.width * 0.1, y: geometry.size.height * 0.2)

                    Circle()
                        .fill(
                            RadialGradient(
                                colors: [
                                    Color(hex: "8B5CF6").opacity(0.3),
                                    Color.clear
                                ],
                                center: .center,
                                startRadius: 0,
                                endRadius: geometry.size.width * 0.35
                            )
                        )
                        .frame(width: geometry.size.width * 0.5)
                        .offset(x: geometry.size.width * 0.5, y: geometry.size.height * 0.5)
                }

                // Grain texture
                GrainyTextureOverlay()

                // Bottom fade
                VStack {
                    Spacer()
                    LinearGradient(
                        colors: [
                            Color.clear,
                            AppColors.background.opacity(0.8),
                            AppColors.background
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                    .frame(height: 100)
                }
            }

            // Content
            VStack(alignment: .leading, spacing: 0) {
                // Navigation bar
                HStack {
                    Button(action: { onBackTapped?() }) {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "chevron.left")
                                .font(.system(size: 16, weight: .semibold))
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

                    Spacer()

                    // Tag pill
                    if let tagLabel = article.tagLabel {
                        ArticleTagPill(text: tagLabel)
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.md)

                Spacer()

                // Title and meta
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    // Category
                    Text(article.category.rawValue.uppercased())
                        .font(AppTypography.captionBold)
                        .foregroundColor(.white.opacity(0.7))
                        .tracking(1.2)

                    // Title
                    Text(article.title)
                        .font(.system(size: 28, weight: .bold))
                        .foregroundColor(.white)
                        .lineSpacing(4)

                    // Subtitle
                    Text(article.subtitle)
                        .font(AppTypography.body)
                        .foregroundColor(.white.opacity(0.85))
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)

                    // Meta row
                    HStack(spacing: AppSpacing.lg) {
                        // Date
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "calendar")
                                .font(.system(size: 12, weight: .medium))
                            Text(article.formattedDate)
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(.white.opacity(0.7))

                        // Read time
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "clock")
                                .font(.system(size: 12, weight: .medium))
                            Text(article.formattedReadTime)
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(.white.opacity(0.7))

                        // Views
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "eye")
                                .font(.system(size: 12, weight: .medium))
                            Text(article.viewCount)
                                .font(AppTypography.caption)
                        }
                        .foregroundColor(.white.opacity(0.7))

                        // Audio
                        if article.hasAudioVersion {
                            Image(systemName: "headphones")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundColor(.white.opacity(0.7))
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.bottom, AppSpacing.xl)
            }
        }
        .frame(height: 340)
    }
}

// MARK: - Grainy Texture Overlay
struct GrainyTextureOverlay: View {
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
    ScrollView {
        MoneyMoveArticleHeroHeader(
            article: MoneyMoveArticle.sampleDigitalFinance
        )

        Text("Content goes here")
            .foregroundColor(.white)
            .padding()
    }
    .background(AppColors.background)
    .ignoresSafeArea()
    .preferredColorScheme(.dark)
}
