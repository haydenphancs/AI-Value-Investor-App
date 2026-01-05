//
//  SearchQueryChip.swift
//  ios
//
//  Molecule: Chip showing a search query suggestion
//

import SwiftUI

struct SearchQueryChip: View {
    let suggestion: SearchQuerySuggestion
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.sm) {
                if let iconName = suggestion.iconName {
                    Image(systemName: iconName)
                        .font(.system(size: 12))
                        .foregroundColor(AppColors.textSecondary)
                }

                Text(suggestion.text)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.pill)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.sm) {
            ForEach(SearchQuerySuggestion.sampleData) { suggestion in
                SearchQueryChip(suggestion: suggestion)
            }
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
