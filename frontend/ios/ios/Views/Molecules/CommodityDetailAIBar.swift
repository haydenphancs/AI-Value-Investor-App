//
//  CommodityDetailAIBar.swift
//  ios
//
//  Molecule: Bottom AI chat bar for Commodity Detail with suggestions
//

import SwiftUI

struct CommodityDetailAIBar: View {
    @Binding var inputText: String
    let commoditySymbol: String
    let suggestions: [CommodityAISuggestion]
    var onSuggestionTap: ((CommodityAISuggestion) -> Void)?
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
                CommodityDetailAIBar(
                    inputText: $text,
                    commoditySymbol: "GCUSD",
                    suggestions: CommodityAISuggestion.defaultSuggestions
                )
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
