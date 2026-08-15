//
//  PlanSelectorRow.swift
//  ios
//
//  Molecule: one selectable plan on the upgrade screen — name, CURRENT badge, monthly
//  credits, price.
//
//  A full-width row rather than a segmented `Picker`: `Picker(.segmented)` truncates its
//  labels to fit and does not grow with Dynamic Type, so "Max $39.99" becomes "Max $3…"
//  on the accessibility sizes — on the control that decides what the user is buying.
//

import SwiftUI

struct PlanSelectorRow: View {
    let plan: PlanDTO
    /// Apple's localized price. The catalog's `priceLabel` is USD cents from our own DB,
    /// so it is a FALLBACK only — showing "$14.99" while the App Store charges €16.99 is
    /// a refund request and an App Review note.
    let displayPrice: String?
    let isSelected: Bool
    let isCurrent: Bool
    let action: () -> Void

    private var price: String { displayPrice ?? plan.priceLabel }

    var body: some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.md) {
                Image(systemName: isSelected ? "largecircle.fill.circle" : "circle")
                    .font(AppTypography.iconMedium)
                    .foregroundColor(isSelected ? AppColors.primaryBlue : AppColors.textMuted)
                    .accessibilityHidden(true)

                // Name+price side by side normally; stacked once the text no longer fits,
                // so neither half ever truncates.
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .firstTextBaseline, spacing: AppSpacing.sm) {
                        nameAndCredits
                        Spacer(minLength: AppSpacing.sm)
                        priceLabel
                    }
                    VStack(alignment: .leading, spacing: AppSpacing.xs) {
                        nameAndCredits
                        priceLabel
                    }
                }
            }
            .padding(AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .cardFill()
                    .overlay(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .stroke(
                                isSelected ? AppColors.primaryBlue.opacity(0.5) : Color.clear,
                                lineWidth: 1.5
                            )
                    )
            )
        }
        .buttonStyle(PlainButtonStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(plan.displayName), \(price)\(isCurrent ? ", current plan" : "")")
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }

    private var nameAndCredits: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            HStack(spacing: AppSpacing.sm) {
                Text(plan.displayName)
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
                if isCurrent {
                    Text("CURRENT")
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(AppColors.textOnAccent)
                        .padding(.horizontal, AppSpacing.sm)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(AppColors.primaryFill))
                }
            }
            Text("\(plan.monthlyCredits) credits / month")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private var priceLabel: some View {
        Text(price)
            .font(AppTypography.headingSmall)
            .foregroundColor(AppColors.textPrimary)
        + Text(plan.priceCents > 0 ? " /mo" : "")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
    }
}

#Preview {
    VStack(spacing: AppSpacing.sm) {
        PlanSelectorRow(
            plan: PlanDTO(tier: "free", displayName: "Free", monthlyCredits: 50,
                          priceCents: 0, priceLabel: "Free"),
            displayPrice: nil, isSelected: false, isCurrent: true, action: {}
        )
        PlanSelectorRow(
            plan: PlanDTO(tier: "pro", displayName: "Pro", monthlyCredits: 1200,
                          priceCents: 1499, priceLabel: "$14.99"),
            displayPrice: "$14.99", isSelected: true, isCurrent: false, action: {}
        )
        PlanSelectorRow(
            plan: PlanDTO(tier: "premium", displayName: "Max", monthlyCredits: 4000,
                          priceCents: 3999, priceLabel: "$39.99"),
            displayPrice: "$39.99", isSelected: false, isCurrent: false, action: {}
        )
    }
    .padding()
    .background(AppColors.background)
}
