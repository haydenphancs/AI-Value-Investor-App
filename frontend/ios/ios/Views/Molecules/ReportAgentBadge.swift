//
//  ReportAgentBadge.swift
//  ios
//
//  Molecule: Agent persona badge with star rating (e.g. "BUFFETT AGENT ★★★★☆")
//

import SwiftUI

struct ReportAgentBadge: View {
    let agent: ReportAgentPersona

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text(agent.rawValue)
                .font(AppTypography.captionBold)
                .foregroundColor(AppColors.textSecondary)
                .tracking(1.2)

            StarRatingView(
                rating: agent.starRating,
                starSize: 12,
                showValue: false
            )
        }
    }
}

#Preview {
    ReportAgentBadge(agent: .buffett)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
