//
//  TickerAnalysisContent.swift
//  ios
//
//  Organism: Analysis tab content combining all analysis sections for Ticker Detail
//

import SwiftUI

struct TickerAnalysisContent: View {
    let analystRatingsData: AnalystRatingsData?
    let sentimentAnalysisData: SentimentAnalysisData?
    let technicalAnalysisData: TechnicalAnalysisData?
    /// The Overview's valuation ("Price") snapshot — multiples vs sector plus FMP's DCF —
    /// rendered here as the Valuation Meter. Defaulted nil: `CryptoDetailView` shares
    /// this view and has no equity valuation.
    var valuationSnapshot: SnapshotItem? = nil
    /// Live header price for the DCF gap; nil renders the model value without a gap.
    var currentPrice: Double? = nil
    var fearGreedData: CryptoFearGreedData? = nil
    let isAnalystLoaded: Bool
    var isFearGreedLoaded: Bool = true
    let isSentimentLoaded: Bool
    let isTechnicalLoaded: Bool
    @Binding var selectedMomentumPeriod: AnalystMomentumPeriod
    @Binding var selectedSentimentTimeframe: SentimentTimeframe
    var selectedFearGreedTimeframe: Binding<FearGreedTimeframe>? = nil
    var onAnalystRatingsMoreTap: (() -> Void)?
    var onAnalystActionsTap: (() -> Void)?
    var onSentimentMoreTap: (() -> Void)?
    var onTechnicalDetailTap: (() -> Void)?
    /// Set by the view model when the technical fetch FAILED. Without this branch the
    /// card simply vanished: a transient blip looked identical to an unsupported asset
    /// and offered no way back (the Index/Commodity screens had the branch; the stock
    /// and crypto screens, which share this view, did not).
    var technicalUnavailableMessage: String? = nil
    var technicalIsRetryable: Bool = false
    var onRetryTechnical: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Valuation Meter — an ADDITIVE SIBLING above the analyst chain, in the slot the
            // Street Estimates card held until 2026-09-17 (TestFlight E9: the developer did
            // not want forward consensus on this tab; it now sits under Earnings on the
            // Financials tab). Multiples vs sector are entitled and already computed for the
            // Overview's Valuation card; FMP's DCF rides on the same snapshot.
            //
            // Deliberately does NOT read `sectionAvailable`: that flag guards the zero-default
            // consensus and price target, and reusing it here would put a HOLD at $0.00 back
            // on screen for a client that cannot update.
            if let snapshot = valuationSnapshot {
                ValuationMeterSection(snapshot: snapshot, currentPrice: currentPrice)
            }

            // Fear & Greed Index (crypto) OR Analyst Ratings (stocks)
            if let fgData = fearGreedData,
               let fgTimeframe = selectedFearGreedTimeframe {
                CryptoFearGreedSection(data: fgData, selectedTimeframe: fgTimeframe)
            } else if !isFearGreedLoaded && analystRatingsData == nil {
                analysisSectionPlaceholder(height: 280)
            } else if let ratingsData = analystRatingsData, !ratingsData.sectionAvailable {
                // Nothing at all. The analyst packages (grades, price targets) are outside the
                // signed FMP licence, so we cannot ask — and `noAnalystCoverageCard` would
                // then state that no analyst covers Apple, which is false. An empty state is
                // honest only when the source HAS no data; when we stopped paying for it, the
                // honest thing is to show no section. Checked BEFORE `hasCoverage`, because
                // an unlicensed source always looks like zero coverage.
                EmptyView()
            } else if let ratingsData = analystRatingsData, !ratingsData.hasCoverage {
                // No analyst covers this ticker. Rendering the section anyway printed a
                // confident "HOLD" consensus over a $0.00 low / $0.00 average / $0.00
                // high target — a fabricated verdict for a company nobody has an opinion
                // on. Say that instead.
                noAnalystCoverageCard
            } else if let ratingsData = analystRatingsData {
                AnalystRatingsSection(
                    ratingsData: ratingsData,
                    selectedMomentumPeriod: $selectedMomentumPeriod,
                    onMoreTapped: {
                        onAnalystRatingsMoreTap?()
                    },
                    onActionsTapped: {
                        onAnalystActionsTap?()
                    }
                )
            } else if !isAnalystLoaded {
                analysisSectionPlaceholder(height: 280)
            }

            // Sentiment Analysis Section
            if let sentimentData = sentimentAnalysisData {
                SentimentAnalysisSection(
                    sentimentData: sentimentData,
                    selectedTimeframe: $selectedSentimentTimeframe,
                    onMoreTapped: {
                        onSentimentMoreTap?()
                    }
                )
            } else if !isSentimentLoaded {
                analysisSectionPlaceholder(height: 200)
            }

            // Technical Analysis Section
            if let technicalData = technicalAnalysisData {
                TechnicalAnalysisSection(
                    technicalData: technicalData,
                    onDetailTapped: {
                        onTechnicalDetailTap?()
                    }
                )
            } else if !isTechnicalLoaded {
                analysisSectionPlaceholder(height: 180)
            } else if let message = technicalUnavailableMessage {
                // Loaded, nothing to show, and the reason. Mirrors IndexDetailView.
                if technicalIsRetryable, let retry = onRetryTechnical {
                    InlineRetryNotice(message: message, onRetry: retry)
                } else {
                    // Permanent for this asset — a Try Again would promise something
                    // that can never succeed.
                    ChartUnavailableView(message: message)
                        .frame(height: 180)
                }
            }

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: AppSpacing.aiBarReserve)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }

    /// Honest empty state for a ticker with no analyst coverage.
    private var noAnalystCoverageCard: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "person.2.slash")
                .font(AppTypography.iconLarge)
                .foregroundColor(AppColors.textMuted)
            Text("No Analyst Coverage")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)
            Text("No Wall Street analyst currently publishes a rating or price target for this stock.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(AppSpacing.lg)
        .cardSurface()
    }

    private func analysisSectionPlaceholder(height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 12)
            .cardFill()
            .frame(height: height)
            .shimmer()
    }
}

#Preview {
    ScrollView {
        TickerAnalysisContent(
            analystRatingsData: AnalystRatingsData.sampleData,
            sentimentAnalysisData: nil,
            technicalAnalysisData: TechnicalAnalysisData.sampleData,
            isAnalystLoaded: true,
            isSentimentLoaded: false,
            isTechnicalLoaded: true,
            selectedMomentumPeriod: .constant(.sixMonths),
            selectedSentimentTimeframe: .constant(.last24h)
        )
    }
    .background(AppColors.background)
}
