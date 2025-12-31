//
//  UpdatesTabButton.swift
//  ios
//
//  Molecule: Tab button for Updates screen filter tabs
//

import SwiftUI

struct UpdatesTabButton: View {
    let tab: NewsFilterTab
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.xs) {
                if tab.isMarketTab {
                    Image(systemName: "globe.americas.fill")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                }

                Text(tab.title)
                    .font(isSelected ? AppTypography.calloutBold : AppTypography.callout)
                    .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)

                if let change = tab.formattedChange {
                    Text(change)
                        .font(AppTypography.caption)
                        .foregroundColor(tab.isPositive ? AppColors.bullish : AppColors.bearish)
                }
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            .background(
                isSelected
                    ? AppColors.primaryBlue
                    : AppColors.cardBackgroundLight
            )
            .clipShape(Capsule())
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: 10) {
        UpdatesTabButton(
            tab: NewsFilterTab(title: "Market", ticker: nil, changePercent: nil, isMarketTab: true),
            isSelected: true,
            action: {}
        )
        UpdatesTabButton(
            tab: NewsFilterTab(title: "AAPL", ticker: "AAPL", changePercent: 2.4, isMarketTab: false),
            isSelected: false,
            action: {}
        )
        UpdatesTabButton(
            tab: NewsFilterTab(title: "TSLA", ticker: "TSLA", changePercent: -1.2, isMarketTab: false),
            isSelected: false,
            action: {}
        )
    }
    .padding()
    .background(AppColors.background)
}
