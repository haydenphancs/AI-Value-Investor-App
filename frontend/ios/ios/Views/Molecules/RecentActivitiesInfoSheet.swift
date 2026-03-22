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

                    // Congress Section
                    congressSectionHeader
                    congressDisclaimerSection

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
                    .font(AppTypography.iconXL)
                    .foregroundColor(AppColors.primaryBlue)

                Text("Understanding Recent Activities")
                    .font(AppTypography.titleCompact)
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
                .font(AppTypography.iconMedium)
                .foregroundColor(AppColors.primaryBlue)

            Text("Institutions Tab")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(.top, AppSpacing.md)
    }

    private var insidersSectionHeader: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "person.fill.checkmark")
                .font(AppTypography.iconMedium)
                .foregroundColor(AppColors.primaryBlue)

            Text("Insiders Tab")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(.top, AppSpacing.md)
    }

    // MARK: - What is This Section (Institutions)

    private var whatIsThisSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("What Are Institutional Activities?")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text("Institutional investors—like mutual funds, pension funds, and hedge funds—must disclose their stock holdings quarterly through SEC Form 13F filings. This data shows you what the \"big money\" is doing.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Text("The most recent quarter's filings are summarized here, showing which institutions increased or decreased their positions.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Flow Bar Section

    private var flowBarSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Understanding the Flow Bar")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            VStack(alignment: .leading, spacing: AppSpacing.md) {
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
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Circle()
                .fill(color)
                .frame(width: 12, height: 12)
                .padding(.top, 5)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.bodySmallEmphasis)
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
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text("Company insiders—executives, directors, and major shareholders—must report their stock trades to the SEC within 2 business days via Form 4. These filings reveal when people with deep company knowledge are buying or selling.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Text("Unlike institutional filings, insider trades are reported almost immediately, giving you a more timely view of activity.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - Informative vs Uninformative Section

    private var informativeVsUninformativeSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Informative vs. Uninformative Trades")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text("Not all insider trades carry the same weight. We classify trades based on their likely motivation:")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            VStack(alignment: .leading, spacing: AppSpacing.md) {
                flowBarExplanation(
                    color: AppColors.bullish,
                    title: "Informative Buy",
                    description: "An insider voluntarily purchases shares on the open market with their own money. This is one of the strongest bullish signals."
                )

                flowBarExplanation(
                    color: AppColors.bearish,
                    title: "Informative Sell",
                    description: "An insider voluntarily sells shares not related to scheduled plans. May indicate concerns about the stock or diversification."
                )

                flowBarExplanation(
                    color: AppColors.textSecondary,
                    title: "Uninformative Buy",
                    description: "Shares acquired through compensation, stock options, or grants. Not a signal of conviction since they didn't use their own money."
                )

                flowBarExplanation(
                    color: AppColors.textSecondary,
                    title: "Uninformative Sell",
                    description: "Scheduled sales (10b5-1 plans), tax-related sales, or option exercises. These are routine and don't indicate sentiment."
                )
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
    }

    // MARK: - Key Insights Section

    private var keyInsightsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "lightbulb.fill")
                    .font(AppTypography.iconMedium)
                    .foregroundColor(AppColors.neutral)

                Text("Key Insights")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
            }

            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                considerationRow("Informative buying is the strongest signal—insiders putting skin in the game.")
                considerationRow("Positive net informative flow suggests insider confidence; persistent negative flow is a warning.")
                considerationRow("Filter to \"Informative\" to see only meaningful trades and cut through noise.")
                considerationRow("CEO and CFO trades carry more weight than directors—they have the deepest knowledge.")
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

    // MARK: - Congress Section

    private var congressSectionHeader: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "building.columns.fill")
                .font(AppTypography.iconMedium)
                .foregroundColor(AppColors.primaryBlue)

            Text("Congress")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(.top, AppSpacing.md)
    }

    private var congressDisclaimerSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("About Congressional Trade Data")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            Text("Congress members report trades in broad ranges (e.g., \"$1,001 - $15,000\" or \"$1,000,001 - $5,000,000\") rather than exact amounts. Aggregate totals shown as \"Est.\" are estimated using the midpoint of these reported ranges.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Text("Trades are sorted by the maximum potential value of the reported range when using \"By Value\" sorting.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
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
                    .font(AppTypography.iconMedium)
                    .foregroundColor(AppColors.neutral)

                Text("Important Considerations")
                    .font(AppTypography.headingSmall)
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
                .font(AppTypography.iconXS)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, 2)

            Text(text)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#Preview {
    RecentActivitiesInfoSheet()
}
