//
//  ReportInsiderSection.swift
//  ios
//
//  Organism: Insider & Management deep dive content combining activity table and management info
//

import SwiftUI

struct ReportInsiderSection: View {
    let insiderData: ReportInsiderData
    let management: ReportKeyManagement

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Insider Activity
            ReportInsiderActivityTable(insiderData: insiderData)

            // Key Management
            ReportKeyManagementTable(management: management)
        }
    }
}

#Preview {
    ReportInsiderSection(
        insiderData: TickerReportData.sampleOracle.insiderData,
        management: TickerReportData.sampleOracle.keyManagement
    )
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
