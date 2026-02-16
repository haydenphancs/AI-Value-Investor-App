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

    private var canSend: Bool {
        !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Suggestion chips row
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.sm) {
                    ForEach(suggestions) { suggestion in
                        ETFAISuggestionChip(suggestion: suggestion) {
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

// MARK: - ETF AI Suggestion Chip
struct ETFAISuggestionChip: View {
    let suggestion: ETFAISuggestion
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(suggestion.text)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(AppColors.cardBackground)
                .cornerRadius(AppCornerRadius.extraLarge)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge)
                        .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
                )
        }
        .buttonStyle(PlainButtonStyle())
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
