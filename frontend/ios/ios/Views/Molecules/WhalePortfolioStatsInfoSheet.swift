//
//  WhalePortfolioStatsInfoSheet.swift
//  ios
//
//  Molecule: explains the two stat tiles on the Whale Profile screen.
//
//  It exists because those tiles were being read as a person's TOTAL wealth and
//  TOTAL investment performance, and they are neither. For a foundation trust
//  the gap is enormous — the 13F sleeve can be a small fraction of the assets,
//  and a mandated annual payout drags any portfolio-value-derived figure down,
//  so a modest percent reads as poor performance when it is not a performance
//  number at all.
//
//  Structured like `SmartMoneyInfoSheet` and presented the way this screen
//  already presents `SectorExposureInfoSheet` / `RecentTradesInfoSheet`.
//  Detents are `[.medium, .large]` rather than their `[.medium]` — a deliberate
//  deviation, because this sheet carries five sections rather than three.
//
//  Wording is kept consistent with the existing 45-day-lag copy in
//  `RecentTradesInfoSheet` and `SupportView`, so the app tells one story.
//

import SwiftUI

struct WhalePortfolioStatsInfoSheet: View {
    let profile: WhaleProfile

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    section(
                        title: "What the portfolio figure is",
                        body: "It's the total value of this filer's U.S.-listed "
                            + "stock positions as reported on their most recent SEC "
                            + "Form 13F. It is not their net worth, and not the "
                            + "firm's total assets under management."
                    )

                    section(
                        title: "What a 13F leaves out",
                        body: "Form 13F only covers U.S.-listed stocks. Bonds, cash, "
                            + "private companies, real estate, foreign-listed holdings, "
                            + "short positions and most derivatives never appear on "
                            + "one — so the real portfolio is usually much larger than "
                            + "the figure shown here."
                    )

                    section(
                        title: asOfTitle,
                        body: "13F filings are due 45 days after the quarter ends, so "
                            + "the most recent positions anyone can see are always at "
                            + "least six weeks old and may already have changed. That "
                            + "delay is set by law, not by us."
                    )

                    section(title: returnTitle, body: returnBody)

                    if profile.isStockProxyReturn {
                        section(
                            title: "Why this return looks different",
                            body: "\(profile.returnLabel) is the share price of a "
                                + "publicly traded vehicle since it began trading — not "
                                + "the return of the 13F holdings shown beside it. The "
                                + "two numbers come from different sources and are not "
                                + "directly comparable."
                        )
                    }

                    InlineDisclaimerNotice()
                        .padding(.top, AppSpacing.sm)
                }
                .padding(AppSpacing.lg)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .background(AppColors.background)
            .navigationTitle("About these numbers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }

    // MARK: - Copy that depends on the actual data

    private var asOfTitle: String {
        guard let quarter = profile.portfolioAsOf, !quarter.isEmpty else {
            return "Filings arrive late"
        }
        return "As of \(quarter)"
    }

    private var returnTitle: String {
        profile.hasDisplayableReturn ? "How the return is calculated"
                                     : "Why there's no return shown"
    }

    /// Three genuinely different explanations, because the tile has three states
    /// and a single hedged paragraph would be wrong in two of them.
    private var returnBody: String {
        if !profile.hasDisplayableReturn {
            return profile.returnStatus == "unavailable"
                ? "We couldn't read this filer's performance history, so we're not "
                    + "showing a number rather than guessing at one."
                : "We need at least two full calendar years of year-end filings "
                    + "before quoting an annualized return. A single year isn't a "
                    + "track record, so we show nothing instead."
        }
        if profile.isStockProxyReturn {
            return "It's the compound annual growth rate of the share price since "
                + "the vehicle began trading. Price only — dividends aren't included."
        }
        var text = "It compounds the year-over-year performance of this filer's "
            + "reported 13F stock positions"
        if let years = profile.returnWindowYears, years > 1 {
            text += " across \(years) calendar years"
        }
        text += ". It describes that reported stock book only — it isn't the fund's "
            + "return to investors, and it's before fees."
        return text
    }

    // MARK: - Layout

    @ViewBuilder
    private func section(title: String, body: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Text(title)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text(body)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#Preview("13F CAGR") {
    WhalePortfolioStatsInfoSheet(profile: {
        var p = WhaleProfile.warrenBuffett
        p.returnStatus = "ok"
        p.returnWindowYears = 5
        p.portfolioAsOf = "Q2 2026"
        return p
    }())
}

#Preview("Not enough history") {
    WhalePortfolioStatsInfoSheet(profile: {
        var p = WhaleProfile.warrenBuffett
        p.returnStatus = "insufficient_history"
        p.portfolioAsOf = "Q2 2026"
        return p
    }())
}
