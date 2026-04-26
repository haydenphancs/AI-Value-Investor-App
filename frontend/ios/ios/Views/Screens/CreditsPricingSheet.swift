//
//  CreditsPricingSheet.swift
//  ios
//
//  Sheet shown when the user taps "Add More Credits" in the Research tab.
//  Displays available credit packs. StoreKit purchase flow is deferred —
//  taps surface a "Coming Soon" alert for now.
//

import SwiftUI

struct CreditsPack: Identifiable {
    let id = UUID()
    let credits: Int
    let priceLabel: String
    let bonusLabel: String?
    let isPopular: Bool

    static let standard: [CreditsPack] = [
        CreditsPack(credits: 25, priceLabel: "$4.99", bonusLabel: nil, isPopular: false),
        CreditsPack(credits: 100, priceLabel: "$14.99", bonusLabel: "Save 25%", isPopular: true),
        CreditsPack(credits: 500, priceLabel: "$59.99", bonusLabel: "Save 40%", isPopular: false)
    ]
}

struct CreditsPricingSheet: View {
    let currentBalance: Int
    @Environment(\.dismiss) private var dismiss
    @State private var pendingPack: CreditsPack?

    private let packs = CreditsPack.standard

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.xl) {
                        header
                        balanceCard
                        ForEach(packs) { pack in
                            packCard(pack)
                        }
                        footerNote
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.xxxl)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("Add Credits")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button(action: { dismiss() }) {
                        Image(systemName: "xmark")
                            .font(AppTypography.iconSmall).fontWeight(.semibold)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .alert("Coming Soon",
                   isPresented: Binding(
                       get: { pendingPack != nil },
                       set: { if !$0 { pendingPack = nil } }
                   )) {
                Button("OK") { pendingPack = nil }
            } message: {
                Text("Credit purchases will be available in an upcoming release.")
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Subviews

    private var header: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "sparkles")
                .font(.system(size: 32))
                .foregroundColor(AppColors.alertOrange)
            Text("Power up your research")
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)
            Text("Each AI report costs 5 credits. Pick a pack to unlock more deep dives.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
    }

    private var balanceCard: some View {
        HStack {
            Image(systemName: "creditcard.fill")
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.alertOrange)
            Text("Current balance")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            Spacer()
            Text("\(currentBalance) credits")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    private func packCard(_ pack: CreditsPack) -> some View {
        Button(action: { pendingPack = pack }) {
            HStack(alignment: .center, spacing: AppSpacing.md) {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    HStack(spacing: AppSpacing.sm) {
                        Text("\(pack.credits) credits")
                            .font(AppTypography.heading)
                            .foregroundColor(AppColors.textPrimary)
                        if pack.isPopular {
                            Text("POPULAR")
                                .font(AppTypography.captionEmphasis)
                                .foregroundColor(.white)
                                .padding(.horizontal, AppSpacing.sm)
                                .padding(.vertical, 2)
                                .background(
                                    Capsule().fill(AppColors.alertOrange)
                                )
                        }
                    }
                    if let bonus = pack.bonusLabel {
                        Text(bonus)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.bullish)
                    } else {
                        Text("\(pack.credits / 5) reports")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
                Spacer()
                Text(pack.priceLabel)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
                    .overlay(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .stroke(
                                pack.isPopular ? AppColors.alertOrange : Color.clear,
                                lineWidth: 1.5
                            )
                    )
            )
        }
        .buttonStyle(PlainButtonStyle())
    }

    private var footerNote: some View {
        Text("Credits never expire. Renews monthly with your subscription tier.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
            .multilineTextAlignment(.center)
            .padding(.top, AppSpacing.sm)
    }
}

#Preview {
    CreditsPricingSheet(currentBalance: 47)
        .preferredColorScheme(.dark)
}
