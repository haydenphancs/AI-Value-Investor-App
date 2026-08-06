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

    /// The signed-zero normalisation above cannot help a NaN (`nan == 0` is false), and
    /// `nan >= 0` is false, so an unguarded NaN rendered a red DOWN arrow next to the
    /// literal text "nan%" — indistinguishable from a real decline.
    private var isFinite: Bool { changePercent.isFinite }

    private var isPositive: Bool {
        normalizedChange >= 0
    }

    private var formattedChange: String {
        guard isFinite else { return "—" }
        let sign = isPositive ? "+" : ""
        return "\(sign)\(String(format: "%.2f", normalizedChange))%"
    }

    private var color: Color {
        guard isFinite else { return AppColors.textSecondary }
        return isPositive ? AppColors.gain : AppColors.loss
    }

    var body: some View {
        HStack(spacing: 2) {
            // Suppressed for non-finite input: an arrow asserts a direction, and there
            // isn't one to assert.
            if showArrow, isFinite {
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
        // Degradation cases — every one of these reached a user as a wrong-looking
        // number before it was guarded. Expected: "+0.00%" up, then three em dashes
        // with NO arrow and neutral (never red) colour.
        PriceChangeLabel(changePercent: -0.0)
        PriceChangeLabel(changePercent: .nan)
        PriceChangeLabel(changePercent: .infinity)
        PriceChangeLabel(changePercent: -.infinity)
    }
    .padding()
    .background(AppColors.background)
}
