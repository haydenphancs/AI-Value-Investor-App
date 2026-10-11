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
    /// What VoiceOver reads for an option; nil reads its visible label (`rawValue`). For
    /// abbreviated labels — "7D" is read as "seven D" — pass spoken ones ("7 days").
    var accessibilityLabelFor: ((T) -> String)? = nil

    var body: some View {
        HStack(spacing: 0) {
            ForEach(options, id: \.rawValue) { option in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedOption = option
                    }
                } label: {
                    Text(option.rawValue)
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(selectedOption == option ? AppColors.textPrimary : AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            selectedOption == option ?
                            AppColors.cardBackgroundLight : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.small)
                }
                .accessibilityLabel(spokenLabel(option))
                // The fill is the only visual cue besides the ink, so VoiceOver must hear it.
                .accessibilityAddTraits(selectedOption == option ? [.isButton, .isSelected] : .isButton)
            }
        }
        .cardSurface(cornerRadius: AppCornerRadius.small)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
        )
    }

    private func spokenLabel(_ option: T) -> String {
        accessibilityLabelFor?(option) ?? option.rawValue
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
