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

    private var isPositive: Bool {
        changePercent >= 0
    }

    private var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", changePercent))%"
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
