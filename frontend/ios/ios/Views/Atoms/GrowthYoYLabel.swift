//
//  GrowthYoYLabel.swift
//  ios
//
//  Atom: Year-over-Year percentage label with color coding
//

import SwiftUI

struct GrowthYoYLabel: View {
    let yoyPercent: Double

    var body: some View {
        Text(formattedValue)
            .font(AppTypography.caption)
            .foregroundColor(yoyColor)
    }

    /// Non-finite input degrades to an em dash, never "nan%" / "inf%".
    ///
    /// This is fed straight from backend growth math, where a YoY ratio over a zero or
    /// missing prior-period base divides to NaN or ±∞. `String(format:)` would print
    /// those verbatim, and `NaN >= 0` is `false`, so the label rendered a red "nan%" —
    /// a value that looks like a real decline.
    private var isFinite: Bool { yoyPercent.isFinite }

    /// Signed zero collapsed to +0, for the same reason `PriceChangeLabel` does it:
    /// `-0.0 >= 0` is `true` (so we pick the "+" branch) while
    /// `String(format: "%.2f", -0.0)` keeps the sign bit and prints "-0.00" — the two
    /// combine into the literal string "+-0.00%". Normalising once here keeps the sign
    /// and the colour agreeing.
    private var normalized: Double {
        yoyPercent == 0 ? 0 : yoyPercent
    }

    /// Explicitly signed on BOTH sides.
    ///
    /// Positive used to render bare ("7.92%") while negative got its "-" from the
    /// formatter, so the up/down distinction rested on noticing an ABSENT character
    /// plus the colour. A leading "+" makes the direction legible without colour
    /// (WCAG 1.4.1) and matches every other percentage surface in the app.
    private var formattedValue: String {
        guard isFinite else { return "—" }
        let sign = normalized >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", normalized))%"
    }

    private var yoyColor: Color {
        guard isFinite else { return AppColors.textSecondary }
        return normalized >= 0 ? AppColors.gain : AppColors.loss
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.lg) {
                GrowthYoYLabel(yoyPercent: 7.92)      // "+7.92%"
                GrowthYoYLabel(yoyPercent: -2.30)
                GrowthYoYLabel(yoyPercent: 0.00)      // "+0.00%"
                GrowthYoYLabel(yoyPercent: -10.92)
            }
            // Degradation cases: signed zero must NOT print "+-0.00%", and a YoY over a
            // zero/missing prior-period base must show an em dash, not a red "nan%".
            HStack(spacing: AppSpacing.lg) {
                GrowthYoYLabel(yoyPercent: -0.0)
                GrowthYoYLabel(yoyPercent: .nan)
                GrowthYoYLabel(yoyPercent: .infinity)
            }
        }
        .padding()
    }
}
