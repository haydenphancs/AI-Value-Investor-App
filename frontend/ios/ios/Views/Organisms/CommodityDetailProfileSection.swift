//
//  CommodityDetailProfileSection.swift
//  ios
//
//  Organism: Commodity Profile section with expandable content
//

import SwiftUI

struct CommodityDetailProfileSection: View {
    let profile: CommodityProfile
    @State private var isExpanded: Bool = false

    // Number of lines to show when collapsed
    private let collapsedLineLimit: Int = 3

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title
            Text("Profile")
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

                // Category badge
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: profile.category.iconName)
                        .font(.system(size: 12))
                        .foregroundColor(profile.category.color)
                    Text(profile.category.rawValue)
                        .font(AppTypography.footnoteBold)
                        .foregroundColor(profile.category.color)
                }
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    Capsule()
                        .fill(profile.category.color.opacity(0.15))
                )

                // Info rows
                CompanyProfileRow(label: "Exchange", value: profile.exchange)
                CompanyProfileRow(label: "Trading Hours", value: profile.tradingHours)
                CompanyProfileRow(label: "Contract Size", value: profile.contractSize)
                CompanyProfileRow(label: "Unit", value: profile.formattedUnit)
                CompanyProfileRow(label: "Currency", value: profile.currency)
                CompanyProfileRow(label: "Tick Size", value: profile.tickSize)

                // Additional info shown when expanded
                if isExpanded {
                    // Divider
                    Rectangle()
                        .fill(AppColors.cardBackgroundLight)
                        .frame(height: 1)

                    CompanyProfileRow(label: "Major Producers", value: profile.majorProducers)
                    CompanyProfileRow(label: "Major Consumers", value: profile.majorConsumers)
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
        CommodityDetailProfileSection(
            profile: CommodityProfile(
                description: "Gold is a precious metal that has been valued throughout human history as a store of wealth, medium of exchange, and safe-haven asset. It is widely used in jewelry, electronics, and central bank reserves. Gold prices are influenced by inflation, interest rates, geopolitical tensions, and currency movements, particularly the US dollar.",
                category: .metals,
                exchange: "COMEX / NYMEX",
                tradingHours: "Sun-Fri 6:00 PM - 5:00 PM ET",
                contractSize: "100 troy ounces",
                unit: .troyOunce,
                currency: "USD",
                tickSize: "$0.10",
                majorProducers: "China, Australia, Russia, USA, Canada",
                majorConsumers: "China, India, USA, Germany, Turkey",
                website: nil
            )
        )
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
