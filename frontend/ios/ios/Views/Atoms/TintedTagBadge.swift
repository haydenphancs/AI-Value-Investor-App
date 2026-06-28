//
//  TintedTagBadge.swift
//  ios
//
//  Atom: a generic tinted capsule badge — colored text on a low-opacity tint of
//  the same color, with an optional leading SF Symbol.
//
//  Generic over an arbitrary accent color (existing badge atoms like
//  `SentimentBadge` / `ArticleTagPill` are locked to fixed palettes), so it
//  backs the Caydex Home pills: "Volume", "Shorts", the "CAYDEX" chip, and the
//  green percent chips on theme tiles.
//

import SwiftUI

struct TintedTagBadge: View {
    let text: String
    let color: Color
    var systemImage: String? = nil
    var backgroundOpacity: Double = 0.12
    var font: Font = AppTypography.captionEmphasis   // 11, semibold
    var tracking: CGFloat = 0

    var body: some View {
        HStack(spacing: 4) {
            if let systemImage {
                Image(systemName: systemImage)
                    .font(.system(size: 9, weight: .bold))
            }
            Text(text)
                .font(font)
                .tracking(tracking)
        }
        .foregroundColor(color)
        .padding(.horizontal, 9)
        .padding(.vertical, 4)
        .background(color.opacity(backgroundOpacity))
        .clipShape(Capsule())
    }
}

#Preview {
    HStack(spacing: 10) {
        TintedTagBadge(text: "Volume", color: AppColors.accentCyan)
        TintedTagBadge(text: "Shorts", color: AppColors.neutral)
        TintedTagBadge(text: "CAYDEX", color: AppColors.accentCyan,
                       systemImage: "sparkles",
                       backgroundOpacity: 0.14,
                       font: AppTypography.captionSmallEmphasis, tracking: 0.4)
        TintedTagBadge(text: "+3.4%", color: AppColors.bullish)
    }
    .padding()
    .background(AppColors.cardBackground)
}
