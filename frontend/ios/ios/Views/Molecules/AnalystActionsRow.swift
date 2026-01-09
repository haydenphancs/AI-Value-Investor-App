//
//  AnalystActionsRow.swift
//  ios
//
//  Row of analyst action badges (Upgrades, Maintains, Downgrades)
//

import SwiftUI

struct AnalystActionsRow: View {
    let actionsSummary: AnalystActionsSummary

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            AnalystActionBadge(actionType: .upgrades, count: actionsSummary.upgrades)
            AnalystActionBadge(actionType: .maintains, count: actionsSummary.maintains)
            AnalystActionBadge(actionType: .downgrades, count: actionsSummary.downgrades)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        AnalystActionsRow(actionsSummary: AnalystActionsSummary.sampleData)
            .padding()
    }
}
