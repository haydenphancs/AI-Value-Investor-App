//
//  StickyTickerHeader.swift
//  ios
//
//  Compact header for sticky display showing only ticker name and price
//

import SwiftUI

struct StickyTickerHeader: View {
    let companyName: String
    let price: String

    var body: some View {
        HStack {
            Text(companyName)
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            Spacer()

            Text(price)
                .font(AppTypography.headline)
                .fontWeight(.bold)
                .foregroundColor(AppColors.textPrimary)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        StickyTickerHeader(
            companyName: "Apple Inc.",
            price: "$178.42"
        )

        StickyTickerHeader(
            companyName: "Tesla, Inc.",
            price: "$252.18"
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
