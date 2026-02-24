//
//  ETFDetailAIBar.swift
//  ios
//
//  Molecule: Bottom AI chat bar for ETF Detail with suggestions
//

import SwiftUI

struct ETFDetailAIBar: View {
    @Binding var inputText: String
    let etfSymbol: String
    let suggestions: [ETFAISuggestion]
    var onSuggestionTap: ((ETFAISuggestion) -> Void)?
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
                ETFDetailAIBar(
                    inputText: $text,
                    etfSymbol: "SPY",
                    suggestions: ETFAISuggestion.defaultSuggestions
                )
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
