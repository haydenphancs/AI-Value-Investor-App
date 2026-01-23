//
//  ShareholderBreakdownInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining shareholder breakdown
//  Helps novice investors understand ownership distribution
//

import SwiftUI

struct ShareholderBreakdownInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // Header
                    headerSection

                    // Ownership Types
                    ownershipTypesSection

                    // What to Look For
                    whatToLookForSection

                    // Examples
                    examplesSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Shareholder Breakdown")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "person.3.fill")
                    .font(.system(size: 24))
                    .foregroundColor(AppColors.primaryBlue)

                Text("Who Owns the Company?")
                    .font(AppTypography.title2)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("Understanding who owns a company's stock can reveal important insights about stability, confidence, and potential price movements.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Ownership Types Section

    private var ownershipTypesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Ownership Types")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                ownershipCard(
                    color: HoldersColors.insiders,
                    title: "Insiders",
                    percentage: "Typically 5-20%",
                    description: "Company executives, directors, and employees who own shares.",
                    bullish: "High insider ownership signals management believes in the company's future.",
                    bearish: "Very low insider ownership may indicate lack of confidence."
                )

                ownershipCard(
                    color: HoldersColors.institutions,
                    title: "Institutions",
                    percentage: "Typically 40-80%",
                    description: "Large investment firms like mutual funds, pension funds, and hedge funds.",
                    bullish: "High institutional ownership means professionals see value.",
                    bearish: "Too high (>90%) can mean crowded trade with limited upside."
                )

                ownershipCard(
                    color: HoldersColors.publicOther,
                    title: "Public/Other",
                    percentage: "Typically 10-40%",
                    description: "Individual retail investors and smaller holders.",
                    bullish: "Balanced retail interest provides liquidity.",
                    bearish: "Very high retail can mean more volatility."
                )
            }
        }
    }

    private func ownershipCard(
        color: Color,
        title: String,
        percentage: String,
        description: String,
        bullish: String,
        bearish: String
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Circle()
                    .fill(color)
                    .frame(width: 12, height: 12)

                Text(title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Text(percentage)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Text(description)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                HStack(alignment: .top, spacing: AppSpacing.xs) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 12))
                        .foregroundColor(AppColors.bullish)
                    Text(bullish)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack(alignment: .top, spacing: AppSpacing.xs) {
                    Image(systemName: "arrow.down.circle.fill")
                        .font(.system(size: 12))
                        .foregroundColor(AppColors.bearish)
                    Text(bearish)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.top, AppSpacing.xs)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - What to Look For Section

    private var whatToLookForSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("What to Look For")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                tipRow(
                    icon: "checkmark.circle.fill",
                    iconColor: AppColors.bullish,
                    title: "Healthy Balance",
                    description: "A mix of insider (10-15%), institutional (50-70%), and public ownership often indicates a stable, well-regarded company."
                )

                tipRow(
                    icon: "exclamationmark.triangle.fill",
                    iconColor: AppColors.neutral,
                    title: "Watch for Extremes",
                    description: "Very high or very low ownership in any category can be a warning sign worth investigating further."
                )

                tipRow(
                    icon: "arrow.triangle.2.circlepath",
                    iconColor: AppColors.primaryBlue,
                    title: "Track Changes Over Time",
                    description: "Rising institutional ownership is often bullish. Declining insider ownership may warrant attention."
                )
            }
        }
    }

    private func tipRow(icon: String, iconColor: Color, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(.system(size: 20))
                .foregroundColor(iconColor)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Examples Section

    private var examplesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Real-World Examples")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                exampleCard(
                    company: "Apple (AAPL)",
                    breakdown: "Insiders: 0.1% | Institutions: 60% | Public: 40%",
                    insight: "Very low insider ownership is normal for mega-caps where founders have diversified over decades."
                )

                exampleCard(
                    company: "Berkshire Hathaway",
                    breakdown: "Insiders: 38% | Institutions: 30% | Public: 32%",
                    insight: "High insider ownership (Warren Buffett) signals strong alignment with shareholders."
                )
            }
        }
    }

    private func exampleCard(company: String, breakdown: String, insight: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text(company)
                .font(AppTypography.bodyBold)
                .foregroundColor(AppColors.textPrimary)

            Text(breakdown)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Text(insight)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    ShareholderBreakdownInfoSheet()
}
