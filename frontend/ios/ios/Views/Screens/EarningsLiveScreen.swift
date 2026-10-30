//
//  EarningsLiveScreen.swift
//  ios
//
//  Screen: one report's live results on their own — where "See live results" goes from a
//  tapped earnings push (`AlertDestination.Target.earningsLive`). Opened by
//  `AlertDestinationCover` inside its own NavigationStack, like every other destination, and
//  the same `EarningsLiveResultsCard` the Tracking sheet shows, so the two never disagree.
//

import SwiftUI

struct EarningsLiveScreen: View {
    let ticker: String
    let reportDate: String?

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                EarningsLiveResultsCard(ticker: ticker, reportDate: reportDate)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.xl)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("\(ticker.uppercased()) Earnings")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Done") { dismiss() }
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }
        }
    }
}

#Preview {
    NavigationStack {
        EarningsLiveScreen(ticker: "INTC", reportDate: "2026-10-29")
    }
}
