//
//  LearnCreditsCard.swift
//  ios
//
//  Molecule: Credit balance card specifically for Learn section
//

import SwiftUI

struct LearnCreditsCard: View {
    let balance: CreditBalance
    var onAddCredits: (() -> Void)?

    private let gradientColors = [
        Color(hex: "F97316"),
        Color(hex: "EA580C")
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            Text("Credit Balance")
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)

            Text("Manage your research credits")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textPrimary.opacity(0.8))

            // Credits Display
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                HStack(alignment: .lastTextBaseline, spacing: AppSpacing.sm) {
                    Text("\(balance.credits)")
                        .font(.system(size: 40, weight: .bold, design: .rounded))
                        .foregroundColor(AppColors.textPrimary)

                    Text("credits")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textPrimary.opacity(0.8))
                }

                Text(balance.formattedRenewalDate)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textPrimary.opacity(0.7))
            }
            .padding(AppSpacing.lg)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(Color.black.opacity(0.2))
            )

            // Add Credits Button
            Button(action: {
                onAddCredits?()
            }) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "plus")
                        .font(.system(size: 14, weight: .semibold))

                    Text("Add More Credits")
                        .font(AppTypography.calloutBold)
                }
                .foregroundColor(Color(hex: "F97316"))
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(AppColors.textPrimary)
                )
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge)
                .fill(
                    LinearGradient(
                        colors: gradientColors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
        )
    }
}

#Preview {
    LearnCreditsCard(balance: CreditBalance.mock)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
