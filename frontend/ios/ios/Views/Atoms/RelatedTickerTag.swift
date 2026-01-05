//
//  RelatedTickerTag.swift
//  ios
//
//  Atom: Tag showing a related stock ticker
//

import SwiftUI

struct RelatedTickerTag: View {
    let ticker: String
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(ticker)
                .font(AppTypography.footnote)
                .fontWeight(.medium)
                .foregroundColor(AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.small)
                        .fill(AppColors.cardBackgroundLight)
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: AppSpacing.sm) {
        RelatedTickerTag(ticker: "APPL")
        RelatedTickerTag(ticker: "ORCL")
        RelatedTickerTag(ticker: "TSLA")
        RelatedTickerTag(ticker: "NVDA")
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
