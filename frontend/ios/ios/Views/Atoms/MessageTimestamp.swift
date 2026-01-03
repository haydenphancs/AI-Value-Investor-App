//
//  MessageTimestamp.swift
//  ios
//
//  Atom: Timestamp label for chat messages
//

import SwiftUI

struct MessageTimestamp: View {
    let time: String
    var alignment: HorizontalAlignment = .trailing

    var body: some View {
        Text(time)
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textMuted)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        MessageTimestamp(time: "2:36 PM")
        MessageTimestamp(time: "2:38 PM", alignment: .leading)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
