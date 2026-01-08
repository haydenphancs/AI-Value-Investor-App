//
//  TickerAISuggestionChip.swift
//  ios
//
//  Molecule: AI suggestion chip for ticker-specific questions
//

import SwiftUI

struct TickerAISuggestionChip: View {
    let suggestion: TickerAISuggestion
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

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.sm) {
            ForEach(TickerAISuggestion.defaultSuggestions) { suggestion in
                TickerAISuggestionChip(suggestion: suggestion)
            }
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
