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

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Navigation Header
                TechnicalDetailHeader(
                    symbol: detailData.symbol,
                    onBackTapped: { dismiss() },
                    onInfoTapped: { /* Info action */ }
                )

                // Scrollable content
                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.lg) {
                        // Moving Averages
                        MovingAveragesSection(
                            indicators: detailData.movingAverages,
                            summary: detailData.movingAveragesSummary
                        )

                        // Oscillators
                        OscillatorsSection(
                            indicators: detailData.oscillators,
                            summary: detailData.oscillatorsSummary
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
    let onInfoTapped: () -> Void

    var body: some View {
        HStack {
            Button(action: onBackTapped) {
                Image(systemName: "chevron.left")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundColor(AppColors.textPrimary)
            }
            .frame(width: 44, height: 44)

            Spacer()

            Text("\(symbol) Technical Analysis")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            Spacer()

            Button(action: onInfoTapped) {
                Image(systemName: "info.circle")
                    .font(.system(size: 18))
                    .foregroundColor(AppColors.textMuted)
            }
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
