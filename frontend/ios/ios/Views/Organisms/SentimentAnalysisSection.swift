//
//  SentimentAnalysisSection.swift
//  ios
//
//  Complete Sentiment Analysis section for the Analysis tab
//

import SwiftUI

struct SentimentAnalysisSection: View {
    let sentimentData: SentimentAnalysisData
    @Binding var selectedTimeframe: SentimentTimeframe
    var onMoreTapped: (() -> Void)?

    @State private var showInfoSheet: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            AnalysisSectionHeader(
                title: "Sentiment Analysis",
                onAction: { showInfoSheet = true },
                iconType: .info
            )

            // Market Mood Meter
            HStack {
                Spacer()
                MarketMoodMeter(
                    sentimentData: sentimentData,
                    selectedTimeframe: $selectedTimeframe
                )
                Spacer()
            }

            // Metrics row
            SentimentMetricsRow(sentimentData: sentimentData, selectedTimeframe: selectedTimeframe)
                .padding(.top, AppSpacing.md)

            // Disclaimer — full width so a wrap stays centred under the meter, like the
            // Technical card (it only looked fine because its copy fits one line).
            AnalysisDisclaimerText()
                .frame(maxWidth: .infinity)
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .sheet(isPresented: $showInfoSheet) {
            SentimentInfoSheet()
        }
    }
}

// MARK: - Remembered timeframe

/// The 24H/7D choice, remembered on this device under one key that `TickerDetailView` and
/// `CryptoDetailView` both bind (see `AnalystMomentumPeriod.storageKey` for the history:
/// the same per-ViewModel copy reset it on every pushed screen and relaunch). Both windows
/// always arrive in `SentimentAnalysisData` — an unmeasured one renders its own "—" — so any
/// stored value is displayable and needs no data fallback.
///
/// Device-only, and deliberately NOT cleared by `AppState.discardDataForEndedSession()`: a
/// display choice of this phone, not account data.
extension SentimentTimeframe {
    static let storageKey = "caydex_sentiment_timeframe"
    static let defaultChoice: SentimentTimeframe = .last24h

    /// What is stored — NOT `rawValue`, which is the pill LABEL ("Last 24H"). Never change an id.
    var storageID: String {
        switch self {
        case .last24h: return "last_24h"
        case .last7d:  return "last_7d"
        }
    }

    /// The default for a missing or unknown id. Display-only — never written back.
    static func stored(_ id: String) -> SentimentTimeframe {
        allCases.first { $0.storageID == id } ?? defaultChoice
    }

    /// `@AppStorage`'s string as the toggle's binding; only a tap writes.
    static func binding(_ id: Binding<String>) -> Binding<SentimentTimeframe> {
        Binding(get: { Self.stored(id.wrappedValue) }, set: { id.wrappedValue = $0.storageID })
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SentimentAnalysisSection(
            sentimentData: SentimentAnalysisData.sampleData,
            selectedTimeframe: .constant(.last24h),
            onMoreTapped: {}
        )
        .padding()
    }
}
