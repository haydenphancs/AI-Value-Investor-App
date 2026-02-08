//
//  ReportWallStreetSection.swift
//  ios
//
//  Organism: Wall Street Consensus deep dive content
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
        .preferredColorScheme(.dark)
}
