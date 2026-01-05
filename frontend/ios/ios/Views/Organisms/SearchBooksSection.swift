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
    var onReadKeyIdeas: ((SearchBookItem) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            Text("AI-Enabled Books")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Book cards
            VStack(spacing: AppSpacing.md) {
                ForEach(books) { book in
                    SearchBookCard(
                        book: book,
                        onChatWithBook: { onChatWithBook?(book) },
                        onReadKeyIdeas: { onReadKeyIdeas?(book) }
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
