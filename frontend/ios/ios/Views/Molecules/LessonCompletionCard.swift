//
//  LessonCompletionCard.swift
//  ios
//
//  Molecule: Completion card for lesson story - displays checkmark, success message, and CTA
//

import SwiftUI

struct LessonCompletionCard: View {
    let title: String
    let subtitle: String
    let lessonNumber: Int
    let totalLessons: Int
    let estimatedMinutes: Int
    let ctaButtonTitle: String
    var onCTATapped: (() -> Void)?
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
                // Checkmark circle
                checkmarkView
                    .scaleEffect(checkmarkScale)
                    .opacity(checkmarkOpacity)

                // Title
                Text(title)
                    .font(.system(size: 28, weight: .bold))
                    .foregroundColor(AppColors.textPrimary)

                // Subtitle
                Text(subtitle)
                    .font(.system(size: 16, weight: .regular))
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
                    .lineSpacing(4)
                    .padding(.horizontal, AppSpacing.lg)

                // Lesson info badge
                lessonInfoBadge

                // CTA Button
                Button(action: {
                    onCTATapped?()
                }) {
                    Text(ctaButtonTitle)
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                        .frame(maxWidth: .infinity)
                        .frame(height: 52)
                        .background(AppColors.primaryBlue)
                        .cornerRadius(26)
                }
                .padding(.top, AppSpacing.md)

                // Close button
                Button(action: {
                    onCloseTapped?()
                }) {
                    Text("Close")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .padding(.top, AppSpacing.sm)
            }
            .padding(AppSpacing.xxl)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge)
                    .fill(AppColors.cardBackground)
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
                .fill(AppColors.bullish)
                .frame(width: 70, height: 70)

            // Checkmark icon
            Image(systemName: "checkmark")
                .font(.system(size: 36, weight: .bold))
                .foregroundColor(AppColors.textPrimary)
        }
    }

    private var lessonInfoBadge: some View {
        HStack(spacing: AppSpacing.sm) {
            // Blue dot
            Circle()
                .fill(AppColors.primaryBlue)
                .frame(width: 8, height: 8)

            Text("Lesson \(lessonNumber) of \(totalLessons)")
                .font(.system(size: 14, weight: .medium))
                .foregroundColor(AppColors.textSecondary)

            // Separator dot
            Circle()
                .fill(AppColors.textMuted)
                .frame(width: 4, height: 4)

            // Clock icon and duration
            HStack(spacing: 4) {
                Image(systemName: "clock")
                    .font(.system(size: 12))
                    .foregroundColor(AppColors.textMuted)

                Text("\(estimatedMinutes) min")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(AppColors.textSecondary)
            }
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
            lessonNumber: 1,
            totalLessons: 5,
            estimatedMinutes: 2,
            ctaButtonTitle: "Analyze a Stock"
        )
    }
    .preferredColorScheme(.dark)
}
