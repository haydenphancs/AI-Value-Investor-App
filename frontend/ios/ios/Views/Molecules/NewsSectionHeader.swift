//
//  NewsSectionHeader.swift
//  ios
//
//  Molecule: Sticky section header for news date groups
//

import SwiftUI

struct NewsSectionHeader: View {
    let title: String

    var body: some View {
        HStack {
            Text(title)
                .font(AppTypography.footnoteBold)
                .foregroundColor(AppColors.primaryBlue)

            Spacer()
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .background(AppColors.background)
    }
}

#Preview {
    VStack(spacing: 0) {
        NewsSectionHeader(title: "TODAY")
        NewsSectionHeader(title: "YESTERDAY")
        NewsSectionHeader(title: "Dec 28, 2025")
    }
    .background(AppColors.background)
}
