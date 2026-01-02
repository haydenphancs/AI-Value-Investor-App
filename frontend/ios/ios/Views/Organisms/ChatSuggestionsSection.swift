//
//  ChatSuggestionsSection.swift
//  ios
//
//  Organism: Section with suggestion chips for chat
//

import SwiftUI

struct ChatSuggestionsSection: View {
    let suggestions: [SuggestionChip]
    var onSuggestionTap: ((SuggestionChip) -> Void)?

    var body: some View {
        SuggestionChipsRow(chips: suggestions) { chip in
            onSuggestionTap?(chip)
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack {
        Spacer()
        ChatSuggestionsSection(suggestions: SuggestionChip.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
