//
//  ReportAgentBadge.swift
//  ios
//
//  Molecule: Agent persona badge (e.g. "ANALYZED BY QUALITY AGENT")
//

import SwiftUI

struct ReportAgentBadge: View {
    let agent: ReportAgentPersona

    var body: some View {
        Text(agent.badgeLabel)
            .font(AppTypography.captionEmphasis)
            .foregroundColor(AppColors.textSecondary)
            .tracking(1.2)
    }
}

#Preview {
    ReportAgentBadge(agent: .buffett)
        .padding()
        .background(AppColors.background)
}
