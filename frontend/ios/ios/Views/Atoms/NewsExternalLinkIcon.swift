//
//  NewsExternalLinkIcon.swift
//  ios
//
//  Atom: External link icon for opening full article
//

import SwiftUI

struct NewsExternalLinkIcon: View {
    var size: CGFloat = 16

    var body: some View {
        Image(systemName: "arrow.up.right.square")
            .font(.system(size: size, weight: .medium))
            .foregroundColor(AppColors.textSecondary)
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        NewsExternalLinkIcon()
        NewsExternalLinkIcon(size: 20)
        NewsExternalLinkIcon(size: 24)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
