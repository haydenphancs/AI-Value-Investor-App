//
//  SearchBar.swift
//  ios
//
//  Molecule: Search bar component
//

import SwiftUI

struct SearchBar: View {
    @Binding var text: String
    var placeholder: String = "Search ticker or ask AI..."
    var onSubmit: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 16, weight: .medium))
                .foregroundColor(AppColors.textMuted)

            TextField("", text: $text, prompt: Text(placeholder)
                .foregroundColor(AppColors.textMuted))
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .onSubmit {
                    onSubmit?()
                }
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    VStack {
        SearchBar(text: .constant(""))
        SearchBar(text: .constant("AAPL"))
    }
    .padding()
    .background(AppColors.background)
}
