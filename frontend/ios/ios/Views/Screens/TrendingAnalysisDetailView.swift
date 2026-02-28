//
//  TrendingAnalysisDetailView.swift
//  ios
//
//  Screen: Detail view for a trending analysis topic
//  Shows sector overview, list of companies, and analysis stats
//

import SwiftUI

struct TrendingAnalysisDetailView: View {
    let analysis: TrendingAnalysis
    var onAnalyzeTicker: ((String) -> Void)?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Header
                headerSection

                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        // Hero section
                        heroSection

                        // Stats row
                        statsSection

                        // Companies list
                        companiesSection

                        Spacer()
                            .frame(height: AppSpacing.xxxl)
                    }
                    .padding(.top, AppSpacing.lg)
                }
            }
        }
        .navigationBarHidden(true)
    }

    // MARK: - Header

    private var headerSection: some View {
        HStack {
            Button(action: { dismiss() }) {
                Image(systemName: "chevron.left")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
            }

            Spacer()

            Text(analysis.title)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .lineLimit(1)

            Spacer()

            // Invisible spacer to balance the back button
            Image(systemName: "chevron.left")
                .font(AppTypography.iconDefault).fontWeight(.semibold)
                .foregroundColor(.clear)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
    }

    // MARK: - Hero

    private var heroSection: some View {
        VStack(spacing: AppSpacing.md) {
            ZStack {
                Circle()
                    .fill(analysis.iconBackgroundColor.opacity(0.2))
                    .frame(width: 72, height: 72)

                Image(systemName: analysis.systemIconName)
                    .font(.system(size: 32, weight: .semibold))
                    .foregroundColor(analysis.iconBackgroundColor)
            }

            Text(analysis.description)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xxl)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: - Stats

    private var statsSection: some View {
        HStack(spacing: AppSpacing.md) {
            statCard(
                value: "\(analysis.companiesCount)",
                label: "Companies",
                iconName: "building.2.fill",
                color: analysis.iconBackgroundColor
            )

            statCard(
                value: "+\(analysis.interestPercent)%",
                label: "Interest",
                iconName: "arrow.up.right",
                color: Color(hex: "22C55E")
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private func statCard(value: String, label: String, iconName: String, color: Color) -> some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: iconName)
                .font(AppTypography.iconDefault).fontWeight(.semibold)
                .foregroundColor(color)

            Text(value)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }

    // MARK: - Companies

    private var companiesSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Key Players")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            LazyVStack(spacing: AppSpacing.sm) {
                ForEach(analysis.companies) { company in
                    companyRow(company)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private func companyRow(_ company: TrendingCompany) -> some View {
        HStack(spacing: 0) {
            // Left: Identity
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(company.ticker)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(company.name)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
            }
            .frame(width: 150, alignment: .leading)

            // Middle: Market Pulse
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(company.price)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(company.marketCap)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            .frame(width: 90, alignment: .leading)

            Spacer()

            // Right: Action
            Button(action: {
                onAnalyzeTicker?(company.ticker)
                dismiss()
            }) {
                Text("Analyze")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.primaryBlue)
            }
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        TrendingAnalysisDetailView(analysis: TrendingAnalysis.mockTrending[0])
    }
    .preferredColorScheme(.dark)
}
