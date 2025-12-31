//
//  AIBadge.swift
//  ios
//
//  Atom: AI Summary badge indicator
//

import SwiftUI

struct AIBadge: View {
    let text: String

    var body: some View {
        Text(text)
            .font(AppTypography.captionBold)
            .foregroundColor(AppColors.accentCyan)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(AppColors.accentCyan.opacity(0.15))
            .clipShape(Capsule())
    }
}

#Preview {
    VStack(spacing: 10) {
        AIBadge(text: "24h - AI Summary")
        AIBadge(text: "AI Generated")
    }
    .padding()
    .background(AppColors.background)
}
