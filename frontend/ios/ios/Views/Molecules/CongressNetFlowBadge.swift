//
//  CongressNetFlowBadge.swift
//  ios
//
//  Molecule: Net flow badge for Congress activities
//  Shows "Net Flow: +$X.XXM" with bullish/bearish color
//

import SwiftUI

struct CongressNetFlowBadge: View {
    let summary: CongressActivitySummary

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Text("Est. Net Flow:")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)

            Text(summary.formattedNetFlow)
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(summary.netFlowColor)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            CongressNetFlowBadge(
                summary: CongressActivitySummary.sampleData
            )
        }
        .padding()
    }
}
