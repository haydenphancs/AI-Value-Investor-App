//
//  SignalOfConfidenceSummaryView.swift
//  ios
//
//  Molecule: Summary text view for Signal of Confidence section
//

import SwiftUI

struct SignalOfConfidenceSummaryView: View {
    let summary: SignalOfConfidenceSummary

    var body: some View {
        Text(summary.formattedSummary)
            .font(AppTypography.subheadline)
            .foregroundColor(AppColors.textSecondary)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SignalOfConfidenceSummaryView(
            summary: SignalOfConfidenceSectionData.sampleData.summary
        )
        .padding()
    }
}
