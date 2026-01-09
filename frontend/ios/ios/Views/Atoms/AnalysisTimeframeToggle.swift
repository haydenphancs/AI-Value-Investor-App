//
//  AnalysisTimeframeToggle.swift
//  ios
//
//  Toggle button for switching between timeframes (6M/1Y, 24H/7D)
//

import SwiftUI

struct AnalysisTimeframeToggle<T: RawRepresentable & CaseIterable & Equatable>: View where T.RawValue == String, T.AllCases: RandomAccessCollection {
    @Binding var selectedOption: T
    let options: [T]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(options, id: \.rawValue) { option in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedOption = option
                    }
                } label: {
                    Text(option.rawValue)
                        .font(AppTypography.captionBold)
                        .foregroundColor(selectedOption == option ? AppColors.textPrimary : AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            selectedOption == option ?
                            AppColors.cardBackgroundLight : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.small)
                }
            }
        }
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.small)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
        )
    }
}

// MARK: - Specific Toggle for Momentum Period
struct MomentumPeriodToggle: View {
    @Binding var selectedPeriod: AnalystMomentumPeriod

    var body: some View {
        AnalysisTimeframeToggle(
            selectedOption: $selectedPeriod,
            options: AnalystMomentumPeriod.allCases.map { $0 }
        )
    }
}

// MARK: - Specific Toggle for Sentiment Timeframe
struct SentimentTimeframeToggleView: View {
    @Binding var selectedTimeframe: SentimentTimeframe

    var body: some View {
        AnalysisTimeframeToggle(
            selectedOption: $selectedTimeframe,
            options: SentimentTimeframe.allCases.map { $0 }
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            MomentumPeriodToggle(selectedPeriod: .constant(.sixMonths))
            SentimentTimeframeToggleView(selectedTimeframe: .constant(.last24h))
        }
    }
}
