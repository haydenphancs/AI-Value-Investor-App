//
//  HoldersInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining holders and smart money metrics
//  Helps novice investors understand ownership data and what it means
//

import SwiftUI

struct HoldersInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xxl) {
                    // Header Section
                    headerSection

                    // Shareholder Breakdown Section
                    shareholderBreakdownSection

                    // Smart Money Section
                    smartMoneySection

                    // Value Investing Tips
                    valueInvestingTipsSection

                    // Reading the Charts Section
                    chartReadingSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Understanding Holders")
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

                Text("Ownership Analysis")
                    .font(AppTypography.title2)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("Understanding who owns a company's stock can provide valuable insights into its stability, growth potential, and market sentiment. This section breaks down ownership patterns and tracks \"smart money\" movements.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Shareholder Breakdown Section

    private var shareholderBreakdownSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Shareholder Breakdown")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                ownershipTypeCard(
                    color: HoldersColors.insiders,
                    title: "Insiders",
                    description: "Company executives, directors, and employees who own shares. High insider ownership (>10%) often signals management confidence.",
                    example: "If Apple's CEO buys more shares, it suggests they believe the stock is undervalued."
                )

                ownershipTypeCard(
                    color: HoldersColors.institutions,
                    title: "Institutions",
                    description: "Large investment firms like mutual funds, pension funds, and hedge funds. High institutional ownership (>50%) indicates professional investor confidence.",
                    example: "When Vanguard or BlackRock increases their stake, it often means they see long-term value."
                )

                ownershipTypeCard(
                    color: HoldersColors.publicOther,
                    title: "Public/Other",
                    description: "Individual retail investors and other smaller holders. Higher retail ownership can mean more volatility but also potential for growth.",
                    example: "Stocks with high retail interest (like meme stocks) can have unpredictable price swings."
                )
            }
        }
    }

    private func ownershipTypeCard(color: Color, title: String, description: String, example: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Circle()
                    .fill(color)
                    .frame(width: 12, height: 12)

                Text(title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(description)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(alignment: .top, spacing: AppSpacing.sm) {
                Text("Example:")
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.accentCyan)

                Text(example)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
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

    // MARK: - Smart Money Section

    private var smartMoneySection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Smart Money Tracking")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                smartMoneyCard(
                    icon: "person.fill.checkmark",
                    title: "Insider Trading",
                    description: "Legal insider trading (not the illegal kind!) shows when executives buy or sell their company's stock. Insiders buying is often bullish; selling can be neutral (they may just need cash).",
                    signal: "Strong buy signal when multiple insiders buy simultaneously"
                )

                smartMoneyCard(
                    icon: "building.columns.fill",
                    title: "Hedge Funds",
                    description: "Professional money managers who actively research stocks. Their positions are disclosed quarterly in 13F filings. Large hedge fund buying often precedes price appreciation.",
                    signal: "Watch for increasing positions by respected value investors"
                )

                smartMoneyCard(
                    icon: "building.2.fill",
                    title: "Congress",
                    description: "U.S. lawmakers must disclose their stock trades. Some studies suggest congressional trading may outperform the market, making their moves worth watching.",
                    signal: "Pay attention to trades by members on relevant committees"
                )
            }
        }
    }

    private func smartMoneyCard(icon: String, title: String, description: String, signal: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(.system(size: 16))
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(width: 24)

                Text(title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(description)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(alignment: .top, spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .font(.system(size: 12))
                    .foregroundColor(AppColors.neutral)

                Text(signal)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.neutral)
                    .fixedSize(horizontal: false, vertical: true)
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

    // MARK: - Value Investing Tips Section

    private var valueInvestingTipsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .foregroundColor(AppColors.neutral)

                Text("Value Investing Tips")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(spacing: AppSpacing.md) {
                tipCard(
                    title: "Follow the Smart Money, Not Blindly",
                    description: "Smart money moves can be informative, but always do your own research. Insiders and institutions can be wrong too."
                )

                tipCard(
                    title: "Look for Accumulation Patterns",
                    description: "Consistent buying over several months is more meaningful than a single large purchase. Patience often pays off."
                )

                tipCard(
                    title: "Consider the Context",
                    description: "Insider selling during a stock's all-time high is different from selling during a dip. Context matters for interpreting moves."
                )

                tipCard(
                    title: "Net Flow is Key",
                    description: "Focus on the net informative flow (buys minus sells) rather than individual transactions. The trend tells the story."
                )
            }
        }
    }

    private func tipCard(title: String, description: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 16))
                    .foregroundColor(AppColors.bullish)
                    .frame(width: 24)

                Text(title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text(description)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 24 + AppSpacing.sm)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Chart Reading Section

    private var chartReadingSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Reading the Charts")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(alignment: .leading, spacing: AppSpacing.md) {
                chartLegendExplanation(
                    color: HoldersColors.buyVolume,
                    title: "Green Bars (Buy Volume)",
                    description: "Shows the dollar amount of stock purchased during the month. Taller bars indicate more buying activity."
                )

                chartLegendExplanation(
                    color: HoldersColors.sellVolume,
                    title: "Red Bars (Sell Volume)",
                    description: "Shows the dollar amount of stock sold during the month. These appear below the zero line."
                )

                chartLegendExplanation(
                    color: HoldersColors.flowLine,
                    title: "Blue Line (Cumulative Flow)",
                    description: "Tracks the running total of net buying/selling over time. An upward trend indicates accumulation."
                )

                chartLegendExplanation(
                    color: AppColors.bullish,
                    title: "Positive Net Flow",
                    description: "When total buys exceed sells, indicating smart money is accumulating shares."
                )

                chartLegendExplanation(
                    color: AppColors.bearish,
                    title: "Negative Net Flow",
                    description: "When total sells exceed buys, suggesting smart money may be reducing exposure."
                )
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
    }

    private func chartLegendExplanation(color: Color, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Circle()
                .fill(color)
                .frame(width: 12, height: 12)
                .padding(.top, 4)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(description)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

#Preview {
    HoldersInfoSheet()
}
