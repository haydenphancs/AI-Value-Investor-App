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
                        .font(AppTypography.iconXS).fontWeight(.medium)
                        .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                }

                // The hidden semibold copy reserves the selected width, so moving the
                // outline never re-flows the neighbouring chips. Hidden from VoiceOver too,
                // or the title is read twice.
                ZStack {
                    Text(tab.title)
                        .font(AppTypography.bodySmallEmphasis)
                        .hidden()
                        .accessibilityHidden(true)
                    Text(tab.title)
                        .font(isSelected ? AppTypography.bodySmallEmphasis : AppTypography.bodySmall)
                        .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                }

                if let change = tab.formattedChange {
                    Text(change)
                        .font(AppTypography.caption)
                        .foregroundColor(tab.isPositive ? AppColors.gain : AppColors.loss)
                }
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            // Selection is an OUTLINE, never a fill. The chip used to fill with primaryBlue,
            // a TEXT token: the gain/loss % on it measured 1.05-1.12:1 (invisible) and the
            // ticker 3.43 light / 2.54 dark. The fill stays cardBackgroundLight because
            // gain/loss are declared only on content surfaces (4.75/6.20 and 4.86/5.10 here).
            // The stroke is primaryBlue at full opacity, 4.52 light / 5.55 dark against the
            // chip; not borderFocus/primaryFill, which is 3.12 in dark.
            .background(AppColors.cardBackgroundLight)
            .clipShape(Capsule())
            .overlay(
                Capsule()
                    .strokeBorder(isSelected ? AppColors.primaryBlue : Color.clear, lineWidth: 1.5)
            )
        }
        .buttonStyle(PlainButtonStyle())
        // The outline is the only visual cue, so VoiceOver must hear the state.
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }
}

#Preview {
    HStack(spacing: 10) {
        UpdatesTabButton(
            tab: NewsFilterTab(title: "Market", ticker: nil, changePercent: nil, isMarketTab: true),
            isSelected: false,
            action: {}
        )
        UpdatesTabButton(
            tab: NewsFilterTab(title: "AAPL", ticker: "AAPL", changePercent: 2.4, isMarketTab: false),
            isSelected: true,
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

#Preview("Dark") {
    HStack(spacing: 10) {
        UpdatesTabButton(
            tab: NewsFilterTab(title: "Market", ticker: nil, changePercent: nil, isMarketTab: true),
            isSelected: false,
            action: {}
        )
        UpdatesTabButton(
            tab: NewsFilterTab(title: "AAPL", ticker: "AAPL", changePercent: 2.4, isMarketTab: false),
            isSelected: true,
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
    .environment(\.colorScheme, .dark)
}
