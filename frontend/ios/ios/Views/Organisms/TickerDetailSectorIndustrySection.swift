//
//  TickerDetailSectorIndustrySection.swift
//  ios
//
//  Organism: Sector & Industry section for Ticker Detail
//

import SwiftUI

struct TickerDetailSectorIndustrySection: View {
    let info: SectorIndustryInfo
    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title with info button inside card styling
            HStack {
                Text("Sector & Industry")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Button(action: {
                    showInfoSheet = true
                }) {
                    Image(systemName: "info.circle")
                        .font(.system(size: 16))
                        .foregroundColor(AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())

                Spacer()
            }

            // Info rows
            VStack(spacing: AppSpacing.md) {
                SectorIndustryRow(label: "Sector", value: info.sector)
                SectorIndustryRow(label: "Industry", value: info.industry)
                SectorIndustryRow(
                    label: "Sector Performance",
                    value: info.formattedPerformance,
                    valueColor: info.performanceColor
                )
                SectorIndustryRow(label: "Industry Rank", value: info.industryRank)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
        .sheet(isPresented: $showInfoSheet) {
            SectorIndustryInfoSheet()
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
        }
    }
}

// MARK: - Sector & Industry Info Sheet
struct SectorIndustryInfoSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.xl) {
                    // Why it matters
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "lightbulb.fill")
                                .font(.system(size: 18))
                                .foregroundColor(AppColors.neutral)
                            Text("Why Sector & Industry Matters")
                                .font(AppTypography.headline)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        Text("Understanding a company's sector and industry helps you:")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)

                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            InfoBulletPoint(
                                icon: "chart.line.uptrend.xyaxis",
                                title: "Compare Performance",
                                description: "Benchmark the stock against similar companies and sector averages."
                            )

                            InfoBulletPoint(
                                icon: "arrow.triangle.branch",
                                title: "Understand Market Cycles",
                                description: "Different sectors perform better in different economic conditions."
                            )

                            InfoBulletPoint(
                                icon: "shield.checkerboard",
                                title: "Diversify Portfolio",
                                description: "Avoid concentration risk by spreading investments across sectors."
                            )

                            InfoBulletPoint(
                                icon: "magnifyingglass.circle",
                                title: "Identify Opportunities",
                                description: "Find undervalued stocks within high-performing industries."
                            )
                        }
                    }

                    // Metrics explained
                    VStack(alignment: .leading, spacing: AppSpacing.md) {
                        Text("Key Metrics Explained")
                            .font(AppTypography.headline)
                            .foregroundColor(AppColors.textPrimary)

                        MetricExplanation(
                            term: "Sector Performance",
                            explanation: "Shows how the overall sector has performed recently, helping you understand macro trends."
                        )

                        MetricExplanation(
                            term: "Industry Rank",
                            explanation: "Indicates where this company stands among its direct competitors based on key metrics."
                        )
                    }
                }
                .padding(AppSpacing.lg)
            }
            .background(AppColors.background)
            .navigationTitle("Sector & Industry")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

// MARK: - Info Bullet Point
struct InfoBulletPoint: View {
    let icon: String
    let title: String
    let description: String

    var body: some View {
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
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

// MARK: - Metric Explanation
struct MetricExplanation: View {
    let term: String
    let explanation: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text(term)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.primaryBlue)

            Text(explanation)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.md)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    ScrollView {
        TickerDetailSectorIndustrySection(
            info: SectorIndustryInfo(
                sector: "Technology",
                industry: "Consumer Electronics",
                sectorPerformance: 2.87,
                industryRank: "#1 of 42"
            )
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}

#Preview("Info Sheet") {
    SectorIndustryInfoSheet()
}
