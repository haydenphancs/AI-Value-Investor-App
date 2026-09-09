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
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            // Profile content
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Description with expandable text
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(profile.description)
                        .font(AppTypography.body)
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
                            .font(AppTypography.labelSmall)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.primaryBlue)
                            // A bare Text label is a ~17pt target. Padding + a content shape is
                            // what actually grows it: hitSlop's trailing .padding(-inset)
                            // returns the frame and a Button clips its hit region to it, so
                            // slop alone moves nothing here (measured: 21 vs 117 hit points).
                            // The sites that use slop successfully set a .frame first.
                            // 8pt takes a ~17pt text run to ~33pt. Not the full 44:
                            // the remaining 11 would push "more" visibly away from the
                            // paragraph it belongs to, for a control that is already
                            // twice the size it was.
                            .padding(.vertical, AppSpacing.sm)
                            .padding(.trailing, AppSpacing.md)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Info rows
                // These screens are served by an ETF that tracks the index (FMP 402s every
                // `^` symbol — index data is a package we did not buy), so the header, the
                // price and the chart are all the FUND's. Two labels are therefore explicit
                // about which of the two each row describes: "Inception Date" beside a fund
                // name reads as the fund's, and 1957 is the S&P 500's — while "Index
                // Provider" is not the fund's sponsor. Ambiguity here is how a screen
                // quietly asserts something untrue.
                CompanyProfileRow(label: "Exchange", value: profile.exchange)
                CompanyProfileRow(label: "Index Constituents", value: profile.formattedConstituents)
                CompanyProfileRow(label: "Weighting", value: profile.weightingMethodology)
                CompanyProfileRow(label: "Fund Inception", value: profile.inceptionDate)
                CompanyProfileRow(label: "Fund Sponsor", value: profile.indexProvider)

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Website
                HStack {
                    Text("Website")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textSecondary)

                    Spacer()

                    Button(action: {
                        onWebsiteTap?()
                    }) {
                        HStack(spacing: AppSpacing.xs) {
                            Text(profile.website)
                                .font(AppTypography.labelSmallEmphasis)
                                .foregroundColor(AppColors.primaryBlue)

                            Image(systemName: "arrow.up.right")
                                .font(AppTypography.iconTiny).fontWeight(.semibold)
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
                .cardFill()
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
}
