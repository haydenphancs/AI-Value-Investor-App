//
//  PriceChangeLabel.swift
//  ios
//
//  Atom: Price change label with arrow indicator
//

import SwiftUI

struct PriceChangeLabel: View {
    let changePercent: Double
    var showArrow: Bool = true
    var fontSize: CGFloat = 13

    /// Signed zero collapsed to +0. `-0.0` is the value any barely-negative move
    /// rounds to server-side, and it breaks BOTH readers below in opposite
    /// directions: `-0.0 >= 0` is `true` (so the arrow and colour say "up") while
    /// `String(format: "%.2f", -0.0)` preserves the sign bit and prints "-0.00" —
    /// the label rendered the literal string "+-0.00%". Normalising once here
    /// keeps the arrow, the colour and the text agreeing on one sign.
    private var normalizedChange: Double {
        changePercent == 0 ? 0 : changePercent
    }

    private var isPositive: Bool {
        normalizedChange >= 0
    }

    private var formattedChange: String {
        let sign = isPositive ? "+" : ""
        return "\(sign)\(String(format: "%.2f", normalizedChange))%"
    }

    private var color: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        HStack(spacing: 2) {
            if showArrow {
                Image(systemName: isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill")
                    .font(.system(size: fontSize * 0.7))
            }

            Text(formattedChange)
                .font(.system(size: fontSize, weight: .semibold))
        }
        .foregroundColor(color)
    }
}

#Preview {
    VStack(spacing: 12) {
        PriceChangeLabel(changePercent: 2.34)
        PriceChangeLabel(changePercent: -1.23)
        PriceChangeLabel(changePercent: 5.67, showArrow: false)
        PriceChangeLabel(changePercent: -0.45, fontSize: 16)
    }
    .padding()
    .background(AppColors.background)
}
