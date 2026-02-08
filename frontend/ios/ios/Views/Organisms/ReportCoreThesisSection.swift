//
//  ReportCoreThesisSection.swift
//  ios
//
//  Organism: Core Thesis section combining Bull and Bear cases
//

import SwiftUI

struct ReportCoreThesisSection: View {
    let thesis: ReportCoreThesis

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("The Core Thesis")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            ReportCoreThesisCard(isBullCase: true, bullets: thesis.bullCase)

            ReportCoreThesisCard(isBullCase: false, bullets: thesis.bearCase)
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ReportCoreThesisSection(thesis: TickerReportData.sampleOracle.coreThesis)
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
