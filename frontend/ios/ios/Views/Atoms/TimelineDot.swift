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

    var body: some View {
        Circle()
            .fill(isHighlighted ? AppColors.primaryBlue : AppColors.textMuted)
            .frame(width: size, height: size)
    }
}

#Preview {
    VStack(spacing: 20) {
        TimelineDot()
        TimelineDot(isHighlighted: true)
        TimelineDot(isHighlighted: true, size: 12)
    }
    .padding()
    .background(AppColors.background)
}
