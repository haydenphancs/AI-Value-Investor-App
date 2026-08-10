//
//  TimelineDot.swift
//  ios
//
//  Atom: Timeline indicator dot for news feed
//

import SwiftUI

struct TimelineDot: View {
    var isHighlighted: Bool = false
    var size: CGFloat = 8
    /// Open circle instead of a filled one — reads as "the timeline continues past
    /// here" rather than "an item occurred here". Used by the truncated Recent Trades
    /// timeline's "+N more" tail. Defaulted, so existing call sites are unaffected.
    var isHollow: Bool = false

    private var color: Color {
        isHighlighted ? AppColors.primaryBlue : AppColors.textMuted
    }

    var body: some View {
        Group {
            if isHollow {
                Circle().strokeBorder(color, lineWidth: 1.5)
            } else {
                Circle().fill(color)
            }
        }
        .frame(width: size, height: size)
    }
}

#Preview {
    VStack(spacing: 20) {
        TimelineDot()
        TimelineDot(isHighlighted: true)
        TimelineDot(isHighlighted: true, size: 12)
        TimelineDot(isHollow: true)
        TimelineDot(size: 12, isHollow: true)
    }
    .padding()
    .background(AppColors.background)
}
