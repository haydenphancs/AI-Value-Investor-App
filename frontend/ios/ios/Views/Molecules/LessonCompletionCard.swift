//
//  LessonCompletionCard.swift
//  ios
//
//  Molecule: Completion card for lesson story — checkmark, success message, Ask Cay AI.
//

import SwiftUI

struct LessonCompletionCard: View {
    let title: String
    let subtitle: String
    var imageName: String? = nil
    /// The card's primary action. "Ask Cay AI about this" used to float as a separate capsule
    /// BELOW the card while "Analyze a Stock" held this slot; the lesson-just-finished moment is
    /// the one where a follow-up question is worth asking, so it takes the primary slot instead.
    /// Optional so a caller with nothing to ask (previews) renders just the Close action.
    var onAskAITapped: (() -> Void)?
    var onCloseTapped: (() -> Void)?

    // Animation state
    @State private var checkmarkScale: CGFloat = 0.5
    @State private var checkmarkOpacity: Double = 0
    @State private var cardOpacity: Double = 0
    @State private var cardOffset: CGFloat = 30

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
                .frame(height: 60)

            // Completion card container
            VStack(spacing: AppSpacing.xxl) {
                // Optional closing-image slot (last card of the lesson)
                if let imageName = imageName, !imageName.isEmpty {
                    LessonImageSlot(imageName: imageName, height: 140, horizontalPadding: 0)
                }

                // Checkmark circle
                checkmarkView
                    .scaleEffect(checkmarkScale)
                    .opacity(checkmarkOpacity)

                // Title
                Text(title)
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textPrimary)

                // Subtitle
                Text(subtitle)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
                    .lineSpacing(4)
                    .padding(.horizontal, AppSpacing.lg)

                // Primary action: ask Cay AI about the lesson just finished.
                if let onAskAITapped {
                    Button(action: onAskAITapped) {
                        HStack(spacing: AppSpacing.sm) {
                            // `sparkles.2` — the app's Cay AI mark, the same glyph the header
                            // button and every chat surface use. This was `sparkles` (the
                            // three-star variant), the only place it appeared.
                            Image(systemName: "sparkles.2")
                            Text("Ask Cay AI about this")
                        }
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textOnAccent)
                        .frame(maxWidth: .infinity)
                        .frame(height: 52)
                        .background(AppColors.primaryFill)
                        .cornerRadius(26)
                    }
                    .padding(.top, AppSpacing.md)
                }

                // Close button
                Button(action: {
                    onCloseTapped?()
                }) {
                    Text("Close")
                        .font(AppTypography.body).fontWeight(.medium)
                        .foregroundColor(AppColors.textSecondary)
                }
                .padding(.top, AppSpacing.sm)
            }
            .padding(AppSpacing.xxl)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge)
                    .cardFill()
            )
            .padding(.horizontal, AppSpacing.xl)
            .opacity(cardOpacity)
            .offset(y: cardOffset)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear {
            animateAppearance()
        }
    }

    // MARK: - Subviews

    private var checkmarkView: some View {
        ZStack {
            // Outer ring
            Circle()
                .stroke(AppColors.bullish, lineWidth: 4)
                .frame(width: 80, height: 80)

            // Filled circle
            Circle()
                .fill(AppColors.gainFill)
                .frame(width: 70, height: 70)

            // Checkmark icon
            Image(systemName: "checkmark")
                .font(AppTypography.iconJumbo).fontWeight(.bold)
                .foregroundColor(AppColors.textOnFill)
        }
    }

    // MARK: - Animations

    private func animateAppearance() {
        withAnimation(.spring(response: 0.5, dampingFraction: 0.7).delay(0.1)) {
            cardOpacity = 1
            cardOffset = 0
        }

        withAnimation(.spring(response: 0.6, dampingFraction: 0.6).delay(0.3)) {
            checkmarkScale = 1.0
            checkmarkOpacity = 1.0
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        LessonCompletionCard(
            title: "You're ready.",
            subtitle: "You've learned the core idea. Practice with a real stock to reinforce it.",
            onAskAITapped: {}
        )
    }
}
