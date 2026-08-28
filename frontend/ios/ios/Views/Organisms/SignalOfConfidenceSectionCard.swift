//
//  SignalOfConfidenceSectionCard.swift
//  ios
//
//  Organism: Complete Signal of Confidence Section card for the Financial tab
//  Displays dividends, buybacks, and shares outstanding over time
//

import SwiftUI

struct SignalOfConfidenceSectionCard: View {
    // MARK: - Properties

    let signalData: SignalOfConfidenceSectionData
    let onDetailTapped: () -> Void

    // MARK: - State

    @State private var selectedView: SignalOfConfidenceViewType = .yield
    @State private var showInfoSheet: Bool = false

    // MARK: - Body

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with title, info icon, and detail link
            headerSection

            // View toggle (Yield % / Capital $)
            SignalOfConfidenceViewToggle(selectedView: $selectedView)
                .padding(.leading, AppSpacing.xs)

            // Main chart
            SignalOfConfidenceChartView(
                dataPoints: signalData.dataPoints,
                viewType: selectedView
            )
            .padding(.top, AppSpacing.sm)

            // Legend
            SignalOfConfidenceLegendView()
                .frame(maxWidth: .infinity)
                .padding(.top, AppSpacing.sm)

            // Dividend Info — or, for a non-payer, the buyback half on its own.
            // `dividendInfo` is nil for every company that pays no dividend, so this
            // branch used to render nothing at all and the buyback verdict (which
            // never depended on dividends) was silently dropped.
            if let dividendInfo = signalData.dividendInfo {
                DividendInfoCard(
                    dividendInfo: dividendInfo,
                    currentYield: signalData.summary.totalYield
                )
                    .padding(.top, AppSpacing.md)
            } else {
                BuybackOnlyInfoCard(
                    buybackStatus: signalData.summary.buybackStatus,
                    buybackYield: signalData.summary.buybackYield,
                    shareCountChange: signalData.summary.shareCountChange
                )
                    .padding(.top, AppSpacing.md)
            }
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .sheet(isPresented: $showInfoSheet) {
            SignalOfConfidenceInfoSheet()
        }
    }

    // MARK: - Header Section

    private var headerSection: some View {
        HStack {
            HStack(spacing: AppSpacing.sm) {
                Text("Signal of Confidence")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                SignalOfConfidenceInfoIcon {
                    showInfoSheet = true
                }
            }

            Spacer()

            // The "Details" affordance is hidden: all six handlers in
            // TickerDetailViewModel are `print()` stubs — no detail screen
            // exists — so the button did nothing when tapped. The callback
            // parameter is intentionally kept so re-enabling is a one-line
            // change once the drill-down ships.
            // Button(action: onDetailTapped) {
            // Text("Details")
            // .font(AppTypography.bodySmallEmphasis)
            // .foregroundColor(AppColors.primaryBlue)
            // }
            // .buttonStyle(.plain)
        }
    }
}

// MARK: - Preview

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            SignalOfConfidenceSectionCard(
                signalData: SignalOfConfidenceSectionData.sampleData,
                onDetailTapped: {}
            )
            .padding()
        }
    }
}
