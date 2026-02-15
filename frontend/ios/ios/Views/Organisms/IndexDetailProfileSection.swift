//
//  IndexDetailProfileSection.swift
//  ios
//
//  Organism: Index Profile section for Index Detail with expandable content
//

import SwiftUI

struct IndexDetailProfileSection: View {
    let profile: IndexProfile
    var onWebsiteTap: (() -> Void)?
    @State private var isExpanded: Bool = false

    // Number of lines to show when collapsed
    private let collapsedLineLimit: Int = 3

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title
            Text("Index Profile")
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
                CompanyProfileRow(label: "Exchange", value: profile.exchange)
                CompanyProfileRow(label: "Constituents", value: profile.formattedConstituents)
                CompanyProfileRow(label: "Weighting", value: profile.weightingMethodology)
                CompanyProfileRow(label: "Inception Date", value: profile.inceptionDate)
                CompanyProfileRow(label: "Index Provider", value: profile.indexProvider)

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
        IndexDetailProfileSection(
            profile: IndexProfile(
                description: "The S&P 500 Index is a market-capitalization-weighted index of 500 leading publicly traded companies in the U.S. It is widely regarded as the best single gauge of large-cap U.S. equities and serves as the foundation for a wide range of investment products.",
                exchange: "NYSE / NASDAQ",
                numberOfConstituents: 503,
                weightingMethodology: "Market-Cap Weighted",
                inceptionDate: "March 4, 1957",
                indexProvider: "S&P Dow Jones Indices",
                website: "www.spglobal.com"
            )
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
