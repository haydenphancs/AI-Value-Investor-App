//
//  ChatWithBookPromptCard.swift
//  ios
//
//  Molecule: Prompt card for chatting with The Intelligent Investor
//

import SwiftUI

struct ChatWithBookPromptCard: View {
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.md) {
                // Book icon with gradient background
                ZStack {
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .fill(
                            LinearGradient(
                                colors: [
                                    Color(hex: "3B82F6"),
                                    Color(hex: "8B5CF6")
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 40, height: 40)

                    Image(systemName: "book.fill")
                        .font(.system(size: 18))
                        .foregroundColor(.white)
                }

                // Text content
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Want to go deeper?")
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Chat with The Intelligent Investor")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                // Arrow
                Image(systemName: "chevron.right")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ChatWithBookPromptCard()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
