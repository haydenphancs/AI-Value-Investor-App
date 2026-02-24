//
//  IndexDetailAIBar.swift
//  ios
//
//  Molecule: Bottom AI chat bar for Index Detail with suggestions
//

import SwiftUI

struct IndexDetailAIBar: View {
    @Binding var inputText: String
    let indexSymbol: String
    let suggestions: [IndexAISuggestion]
    var onSuggestionTap: ((IndexAISuggestion) -> Void)?
    var onSend: (() -> Void)?

    var body: some View {
        CaudexAIChatBar(
            inputText: $inputText,
            suggestions: suggestions.map(\.text),
            onSuggestionTap: { text in
                if let suggestion = suggestions.first(where: { $0.text == text }) {
                    onSuggestionTap?(suggestion)
                }
            },
            onSend: onSend
        )
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var text = ""

        var body: some View {
            VStack {
                Spacer()
                IndexDetailAIBar(
                    inputText: $text,
                    indexSymbol: "^GSPC",
                    suggestions: IndexAISuggestion.defaultSuggestions
                )
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
