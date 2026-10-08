//
//  TickerFinancialsContent.swift
//  ios
//
//  Organism: Financials tab content combining all financial sections for Ticker Detail
//

import SwiftUI

struct TickerFinancialsContent: View {
    let earningsData: EarningsData?
    let growthData: GrowthSectionData?
    let profitPowerData: ProfitPowerSectionData?
    let signalOfConfidenceData: SignalOfConfidenceSectionData?
    let revenueBreakdownData: RevenueBreakdownData?
    let healthCheckData: HealthCheckSectionData?
    /// False while the Financials fetches are still in flight. Without it, a
    /// loading tab and a tab whose backend returned nothing render identically
    /// (six missing cards), so the user can't tell which they're looking at.
    var isLoaded: Bool = true
    /// Why the tab has nothing to show, already mapped through `AppError` by the ViewModel
    /// (never a raw backend string). nil means every fetch answered — an empty answer is
    /// "isn't available for this company", a FAILURE is this card with a retry.
    var loadFailureMessage: String? = nil
    /// Sections whose fetch failed while others loaded — named in an inline retry notice.
    var failedSectionNames: [String] = []
    var isRetrying: Bool = false
    var onRetry: (() -> Void)?
    var onEarningsDetailTap: (() -> Void)?
    var onGrowthDetailTap: (() -> Void)?
    var onProfitPowerDetailTap: (() -> Void)?
    var onSignalOfConfidenceDetailTap: (() -> Void)?
    var onRevenueBreakdownDetailTap: (() -> Void)?
    var onHealthCheckDetailTap: (() -> Void)?
    /// The Growth / Profit Power build lost a DATA leg upstream (an FMP 429): an empty
    /// series then reads "temporarily unavailable", not "isn't available for this company".
    var growthIsDegraded: Bool = false
    var profitPowerIsDegraded: Bool = false
    /// The section's PEER lookup failed upstream (`degraded` holds "benchmarks"): the card is
    /// complete except for the peer median, and says so in one muted line. No retry notice.
    var growthPeerUnavailable: Bool = false
    var profitPowerPeerUnavailable: Bool = false
    var healthCheckPeerUnavailable: Bool = false

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Earnings Section
            if let earningsData = earningsData {
                EarningsSectionCard(
                    earningsData: earningsData,
                    onDetailTap: {
                        onEarningsDetailTap?()
                    },
                    onRetry: onRetry
                )
            }

            // No Street Estimates card here (or anywhere): it sat on the Analysis tab until
            // the Valuation Meter took that slot (2026-09-17), moved under Earnings for a
            // day, and was then dropped outright at the developer's request. The forward
            // estimates still arrive on `AnalystRatingsData` (Cay AI grounding) — they are
            // simply not rendered as a card.

            // Growth Section
            if let growthData = growthData {
                GrowthSectionCard(
                    growthData: growthData,
                    isDegraded: growthIsDegraded,
                    peerComparisonUnavailable: growthPeerUnavailable,
                    onDetailTapped: {
                        onGrowthDetailTap?()
                    }
                )
            }

            // Revenue Breakdown Section ("How TICKER Makes Money")
            if let revenueBreakdownData = revenueBreakdownData {
                RevenueBreakdownSectionCard(
                    data: revenueBreakdownData,
                    onDetailTapped: {
                        onRevenueBreakdownDetailTap?()
                    }
                )
            }

            // Profit Power Section
            if let profitPowerData = profitPowerData {
                ProfitPowerSectionCard(
                    profitPowerData: profitPowerData,
                    onDetailTapped: {
                        onProfitPowerDetailTap?()
                    },
                    isDegraded: profitPowerIsDegraded,
                    peerComparisonUnavailable: profitPowerPeerUnavailable
                )
            }

            // Health Check Section
            if let healthCheckData = healthCheckData {
                HealthCheckSectionCard(
                    healthCheckData: healthCheckData,
                    onDetailTapped: {
                        onHealthCheckDetailTap?()
                    },
                    peerComparisonUnavailable: healthCheckPeerUnavailable
                )
            }

