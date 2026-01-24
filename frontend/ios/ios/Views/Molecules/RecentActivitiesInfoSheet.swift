//
//  RecentActivitiesInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining recent institutional and insider activities
//  Provides guidance for novice investors on interpreting trading data
//

import SwiftUI

struct RecentActivitiesInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // Header
                    headerSection

                    // Institutions Section
                    institutionsSectionHeader

                    // What is This Section
                    whatIsThisSection

                    // Understanding the Flow Bar
                    flowBarSection

                    // Insiders Section
                    insidersSectionHeader

                    // What are Insider Activities
                    whatAreInsiderActivitiesSection

                    // Informative vs Uninformative
                    informativeVsUninformativeSection

                    // Key Insights
                    keyInsightsSection

                    // Important Considerations
                    considerationsSection
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Recent Activities")
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
                Image(systemName: "chart.bar.doc.horizontal")
                    .font(.system(size: 24))
                    .foregroundColor(AppColors.primaryBlue)

                Text("Understanding Recent Activities")
                    .font(AppTypography.title2)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("This section tracks recent buying and selling activity by institutional investors and company insiders—two key groups whose actions can signal future stock performance.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Section Headers

    private var institutionsSectionHeader: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "building.columns.fill")
                .font(.system(size: 18))
                .foregroundColor(AppColors.primaryBlue)

            Text("Institutions Tab")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(.top, AppSpacing.md)
    }

    private var insidersSectionHeader: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "person.fill.checkmark")
                .font(.system(size: 18))
                .foregroundColor(AppColors.primaryBlue)

            Text("Insiders Tab")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(.top, AppSpacing.md)
    }

    // MARK: - What is This Section (Institutions)

    private var whatIsThisSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What Are Institutional Activities?")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            Text("Institutional investors—like mutual funds, pension funds, and hedge funds—must disclose their stock holdings quarterly through SEC Form 13F filings. This data shows you what the \"big money\" is doing.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Text("The most recent quarter's filings are summarized here, showing which institutions increased or decreased their positions.")
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Flow Bar Section

    private var flowBarSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Understanding the Flow Bar")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                flowBarExplanation(
                    color: AppColors.bullish,
                    title: "In Flow (Green)",
                    description: "Total value of shares purchased by institutions this quarter. Represents new money flowing into the stock."
                )

                flowBarExplanation(
                    color: AppColors.bearish,
                    title: "Out Flow (Red)",
                    description: "Total value of shares sold by institutions this quarter. Represents money exiting the stock."
                )

                flowBarExplanation(
                    color: AppColors.primaryBlue,
                    title: "Net Flow",
                    description: "The difference between In Flow and Out Flow. Positive means more buying; negative means more selling."
                )
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
    }

    private func flowBarExplanation(color: Color, title: String, description: String) -> some View {
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

    // MARK: - What Are Insider Activities Section

    private var whatAreInsiderActivitiesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What Are Insider Activities?")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            Text("Company insiders—executives, directors, and major shareholders—must report their stock trades to the SEC within 2 business days via Form 4. These filings reveal when people with deep company knowledge are buying or selling.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Text("Unlike institutional filings, insider trades are reported almost immediately, giving you a more timely view of activity.")
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Informative vs Uninformative Section

    private var informativeVsUninformativeSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Informative vs. Uninformative Trades")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            Text("Not all insider trades carry the same weight. We classify trades based on their likely motivation:")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: AppSpacing.md) {
                tradeTypeCard(
                    color: AppColors.bullish,
                    title: "Informative Buy",
                    description: "An insider voluntarily purchases shares on the open market with their own money. This is one of the strongest bullish signals—they believe the stock will rise.",
                    example: "CEO uses personal funds to buy $500K in shares"
                )

                tradeTypeCard(
                    color: AppColors.bearish,
                    title: "Informative Sell",
                    description: "An insider voluntarily sells shares not related to scheduled plans. May indicate concerns about the stock, though could also be diversification.",
                    example: "CFO sells shares outside of a 10b5-1 plan"
                )

                tradeTypeCard(
                    color: AppColors.textSecondary,
                    title: "Uninformative Buy",
                    description: "Shares acquired through compensation, stock options, or grants. Not a signal of conviction since they didn't use their own money.",
                    example: "Director receives annual stock grant"
                )

                tradeTypeCard(
                    color: AppColors.textSecondary,
                    title: "Uninformative Sell",
                    description: "Scheduled sales (10b5-1 plans), tax-related sales, or option exercises. These are routine and don't indicate sentiment.",
                    example: "Automatic quarterly sale per preset plan"
                )
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
    }

    private func tradeTypeCard(color: Color, title: String, description: String, example: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Circle()
                    .fill(color)
                    .frame(width: 10, height: 10)

                Text(title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(color == AppColors.textSecondary ? AppColors.textPrimary : color)
            }

            Text(description)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: AppSpacing.xs) {
                Text("Example:")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .italic()

                Text(example)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .italic()
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .fill(AppColors.background)
        )
    }

    // MARK: - Key Insights Section

    private var keyInsightsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Key Insights")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                insightCard(
                    number: "1",
                    title: "Informative Buying is the Strongest Signal",
                    description: "When insiders spend their own money to buy shares, they're putting skin in the game. Multiple insiders buying is especially bullish."
                )

                insightCard(
                    number: "2",
                    title: "Focus on the Net Flow",
                    description: "Positive net informative flow (more buying than selling) suggests insiders are confident. Persistent negative flow may be a warning sign."
                )

                insightCard(
                    number: "3",
                    title: "Filter to \"Informative\" for Clarity",
                    description: "Use the filter to see only meaningful trades. Uninformative trades add noise but don't indicate sentiment."
                )

                insightCard(
                    number: "4",
                    title: "Consider the Role",
                    description: "CEO and CFO trades often carry more weight than directors, as they have the deepest knowledge of the company's prospects."
                )
            }
        }
    }

    private func insightCard(number: String, title: String, description: String) -> some View {
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

    // MARK: - Considerations Section

    private var considerationsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 18))
                    .foregroundColor(AppColors.neutral)

                Text("Important Considerations")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                considerationRow("Institutional 13F filings are delayed 45 days after quarter end.")
                considerationRow("Insider Form 4 filings are reported within 2 business days.")
                considerationRow("Index funds buy automatically, not based on conviction.")
                considerationRow("Insiders may sell for personal reasons (taxes, diversification, home purchase).")
                considerationRow("Always combine with other research—activity data is just one signal.")
            }
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .stroke(AppColors.neutral.opacity(0.3), lineWidth: 1)
                )
        )
    }

    private func considerationRow(_ text: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: "arrow.right.circle.fill")
                .font(.system(size: 12))
                .foregroundColor(AppColors.textMuted)
                .padding(.top, 2)

            Text(text)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#Preview {
    RecentActivitiesInfoSheet()
}
