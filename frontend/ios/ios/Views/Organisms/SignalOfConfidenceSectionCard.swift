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

            // Summary text
            SignalOfConfidenceSummaryView(summary: signalData.summary)
                .padding(.top, AppSpacing.sm)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
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
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                SignalOfConfidenceInfoIcon {
                    showInfoSheet = true
                }
            }

            Spacer()

            Button(action: onDetailTapped) {
                Text("Detail")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .buttonStyle(.plain)
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
