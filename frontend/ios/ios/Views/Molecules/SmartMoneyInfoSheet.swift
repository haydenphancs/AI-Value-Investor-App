//
//  SmartMoneyInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining smart money tracking
//  Includes Peter Lynch quote and guidance for novice investors
//

import SwiftUI

struct SmartMoneyInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // Peter Lynch Quote
                    quoteSection

                    // What is Smart Money
                    whatIsSmartMoneySection

                    // Types of Smart Money
                    smartMoneyTypesSection

                    // Reading the Chart
                    readingChartSection

                    // Key Takeaways
                    keyTakeawaysSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Smart Money")
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

    // MARK: - Quote Section

    private var quoteSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Image(systemName: "quote.opening")
                .font(.system(size: 24))
                .foregroundColor(AppColors.primaryBlue)

            Text("Insiders might sell their shares for any number of reasons (taxes, divorce, buying a house), but they buy them for only one: they think the price will rise.")
                .font(AppTypography.body)
                .italic()
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            Text("— Peter Lynch")
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.primaryBlue)
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .stroke(AppColors.primaryBlue.opacity(0.3), lineWidth: 1)
                )
        )
    }

    // MARK: - What is Smart Money

    private var whatIsSmartMoneySection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What is Smart Money?")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            Text("\"Smart Money\" refers to capital invested by those with deep knowledge of the market or a specific company. Tracking their buying and selling patterns can provide valuable signals about a stock's future direction.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Text("The chart shows stock price (top) alongside buy/sell activity (bottom), letting you see when smart money acted relative to price movements.")
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Smart Money Types

    private var smartMoneyTypesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Types of Smart Money")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                smartMoneyTypeCard(
                    icon: "person.fill.checkmark",
                    title: "Insider Trading",
                    description: "Legal trades by company executives, directors, and employees. They have the deepest knowledge of the company.",
                    signal: "Insider buying is one of the strongest bullish signals. Selling is less meaningful (they may need cash).",
                    signalType: .bullish
                )

                smartMoneyTypeCard(
                    icon: "building.columns.fill",
                    title: "Hedge Funds",
                    description: "Professional money managers who actively research stocks. Disclosed quarterly in 13F filings.",
                    signal: "Large position increases by respected funds often precede gains. Watch for coordinated buying.",
                    signalType: .neutral
                )

                smartMoneyTypeCard(
                    icon: "building.2.fill",
                    title: "Congress",
                    description: "U.S. lawmakers must disclose their stock trades. Some have access to non-public information.",
                    signal: "Trades by members on relevant committees (e.g., tech committee buying tech stocks) can be informative.",
                    signalType: .neutral
                )
            }
        }
    }

    private enum SignalType {
        case bullish, neutral, bearish

        var color: Color {
            switch self {
            case .bullish: return AppColors.bullish
            case .neutral: return AppColors.neutral
            case .bearish: return AppColors.bearish
            }
        }
    }

    private func smartMoneyTypeCard(
        icon: String,
        title: String,
        description: String,
        signal: String,
        signalType: SignalType
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(.system(size: 18))
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
                    .foregroundColor(signalType.color)

                Text(signal)
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

    // MARK: - Reading the Chart

    private var readingChartSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Reading the Chart")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                chartLegendRow(
                    color: HoldersColors.flowLine,
                    title: "Price Line (Top Chart)",
                    description: "Stock price over the period. Compare when smart money acted vs price movements."
                )

                chartLegendRow(
                    color: HoldersColors.buyVolume,
                    title: "Green Bars (Buy Volume)",
                    description: "Dollar amount purchased. Taller bars = more buying activity that month."
                )

                chartLegendRow(
                    color: HoldersColors.sellVolume,
                    title: "Red Bars (Sell Volume)",
                    description: "Dollar amount sold. Extends below the zero line for visual clarity."
                )

                chartLegendRow(
                    color: AppColors.bullish,
                    title: "Positive Net Flow",
                    description: "When total buys exceed sells — smart money is accumulating."
                )

                chartLegendRow(
                    color: AppColors.bearish,
                    title: "Negative Net Flow",
                    description: "When total sells exceed buys — smart money may be reducing exposure."
                )
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
    }

    private func chartLegendRow(color: Color, title: String, description: String) -> some View {
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

    // MARK: - Key Takeaways

    private var keyTakeawaysSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Key Takeaways")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                takeawayRow(
                    number: "1",
                    title: "Buying > Selling",
                    description: "Pay more attention to buying activity. Selling has many innocent explanations."
                )

                takeawayRow(
                    number: "2",
                    title: "Look for Patterns",
                    description: "Consistent buying over months is more meaningful than a single large purchase."
                )

                takeawayRow(
                    number: "3",
                    title: "Compare to Price",
                    description: "Smart money buying during dips suggests they see value. Buying at highs shows strong conviction."
                )

                takeawayRow(
                    number: "4",
                    title: "Don't Follow Blindly",
                    description: "Smart money can be wrong. Use this as one data point among many in your research."
                )
            }
        }
    }

    private func takeawayRow(number: String, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Text(number)
                .font(AppTypography.headline)
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: 24, height: 24)
                .background(
                    Circle()
                        .fill(AppColors.primaryBlue.opacity(0.15))
                )

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
}

#Preview {
    SmartMoneyInfoSheet()
}
