//
//  TickerDetailAIBar.swift
//  ios
//
//  Molecule: Bottom AI chat bar for Ticker Detail with suggestions
//

import SwiftUI

struct TickerDetailAIBar: View {
    @Binding var inputText: String
    let tickerSymbol: String
    let suggestions: [TickerAISuggestion]
    var onSuggestionTap: ((TickerAISuggestion) -> Void)?
    var onSend: (() -> Void)?

    private var canSend: Bool {
        !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Suggestion chips row
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.sm) {
                    ForEach(suggestions) { suggestion in
                        TickerAISuggestionChip(suggestion: suggestion) {
                            onSuggestionTap?(suggestion)
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }

            // Input bar
            HStack(spacing: AppSpacing.md) {
                // Sparkle icon
                Image(systemName: "sparkles")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundColor(AppColors.primaryBlue)

                // Text field
                TextField("Ask Caudex AI...", text: $inputText)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)

                // Send button
                Button(action: {
                    if canSend {
                        onSend?()
                    }
                }) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 28))
                        .foregroundColor(canSend ? AppColors.primaryBlue : AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
                .disabled(!canSend)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.extraLarge)
            .padding(.horizontal, AppSpacing.lg)
        }
        .padding(.bottom, AppSpacing.md)
        .background(
            AppColors.background
                .shadow(color: Color.black.opacity(0.3), radius: 10, x: 0, y: -5)
        )
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var text = ""

        var body: some View {
            VStack {
                Spacer()
                TickerDetailAIBar(
                    inputText: $text,
                    tickerSymbol: "AAPL",
                    suggestions: TickerAISuggestion.defaultSuggestions
                )
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
