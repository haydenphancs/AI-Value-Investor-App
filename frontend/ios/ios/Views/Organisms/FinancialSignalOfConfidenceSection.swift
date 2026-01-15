//
//  FinancialSignalOfConfidenceSection.swift
//  ios
//
//  Organism: Signal of Confidence section showing dividends and buybacks
//

import SwiftUI

struct FinancialSignalOfConfidenceSection: View {
    let data: SignalOfConfidenceData
    @Binding var selectedViewType: SignalViewType
    var onDetailTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header
            FinancialSectionHeader(
                title: "Signal of Confidence",
                infoTitle: "Signal of Confidence",
                infoDescription: FinancialInfoContent.signalOfConfidence,
                showDetailLink: true,
                onDetailTap: onDetailTap
            )

            // View type toggle
            HStack {
                FinancialSegmentedControl(
                    selection: $selectedViewType,
                    style: .toggle
                )
                Spacer()
            }

            // Chart
            SignalOfConfidenceChart(
                data: data,
                viewType: selectedViewType
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            FinancialSignalOfConfidenceSection(
                data: SignalOfConfidenceData.sampleData,
                selectedViewType: .constant(.yield)
            )
            .padding(.vertical)
        }
    }
}
