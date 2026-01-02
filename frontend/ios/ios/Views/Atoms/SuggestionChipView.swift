//
//  SuggestionChipView.swift
//  ios
//
//  Atom: Suggestion chip/pill for chat suggestions
//

import SwiftUI

struct SuggestionChipView: View {
    let chip: SuggestionChip
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(chip.text)
                .font(AppTypography.callout)
                .foregroundColor(chip.type.textColor)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                        .fill(chip.type.backgroundColor)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                        .stroke(chip.type.borderColor, lineWidth: 1)
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ForEach(SuggestionChip.sampleData) { chip in
            SuggestionChipView(chip: chip)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
