//
//  ChatWithBookPromptCard.swift
//  ios
//
//  Molecule: prompt card offering a chat about a book.
//
//  The title is INJECTED rather than hardcoded here: the label the user reads and the
//  book the chat is actually seeded with have to come from one place, or they drift.
//  They already had — this card said "The Intelligent Investor" while the handler
//  behind it was a `print()` that opened nothing at all.
//

import SwiftUI

struct ChatWithBookPromptCard: View {
    let bookTitle: String
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
                                    AppColors.primaryBlue,
                                    AppColors.alertPurple
                                ],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 40, height: 40)

                    Image(systemName: "book.fill")
                        .font(AppTypography.iconMedium)
                        .foregroundColor(AppColors.textOnAccent)
                }

                // Text content
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Want to go deeper?")
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Chat with \(bookTitle)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                // Arrow
                Image(systemName: "chevron.right")
                    .font(AppTypography.iconSmall).fontWeight(.medium)
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(AppSpacing.lg)
            .cardSurface(cornerRadius: AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ChatWithBookPromptCard(bookTitle: "The Intelligent Investor")
        .padding()
        .background(AppColors.background)
}
