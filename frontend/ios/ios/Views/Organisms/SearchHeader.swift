//
//  SearchHeader.swift
//  ios
//
//  Organism: Header for search screen with back button, search bar, and suggestion chips
//

import SwiftUI

struct SearchHeader: View {
    @Binding var searchText: String
    let suggestions: [SearchQuerySuggestion]
    var onBackTapped: (() -> Void)?
    var onSearchSubmit: (() -> Void)?
    var onSuggestionTapped: ((SearchQuerySuggestion) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Search bar row
            HStack(spacing: AppSpacing.md) {
                // Back button
                Button(action: {
                    onBackTapped?()
                }) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(PlainButtonStyle())

                // Search bar
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "sparkles")
                        .font(.system(size: 16, weight: .medium))
                        .foregroundColor(AppColors.primaryBlue)

                    TextField("", text: $searchText, prompt: Text("Search or ask Caudex AI...")
                        .foregroundColor(AppColors.textMuted))
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textPrimary)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .onSubmit {
                            onSearchSubmit?()
                        }
                }
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.cardBackground)
                .cornerRadius(AppCornerRadius.large)
            }
            .padding(.horizontal, AppSpacing.lg)

            // Suggestion chips
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.sm) {
                    ForEach(suggestions) { suggestion in
                        SearchQueryChip(suggestion: suggestion) {
                            onSuggestionTapped?(suggestion)
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
        .padding(.top, AppSpacing.sm)
        .padding(.bottom, AppSpacing.md)
    }
}

#Preview {
    VStack {
        SearchHeader(
            searchText: .constant(""),
            suggestions: SearchQuerySuggestion.sampleData
        )

        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
