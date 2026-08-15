//
//  TimeAgoLabel.swift
//  ios
//
//  Atom: Label showing time ago or date for chat history items
//

import SwiftUI

struct TimeAgoLabel: View {
    let text: String
    /// Defaults to the chat-history look. `MoneyMoveCard` passes `textSecondary` because this
    /// sits immediately beside a `ReadTimeLabel`, which uses that ink — two different greys in
    /// a two-item meta row reads as a bug rather than a hierarchy.
    var color: Color = AppColors.textMuted

    var body: some View {
        Text(text)
            .font(AppTypography.caption)
            .foregroundColor(color)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        TimeAgoLabel(text: "2h ago")
        TimeAgoLabel(text: "1d ago")
        TimeAgoLabel(text: "12/20/2025")
        TimeAgoLabel(text: "Yesterday", color: AppColors.textSecondary)
    }
    .padding()
    .background(AppColors.background)
}
