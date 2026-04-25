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
    /// Open the keyboard automatically when this view appears. Used by sheets
    /// that present a search input — the user already tapped a search target
    /// to get here, so making them tap a second time is busywork.
    var autoFocus: Bool = false

    @FocusState private var fieldFocused: Bool

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "magnifyingglass")
                .font(AppTypography.iconDefault).fontWeight(.medium)
                .foregroundColor(AppColors.textMuted)

            TextField("", text: $text, prompt: Text(placeholder)
                .foregroundColor(AppColors.textMuted))
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .focused($fieldFocused)
                .onSubmit {
                    onSubmit?()
                }

            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 16))
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
        .onAppear {
            // Defer focusing slightly so the sheet present animation completes
            // first — focusing too early gets swallowed by UIKit and the
            // keyboard never appears.
            if autoFocus {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                    fieldFocused = true
                }
            }
        }
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
