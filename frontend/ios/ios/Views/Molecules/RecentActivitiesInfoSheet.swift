//
//  RecentActivitiesInfoSheet.swift
//  ios
//
//  Molecule: Educational sheet explaining recent institutional activities
//  Provides guidance for novice investors on interpreting institutional trading
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

                    // What is This Section
                    whatIsThisSection

                    // Understanding the Flow Bar
                    flowBarSection

                    // Reading the Activity List
                    activityListSection

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

                Text("Understanding Institutional Activity")
                    .font(AppTypography.title2)
                    .foregroundColor(AppColors.textPrimary)
            }

            Text("This section shows recent buying and selling activity by large institutional investors, based on their required SEC filings.")
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

    // MARK: - What is This Section

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

    // MARK: - Activity List Section

    private var activityListSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Reading the Activity List")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                activityExplanation(
                    icon: "building.columns.fill",
                    title: "Institution Name & Category",
                    description: "The name of the investing firm and their type (e.g., Asset Management, Mutual Funds)."
                )

                activityExplanation(
                    icon: "calendar",
                    title: "Filing Date",
                    description: "When the SEC filing was submitted. Note: 13F filings are delayed up to 45 days after quarter end."
                )

                activityExplanation(
                    icon: "plus.forwardslash.minus",
                    title: "Change Value & Percent",
                    description: "The dollar amount and percentage change in their position. Green = increased, Red = decreased."
                )

                activityExplanation(
                    icon: "chart.bar.fill",
                    title: "Total Held",
                    description: "The total current value of their position in the stock."
                )
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .fill(AppColors.cardBackground)
            )
        }
    }

    private func activityExplanation(icon: String, title: String, description: String) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(.system(size: 16))
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: 24)

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

    // MARK: - Key Insights Section

    private var keyInsightsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            Text("Key Insights")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            VStack(spacing: AppSpacing.md) {
                insightCard(
                    number: "1",
                    title: "Institutional Buying is Bullish",
                    description: "When multiple large institutions increase positions, it signals confidence in the stock's prospects."
                )

                insightCard(
                    number: "2",
                    title: "Watch the Net Flow",
                    description: "Positive net flow means more money coming in than going out—a bullish signal. Persistent negative flow can indicate trouble."
                )

                insightCard(
                    number: "3",
                    title: "Size Matters",
                    description: "Large position changes by respected funds (Vanguard, BlackRock, Fidelity) often carry more weight than smaller funds."
                )

                insightCard(
                    number: "4",
                    title: "Look for Patterns",
                    description: "A single quarter's data can be noisy. Look at trends over multiple quarters for clearer signals."
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
                considerationRow("13F filings are delayed 45 days after quarter end, so data may be outdated.")
                considerationRow("Institutions may have already changed positions since filing.")
                considerationRow("Index funds (like Vanguard Total Market) buy automatically, not based on conviction.")
                considerationRow("Always combine with other research—institutional activity is just one data point.")
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
