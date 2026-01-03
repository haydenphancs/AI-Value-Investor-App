//
//  UserMessageBubble.swift
//  ios
//
//  Molecule: User message bubble (right-aligned)
//

import SwiftUI

struct UserMessageBubble: View {
    let text: String
    let timestamp: String

    var body: some View {
        VStack(alignment: .trailing, spacing: AppSpacing.xs) {
            Text(text)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.cardBackgroundLight)
                .cornerRadius(AppCornerRadius.large)

            MessageTimestamp(time: timestamp)
        }
        .frame(maxWidth: .infinity, alignment: .trailing)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        UserMessageBubble(
            text: "What's the current sentiment around Tesla stock?",
            timestamp: "2:36 PM"
        )
        UserMessageBubble(
            text: "How's Tesla's stock performance?",
            timestamp: "2:38 PM"
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
