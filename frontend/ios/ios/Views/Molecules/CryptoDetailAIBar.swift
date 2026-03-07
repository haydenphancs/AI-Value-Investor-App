//
//  CryptoDetailAIBar.swift
//  ios
//
//  Molecule: Bottom AI chat bar for Crypto Detail with suggestions
//

import SwiftUI

struct CryptoDetailAIBar: View {
    @Binding var inputText: String
    let cryptoSymbol: String
    let suggestions: [CryptoAISuggestion]
    var onSuggestionTap: ((CryptoAISuggestion) -> Void)?
    var onSend: (() -> Void)?

    var body: some View {
        CaydexAIChatBar(
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
                CryptoDetailAIBar(
                    inputText: $text,
                    cryptoSymbol: "BTC",
                    suggestions: CryptoAISuggestion.defaultSuggestions
                )
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
