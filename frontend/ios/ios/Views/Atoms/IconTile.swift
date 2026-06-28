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
                Image(systemName: systemName)
                    .font(.system(size: iconPointSize ?? size * 0.5, weight: .semibold))
                    .foregroundColor(accent)
            )
    }
}

#Preview {
    HStack(spacing: 12) {
        IconTile(systemName: "chart.line.uptrend.xyaxis", accent: AppColors.bullish,
                 size: 30, cornerRadius: 9, iconPointSize: 17)
        IconTile(systemName: "building.columns.fill", accent: AppColors.primaryBlue,
                 size: 40, iconPointSize: 21)
        IconTile(systemName: "cpu.fill", accent: Color(hex: "22D3EE"),
                 size: 42, cornerRadius: 12, tintOpacity: 0.15, iconPointSize: 23)
    }
    .padding()
    .background(AppColors.cardBackground)
}
