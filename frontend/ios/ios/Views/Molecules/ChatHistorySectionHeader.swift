//
//  ChatHistorySectionHeader.swift
//  ios
//
//  Molecule: Section header for chat history (TODAY, YESTERDAY, OLDER)
//

import SwiftUI

/// A plain label, not a control. It used to be a `Button` with an optional `onTap` and a
/// "Today" chevron — but the chevron was commented out and the only caller's handler just
/// printed, so it was a tappable row that did nothing. Nothing navigates from a section.
struct ChatHistorySectionHeader: View {
    let section: ChatHistorySection

    var body: some View {
        HStack {
            Text(section.rawValue)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textMuted)

            Spacer()
        }
        .accessibilityAddTraits(.isHeader)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        ChatHistorySectionHeader(section: .today)
        ChatHistorySectionHeader(section: .yesterday)
        ChatHistorySectionHeader(section: .older)
    }
    .padding()
    .background(AppColors.background)
}


