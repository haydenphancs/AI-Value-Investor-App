//
//  IconTile.swift
//  ios
//
//  Atom: a rounded-square tile with a low-opacity accent tint behind a centered
//  SF Symbol drawn in that accent. The recurring "icon chip" in the Caydex Home
//  design — used at 30pt (scanner headers), 40pt (signal rows) and 42pt (theme
//  tiles).
//

import SwiftUI

struct IconTile: View {
    let systemName: String
    let accent: Color
    var size: CGFloat = 40
    var cornerRadius: CGFloat = 11
    var tintOpacity: Double = 0.16
    /// Defaults to half the tile size; override for finer control.
    var iconPointSize: CGFloat? = nil

    var body: some View {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            .fill(accent.opacity(tintOpacity))
            .frame(width: size, height: size)
            .overlay(
                // DELIBERATELY a raw `.system(size:)` and not an `AppTypography` icon
                // token, i.e. this glyph does NOT scale with Dynamic Type. Scaling it
                // alone overflows the fixed `size × size` tile; scaling both breaks
                // every constrained row that budgets 30/40/42pt for this chip. The
                // glyph is never the sole carrier of meaning — every call site pairs
                // the tile with a `Text` label that DOES scale, and WCAG 1.4.4 governs
                // text, not decorative iconography. Revisit only if a tile ever ships
                // without an adjacent label.
                Image(systemName: systemName)
                    .font(.system(size: iconPointSize ?? size * 0.5, weight: .semibold))
                    .foregroundColor(accent)
                    .accessibilityHidden(true)
            )
    }
}

#Preview {
    HStack(spacing: 12) {
        IconTile(systemName: "chart.line.uptrend.xyaxis", accent: AppColors.bullish,
                 size: 30, cornerRadius: 9, iconPointSize: 17)
        IconTile(systemName: "building.columns.fill", accent: AppColors.primaryBlue,
                 size: 40, iconPointSize: 21)
        IconTile(systemName: "cpu.fill", accent: AppColors.accentCyan,
                 size: 42, cornerRadius: 12, tintOpacity: 0.15, iconPointSize: 23)
    }
    .padding()
    .background(AppColors.cardBackground)
}
