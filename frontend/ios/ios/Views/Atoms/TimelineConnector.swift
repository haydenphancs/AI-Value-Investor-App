//
//  TimelineConnector.swift
//  ios
//
//  Atom: Vertical line connecting timeline items
//

import SwiftUI

struct TimelineConnector: View {
    var height: CGFloat = 60

    var body: some View {
        Rectangle()
            .fill(AppColors.textMuted.opacity(0.3))
            .frame(width: 1, height: height)
    }
}

#Preview {
    VStack(spacing: 0) {
        TimelineDot()
        TimelineConnector(height: 40)
        TimelineDot()
        TimelineConnector(height: 60)
        TimelineDot(isHighlighted: true)
    }
    .padding()
    .background(AppColors.background)
}
