//
//  ReportTimeSectionHeader.swift
//  ios
//
//  Molecule: iOS-style section band for the grouped Reports list
//  (RECENT / LAST MONTH / OLDER). Mirrors ChatHistorySectionHeader styling
//  so the Reports list reads consistently with chat history.
//

import SwiftUI

struct ReportTimeSectionHeader: View {
    let section: ReportTimeSection

    var body: some View {
        HStack {
            Text(section.rawValue)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textMuted)
            Spacer()
        }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.lg) {
        ReportTimeSectionHeader(section: .recent)
        ReportTimeSectionHeader(section: .lastMonth)
        ReportTimeSectionHeader(section: .older)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
