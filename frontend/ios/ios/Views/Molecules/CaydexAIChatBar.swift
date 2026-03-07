//
//  CaydexAIChatBar.swift
//  ios
//
//  Molecule: Shared bottom AI chat bar used across all detail screens.
//  Supports optional suggestion pills above the input field.
//

import SwiftUI

struct CaydexAIChatBar: View {
    @Binding var inputText: String
    var placeholder: String = "Ask Cay AI..."
    var suggestions: [String] = []
    var onSuggestionTap: ((String) -> Void)?
    var onSend: (() -> Void)?

    private var canSend: Bool {
        !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Suggestion chips row (only shown when suggestions are provided)
            if !suggestions.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: AppSpacing.sm) {
                        ForEach(suggestions, id: \.self) { suggestion in
                            CaydexAISuggestionChip(text: suggestion) {
                                onSuggestionTap?(suggestion)
                            }
                        }
                    }
                    .padding(.horizontal, AppSpacing.lg)
                }
            }

            // Input bar
            HStack(spacing: AppSpacing.md) {
                // Sparkle icon
                Image(systemName: "sparkles")
                    .font(AppTypography.iconMedium).fontWeight(.medium)
                    .foregroundColor(AppColors.primaryBlue)

                // Text field
                TextField(placeholder, text: $inputText)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)

                // Send button
                Button(action: {
                    if canSend {
                        onSend?()
                    }
                }) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(AppTypography.iconDisplay)
                        .foregroundColor(canSend ? AppColors.primaryBlue : AppColors.textMuted)
                }
                .buttonStyle(PlainButtonStyle())
                .disabled(!canSend)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.sm)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.extraLarge)
            .padding(.horizontal, AppSpacing.lg)
        }
        .padding(.bottom, AppSpacing.md)
        .background(
            LinearGradient(
                colors: [
                    AppColors.background.opacity(0),
                    AppColors.background.opacity(0.7),
                    AppColors.background.opacity(0.95),
                    AppColors.background
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
        )
    }
}

// MARK: - Suggestion Chip
struct CaydexAISuggestionChip: View {
    let text: String
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(text)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                        .fill(AppColors.cardBackground)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                        .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

// MARK: - Preview
#Preview {
    struct PreviewWrapper: View {
        @State private var text = ""

        var body: some View {
            VStack {
                Spacer()

                // With suggestions
                CaydexAIChatBar(
                    inputText: $text,
                    suggestions: ["What's the P/E ratio?", "Why does it move?", "Should I buy?"]
                )

                // Without suggestions
                CaydexAIChatBar(
                    inputText: $text
                )
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
