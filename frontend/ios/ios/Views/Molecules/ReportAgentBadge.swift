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
        Text(agent.rawValue)
            .font(AppTypography.captionBold)
            .foregroundColor(AppColors.textSecondary)
            .tracking(1.2)
    }
}

#Preview {
    ReportAgentBadge(agent: .buffett)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
