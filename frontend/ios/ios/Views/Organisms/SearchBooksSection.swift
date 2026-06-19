//
//  SearchBooksSection.swift
//  ios
//
//  Organism: Section displaying AI-enabled books in search
//

import SwiftUI

struct SearchBooksSection: View {
    let books: [SearchBookItem]
    var onChatWithBook: ((SearchBookItem) -> Void)?
    var isBookmarked: ((SearchBookItem) -> Bool)?
    var onToggleBookmark: ((SearchBookItem) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            Text("AI-Enabled Books")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Book cards
            VStack(spacing: AppSpacing.md) {
                ForEach(books) { book in
                    SearchBookCard(
                        book: book,
                        isBookmarked: isBookmarked?(book) ?? false,
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
        SearchBooksSection(books: SearchBookItem.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
