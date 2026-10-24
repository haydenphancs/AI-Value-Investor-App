//
//  DataSourcesView.swift
//  ios
//
//  Screen: attribution for every upstream market-data provider.
//
//  Why this exists: the app displayed data from six providers and credited exactly ONE
//  of them ("Powered by Alternative.me" on the Crypto Fear & Greed card). Most data
//  licenses require attribution, and App Review 5.2.2 requires you to be specifically
//  permitted by each service's terms and to produce that authorization on request.
//
//  Update this list when adding or removing an integration in
//  backend/app/integrations/.
//

import SwiftUI

struct DataSourceItem: Identifiable {
    let id = UUID()
    /// Provider name as they want to be credited.
    let name: String
    /// What Caydex uses them for, in plain language.
    let usage: String
    let url: String
}

struct DataSourcesView: View {
    // Keep in sync with backend/app/integrations/.
    private let items: [DataSourceItem] = [
        DataSourceItem(
            name: "Financial Modeling Prep",
            usage: "Company fundamentals, financial statements, quotes, price history, "
                + "analyst estimates, earnings, insider and institutional filings, "
                + "ETF holdings, and news.",
            url: "https://financialmodelingprep.com"
        ),
        DataSourceItem(
            name: "CoinGecko",
            usage: "Cryptocurrency market data — supply, market cap, fully diluted "
                + "valuation, and trading volume.",
            url: "https://www.coingecko.com"
        ),
        DataSourceItem(
            name: "Federal Reserve Bank of St. Louis (FRED)",
            usage: "Macroeconomic series — inflation, interest rates, yield curves, and "
                + "industry output. This product uses the FRED® API but is not endorsed "
                + "or certified by the Federal Reserve Bank of St. Louis.",
            // FRED's API terms require showing a link to them (2026-10-22).
            url: "https://fred.stlouisfed.org/docs/api/terms_of_use.html"
        ),
        DataSourceItem(
            name: "FINRA",
            usage: "Short-interest data from consolidated equity reporting.",
            url: "https://www.finra.org"
        ),
        DataSourceItem(
            name: "Nasdaq",
            usage: "Short-interest data for Nasdaq-listed securities.",
            url: "https://www.nasdaq.com"
        ),
        DataSourceItem(
            name: "Alternative.me",
            usage: "Crypto Fear & Greed Index.",
            url: "https://alternative.me/crypto/fear-and-greed-index/"
        ),
        DataSourceItem(
            name: "ApeWisdom",
            usage: "Aggregate social-media mention counts for retail sentiment.",
            url: "https://apewisdom.io"
        ),
        DataSourceItem(
            name: "U.S. Securities and Exchange Commission (EDGAR)",
            usage: "Institutional 13F holdings and company filings. Public records.",
            url: "https://www.sec.gov/edgar"
        ),
        DataSourceItem(
            name: "U.S. House and Senate financial disclosures",
            usage: "Congressional trade disclosures filed under the STOCK Act. "
                + "Public records.",
            url: "https://disclosures-clerk.house.gov"
        ),
        DataSourceItem(
            name: "U.S. Census Bureau",
            usage: "Industry revenue data used for the measured US industry revenue. "
                + "This product uses the Census Bureau Data API but is not endorsed or "
                + "certified by the Census Bureau.",
            url: "https://www.census.gov"
        ),
        // Pinned public-domain projection tables (backend/data/industry_tam_projections.json,
        // 2026-10-22): no integration file — the backend reads a committed table.
        DataSourceItem(
            name: "U.S. Bureau of Labor Statistics",
            usage: "Employment Projections industry output and nominal GDP, used to project "
                + "US industry revenue for this year and five years ahead. "
                + "Source: U.S. Bureau of Labor Statistics.",
            url: "https://www.bls.gov/emp/"
        ),
        DataSourceItem(
            name: "Centers for Medicare & Medicaid Services",
            usage: "National Health Expenditure projections, used to project US revenue of "
                + "health insurers and drug wholesalers, and the historical US nominal GDP "
                + "(U.S. Bureau of Economic Analysis data, as printed in its Table 1) behind the "
                + "GDP-based projections.",
            url: "https://www.cms.gov/data-research/statistics-trends-and-reports/national-health-expenditure-data/projected"
        ),
        // Published global market estimates (backend/data/industry_global_markets.json,
        // 2026-10-24): the credit the EU re-use decision and CC BY 4.0 ask for, with the change
        // indicated (Caydex fills and extends the stated years).
        DataSourceItem(
            name: "European Commission",
            usage: "Global chip market estimate from the Chips Act 2.0 proposal, COM(2026) 504 "
                + "(EUR-Lex). Caydex fills the years between its stated figures and extends its "
                + "last rate to five years ahead.",
            url: "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:52026PC0504"
        ),
        DataSourceItem(
            name: "European Commission, Joint Research Centre",
            usage: "Global chip-equipment sales estimate from \"EU's strengths and weaknesses "
                + "in the global semiconductor sector\" (JRC141323). © European Union, 2025, "
                + "licensed under CC BY 4.0. Caydex fills the years between its stated figures "
                + "and extends its last rate to five years ahead.",
            url: "https://publications.jrc.ec.europa.eu/repository/handle/JRC141323"
        ),
        DataSourceItem(
            name: "U.S. International Trade Commission",
            usage: "Global advertising and digital advertising estimates from Recent Trends in "
                + "U.S. Services Trade, 2025 (Publication 5643). Caydex fills and extends the "
                + "years from its stated figure and rate.",
            url: "https://www.usitc.gov/publications/332/pub5643.pdf"
        ),
        DataSourceItem(
            name: "U.S. Food and Drug Administration (openFDA)",
            usage: "Drug and device approval counts for healthcare companies.",
            url: "https://open.fda.gov"
        ),
        DataSourceItem(
            name: "U.S. Patent and Trademark Office",
            usage: "Patent counts used as an innovation signal.",
            url: "https://www.uspto.gov"
        )
    ]

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Text("Caydex presents data from the providers below. We're grateful "
                             + "to each of them.")
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)

                        Text("Data may be delayed and is provided as-is. Where our figures "
                             + "differ from an exchange's or a filing's official record, "
                             + "the official source governs.")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.md)

                    VStack(spacing: 1) {
                        ForEach(items) { item in
                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                Text(item.name)
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textPrimary)
                                Text(item.usage)
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                                Text(item.url)
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(AppSpacing.lg)
                            .background(AppColors.cardBackground)
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
                    // Card on the page background: an edge in light, nothing in dark.
                    .cardBorder(cornerRadius: AppCornerRadius.large)
                    .padding(.horizontal, AppSpacing.lg)

                    Text("Provider names and marks belong to their respective owners. "
                         + "Their inclusion here is attribution, not endorsement of Caydex.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.sm)

                    Spacer().frame(height: AppSpacing.xxxl)
                }
            }
        }
        .navigationTitle("Data Sources")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
    }
}

#Preview {
    NavigationStack { DataSourcesView() }
}
