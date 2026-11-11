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
    private let card: EarningsLiveResultsCard

    @Environment(\.dismiss) private var dismiss

    init(ticker: String, reportDate: String?) {
        self.ticker = ticker
        self.card = EarningsLiveResultsCard(ticker: ticker, reportDate: reportDate)
    }

    #if DEBUG
    /// Previews only: draws `answer` and never calls the network.
    init(previewing answer: EarningsLiveResponse) {
        self.ticker = answer.ticker
        self.card = EarningsLiveResultsCard(previewing: answer)
    }
    #endif

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                card
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

#if DEBUG
#Preview {
    NavigationStack {
        EarningsLiveScreen(previewing: .sampleData)
    }
}
#endif
