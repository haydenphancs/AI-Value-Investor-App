//
//  AIBooksSection.swift
//  ios
//
//  Organism: Section showing AI-enabled educational books
//

import SwiftUI

struct AIBooksSection: View {
    let books: [EducationBook]
    var onSeeAll: (() -> Void)?
    var onBookTap: ((EducationBook) -> Void)?
    var onChatWithBook: ((EducationBook) -> Void)?
    var isBookmarked: ((EducationBook) -> Bool)?
    var onToggleBookmark: ((EducationBook) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text("AI-Enabled Books")
                        .font(AppTypography.heading)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Most Read")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                Button(action: {
                    onSeeAll?()
                }) {
                    Text("See All")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Book cards
            VStack(spacing: AppSpacing.md) {
                ForEach(books) { book in
                    EducationBookCard(
                        book: book,
                        isBookmarked: isBookmarked?(book) ?? false,
                        onTap: { onBookTap?(book) },
                        onChatWithBook: { onChatWithBook?(book) },
                        onToggleBookmark: { onToggleBookmark?(book) }
                    )
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        AIBooksSection(books: EducationBook.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
