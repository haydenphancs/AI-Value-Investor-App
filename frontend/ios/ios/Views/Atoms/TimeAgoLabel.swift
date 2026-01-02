//
//  TimeAgoLabel.swift
//  ios
//
//  Atom: Label showing time ago or date for chat history items
//

import SwiftUI

struct TimeAgoLabel: View {
    let text: String

    var body: some View {
        Text(text)
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        TimeAgoLabel(text: "2h ago")
        TimeAgoLabel(text: "1d ago")
        TimeAgoLabel(text: "12/20/2025")
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
