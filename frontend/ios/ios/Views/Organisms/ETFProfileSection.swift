//
//  ETFProfileSection.swift
//  ios
//
//  Organism: ETF Profile section for ETF Detail with expandable content
//  Fetches from GET /api/v1/etfs/{symbol}/profile
//

import SwiftUI

struct ETFProfileSection: View {
    let profile: ETFProfile
    let symbol: String
    var onWebsiteTap: (() -> Void)?
    @State private var isExpanded: Bool = false
    @State private var liveProfile: ETFProfile?

    private let repository: StockRepository = .shared

    private var displayProfile: ETFProfile {
        liveProfile ?? profile
    }

    // Number of lines to show when collapsed
    private let collapsedLineLimit: Int = 3

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title
            Text("\(symbol) Profile")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            // Profile content
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Description with expandable text
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(displayProfile.description)
                        .font(AppTypography.labelSmall)
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
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                // Divider
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                // Info rows
                CompanyProfileRow(label: "Issuer", value: displayProfile.etfCompany)
                CompanyProfileRow(label: "Asset Class", value: displayProfile.assetClass)
                CompanyProfileRow(label: "Inception Date", value: displayProfile.inceptionDate)
                CompanyProfileRow(label: "Domicile", value: displayProfile.domicile)
                CompanyProfileRow(label: "Index Tracked", value: displayProfile.indexTracked)

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
                            Text(displayProfile.website)
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
                .fill(AppColors.cardBackground)
        )
        .task {
            await fetchLiveProfile()
        }
    }

    private func fetchLiveProfile() async {
        do {
            let dto = try await repository.getETFProfile(symbol: symbol)
            await MainActor.run {
                self.liveProfile = dto.toDisplayModel()
            }
            print("[ETFProfile] Live data loaded for \(symbol)")
        } catch {
            print("[ETFProfile] Failed for \(symbol): \(error)")
        }
    }
}

#Preview {
    ScrollView {
        ETFProfileSection(
            profile: ETFProfile(
                description: "The SPDR S&P 500 ETF Trust is the oldest and most well-known exchange-traded fund in the world. Launched in 1993 by State Street Global Advisors, SPY tracks the S&P 500 Index, providing broad exposure to 500 of the largest U.S. companies across all major sectors.",
                symbol: "SPY",
                etfCompany: "State Street Global Advisors",
                assetClass: "Equity",
                inceptionDate: "January 22, 1993",
                domicile: "United States",
                indexTracked: "S&P 500",
                website: "ssga.com"
            ),
            symbol: "SPY"
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
