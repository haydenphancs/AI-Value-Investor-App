//
//  PeerComparisonUnavailableNote.swift
//  ios
//
//  Molecule: one muted line on a Financials card whose PEER lookup failed upstream — the
//  build answered with `degraded` containing "benchmarks". The company's own figures are
//  complete; only the industry/sector median (the dashed line, the "vs X" label) is missing,
//  so the card says that instead of looking like the company has no peers. Not a retry
//  notice: a peer-only gap never offers one (`TickerDetailViewModel.nonDataLegReasons`).
//
//  Shared by Growth, Profit Power and Health Check so the three say it in the same words.
//

import SwiftUI

struct PeerComparisonUnavailableNote: View {
    static let message = "Peer comparison temporarily unavailable."

    var body: some View {
        Text(Self.message)
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        PeerComparisonUnavailableNote()
            .padding()
    }
}
