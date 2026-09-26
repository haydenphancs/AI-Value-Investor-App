//
//  ReportWallStreetSection.swift
//  ios
//
//  Organism: "Valuation & Institutions" deep dive content (wire key and type name keep the
//  old "Wall Street consensus" spelling) — the Caydex Fair Value Estimate, its range chart,
//  the Institutions (13F) flow and the AI insight. See ReportConsensusBar.
//

import SwiftUI

struct ReportWallStreetSection: View {
    let consensus: ReportWallStreetConsensus

    var body: some View {
        ReportConsensusBar(consensus: consensus)
    }
}

#Preview {
    ReportWallStreetSection(consensus: TickerReportData.sampleOracle.wallStreetConsensus)
        .padding()
        .background(AppColors.cardBackground)
}
