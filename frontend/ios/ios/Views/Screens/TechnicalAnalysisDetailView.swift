//
//  TechnicalAnalysisDetailView.swift
//  ios
//
//  Full Technical Analysis detail screen
//

import SwiftUI

struct TechnicalAnalysisDetailView: View {
    let detailData: TechnicalAnalysisDetailData
    @Environment(\.dismiss) private var dismiss
    @State private var selectedTimeframe: TechnicalTimeframe = .daily

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Navigation Header
                TechnicalDetailHeader(
                    symbol: detailData.symbol,
                    onBackTapped: { dismiss() }
                )

                // Scrollable content
                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.lg) {
                        // Daily / Weekly picker
                        Picker("Timeframe", selection: $selectedTimeframe) {
                            ForEach(TechnicalTimeframe.allCases, id: \.self) { tf in
                                Text(tf.rawValue).tag(tf)
                            }
                        }
                        .pickerStyle(.segmented)

                        // Moving Averages
                        MovingAveragesSection(
                            indicators: detailData.currentMovingAverages(for: selectedTimeframe),
                            summary: detailData.currentMovingAveragesSummary(for: selectedTimeframe)
                        )

                        // Oscillators
                        OscillatorsSection(
                            indicators: detailData.currentOscillators(for: selectedTimeframe),
                            summary: detailData.currentOscillatorsSummary(for: selectedTimeframe)
                        )

                        // Pivot Points
                        PivotPointsSection(pivotData: detailData.pivotPoints)

                        // Volume Analysis
                        VolumeAnalysisSection(volumeData: detailData.volumeAnalysis)

                        // Fibonacci Retracement
                        FibonacciRetracementSection(fibData: detailData.fibonacciRetracement)

                        // Key Support & Resistance
                        SupportResistanceSection(srData: detailData.supportResistance)

                        // Disclaimer
                        AnalysisDisclaimerText()
                            .padding(.horizontal, AppSpacing.lg)

                        // Bottom spacing
                        Spacer()
                            .frame(height: AppSpacing.xxxl)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.lg)
                }
            }
        }
        .navigationBarHidden(true)
    }
}

// MARK: - Technical Detail Header
struct TechnicalDetailHeader: View {
    let symbol: String
    let onBackTapped: () -> Void

    var body: some View {
        HStack {
            Button(action: onBackTapped) {
                Image(systemName: "chevron.down")
                    .font(AppTypography.iconMedium).fontWeight(.medium)
                    .foregroundColor(AppColors.textPrimary)
            }
            .frame(width: 44, height: 44)

            Spacer()

            Text("\(symbol) Technical Analysis")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)

            Spacer()

            // Spacer to balance the back button
            Spacer()
                .frame(width: 44, height: 44)
        }
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.sm)
        .background(AppColors.background)
    }
}

#Preview {
    TechnicalAnalysisDetailView(detailData: TechnicalAnalysisDetailData.sampleData)
}
