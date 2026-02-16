//
//  ETFProfileSection.swift
//  ios
//
//  Organism: ETF Profile section for ETF Detail with expandable content
//  Uses FMP (Financial Modeling Prep) data for profile fields
//

import SwiftUI

struct ETFProfileSection: View {
    let profile: ETFProfile
    var onWebsiteTap: (() -> Void)?
    @State private var isExpanded: Bool = false

    // Number of lines to show when collapsed
    private let collapsedLineLimit: Int = 3

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title
            Text("ETF Profile")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            // Profile content
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Description with expandable text
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(profile.description)
                        .font(AppTypography.footnote)
                        .foregroundColor(AppColors.textSecondary)
                        .lineSpacing(4)
                        .lineLimit(isExpanded ? nil : collapsedLineLimit)
                        .fixedSize(horizontal: false, vertical: true)

                    // More/Less button
                    Button(action: {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            isExpanded.toggle()
                        }
                    }) {
                        Text(isExpanded ? "Show less" : "more")
                            .font(AppTypography.footnote)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Info rows
                CompanyProfileRow(label: "Symbol", value: profile.symbol)
                CompanyProfileRow(label: "Issuer", value: profile.etfCompany)
                CompanyProfileRow(label: "Asset Class", value: profile.assetClass)
                CompanyProfileRow(label: "Expense Ratio", value: profile.expenseRatio)
                CompanyProfileRow(label: "Inception Date", value: profile.inceptionDate)
                CompanyProfileRow(label: "Domicile", value: profile.domicile)
                CompanyProfileRow(label: "Index Tracked", value: profile.indexTracked)

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Website
                HStack {
                    Text("Website")
                        .font(AppTypography.footnote)
                        .foregroundColor(AppColors.textSecondary)

                    Spacer()

                    Button(action: {
                        onWebsiteTap?()
                    }) {
                        HStack(spacing: AppSpacing.xs) {
                            Text(profile.website)
                                .font(AppTypography.footnoteBold)
                                .foregroundColor(AppColors.primaryBlue)

                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 10, weight: .semibold))
                                .foregroundColor(AppColors.primaryBlue)
                        }
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    ScrollView {
        ETFProfileSection(
            profile: ETFProfile(
                description: "The SPDR S&P 500 ETF Trust is the oldest and most well-known exchange-traded fund in the world. Launched in 1993 by State Street Global Advisors, SPY tracks the S&P 500 Index, providing broad exposure to 500 of the largest U.S. companies across all major sectors. It is the most liquid ETF on the market, widely used by institutional and retail investors alike for core portfolio allocation, hedging, and tactical trading.",
                symbol: "SPY",
                etfCompany: "State Street Global Advisors",
                assetClass: "Equity",
                expenseRatio: "0.0945%",
                inceptionDate: "January 22, 1993",
                domicile: "United States",
                indexTracked: "S&P 500",
                website: "ssga.com"
            )
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
