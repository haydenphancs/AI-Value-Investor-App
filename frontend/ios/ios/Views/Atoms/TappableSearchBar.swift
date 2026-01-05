//
//  TappableSearchBar.swift
//  ios
//
//  Atom: A search bar placeholder that acts as a button to navigate to search
//

import SwiftUI

struct TappableSearchBar: View {
    var placeholder: String = "Search ticker or ask AI..."
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 16, weight: .medium))
                    .foregroundColor(AppColors.textMuted)

                Text(placeholder)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textMuted)

                Spacer()
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack {
        TappableSearchBar()
        TappableSearchBar(placeholder: "Search or ask Caudex AI...")
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
