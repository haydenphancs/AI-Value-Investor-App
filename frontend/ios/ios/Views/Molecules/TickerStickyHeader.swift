//
//  TickerStickyHeader.swift
//  ios
//
//  Molecule: Compact sticky header for Ticker Detail when scrolling
//  Shows ticker name, price, and tab bar - becomes the ceiling when scrolling
//

import SwiftUI

struct TickerStickyHeader: View {
    let companyName: String
    let symbol: String
    let price: String
    let priceChange: String
    let priceChangePercent: String
    let isPositive: Bool
    @Binding var selectedTab: TickerDetailTab

    private var changeColor: Color {
        isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var arrowIcon: String {
        isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill"
    }

    var body: some View {
        VStack(spacing: 0) {
            // Compact price row
            HStack {
                // Left side - Company name only (compact)
                Text(companyName)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                Spacer()

                // Right side - Price and change inline
                HStack(spacing: AppSpacing.sm) {
                    Text(price)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    HStack(spacing: 2) {
                        Image(systemName: arrowIcon)
                            .font(.system(size: 8, weight: .semibold))
                            .foregroundColor(changeColor)

                        Text(priceChangePercent)
                            .font(AppTypography.caption)
                            .fontWeight(.semibold)
                            .foregroundColor(changeColor)
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.sm)

            // Tab Bar
            TickerDetailTabBar(selectedTab: $selectedTab)

            // Bottom divider
            Rectangle()
                .fill(AppColors.cardBackgroundLight)
                .frame(height: 1)
        }
        .background(AppColors.background)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var selectedTab: TickerDetailTab = .overview

        var body: some View {
            VStack {
                TickerStickyHeader(
                    companyName: "Apple Inc.",
                    symbol: "AAPL",
                    price: "$178.42",
                    priceChange: "+$2.34",
                    priceChangePercent: "(+1.33%)",
                    isPositive: true,
                    selectedTab: $selectedTab
                )

                Spacer()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