            // Signal of Confidence Section
            if let signalOfConfidenceData = signalOfConfidenceData {
                SignalOfConfidenceSectionCard(
                    signalData: signalOfConfidenceData,
                    onDetailTapped: {
                        onSignalOfConfidenceDetailTap?()
                    }
                )
            }

            // Still loading, failed, or genuinely empty — say which.
            if !isLoaded && !hasAnySection {
                loadingPlaceholder
            } else if isLoaded && !hasAnySection, let loadFailureMessage {
                // A failed load (network down, overview + fallbacks failed, every fetch
                // errored) is not a property of the company: it gets a reason and a retry.
                DetailLoadFailureCard(
                    message: loadFailureMessage,
                    title: "Couldn't load financials",
                    isRetrying: isRetrying,
                    onRetry: onRetry
                )
            } else if isLoaded && !hasAnySection {
                ChartUnavailableView(
                    message: "Financial data isn't available for this company right now.",
                    systemImage: "doc.text.magnifyingglass"
                )
                .padding(.vertical, AppSpacing.xl)
            } else if isLoaded && !failedSectionNames.isEmpty {
                // Some sections loaded, others failed. A typed backend error is not
                // auto-retried by APIClient, so without this the failed card would simply
                // be missing with no way back short of pull-to-refresh.
                // A retry keeps the tab settled (`isLoaded` stays true), so the notice stays
                // up through it, says so, and cannot be tapped again until it lands.
                InlineRetryNotice(
                    message: "Couldn't load \(failedSectionNames.joined(separator: ", ")).",
                    retryTitle: isRetrying ? "Retrying\u{2026}" : "Try Again",
                    onRetry: onRetry
                )
                .disabled(isRetrying)
            }

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: AppSpacing.aiBarReserve)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }

    private var hasAnySection: Bool {
        earningsData != nil || growthData != nil || revenueBreakdownData != nil
            || profitPowerData != nil || healthCheckData != nil
            || signalOfConfidenceData != nil
    }

    private var loadingPlaceholder: some View {
        VStack(spacing: AppSpacing.lg) {
            ForEach(0..<3, id: \.self) { _ in
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .cardFill()
                    .frame(height: 180)
                    .shimmer()
            }
        }
        .accessibilityLabel("Loading financials")
    }
}

#Preview {
    ScrollView {
        TickerFinancialsContent(
            earningsData: EarningsData.sampleData,
            growthData: GrowthSectionData.sampleData,
            profitPowerData: ProfitPowerSectionData.sampleData,
            signalOfConfidenceData: SignalOfConfidenceSectionData.sampleData,
            revenueBreakdownData: RevenueBreakdownData.sampleApple,
            healthCheckData: HealthCheckSectionData.sampleData
        )
    }
    .background(AppColors.background)
}

#Preview("Load failed") {
    // No sample market data on a failed load — the tab shows the reason and a retry.
    ScrollView {
        TickerFinancialsContent(
            earningsData: nil,
            growthData: nil,
            profitPowerData: nil,
            signalOfConfidenceData: nil,
            revenueBreakdownData: nil,
            healthCheckData: nil,
            isLoaded: true,
            loadFailureMessage: "Unable to connect. Check your internet connection.",
            onRetry: {}
        )
    }
    .background(AppColors.background)
}

#Preview("Partial failure") {
    ScrollView {
        TickerFinancialsContent(
            earningsData: EarningsData.sampleData,
            growthData: nil,
            profitPowerData: ProfitPowerSectionData.sampleData,
            signalOfConfidenceData: nil,
            revenueBreakdownData: nil,
            healthCheckData: nil,
            isLoaded: true,
            loadFailureMessage: "Our market data provider is temporarily unavailable. Try again shortly.",
            failedSectionNames: ["Growth", "Health Check"],
            onRetry: {}
        )
    }
    .background(AppColors.background)
}
