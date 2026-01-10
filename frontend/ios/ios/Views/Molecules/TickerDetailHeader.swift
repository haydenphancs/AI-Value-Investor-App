//
//  TickerDetailHeader.swift
//  ios
//
//  Molecule: Navigation header for Ticker Detail screen
//

import SwiftUI

struct TickerDetailHeader: View {
    var onBackTapped: (() -> Void)?
    var onNotificationTapped: (() -> Void)?
    var onFavoriteTapped: (() -> Void)?
    var onMoreTapped: (() -> Void)?
    var isFavorite: Bool = false

    // Optional ticker info to show when scrolled (pinned state)
    var tickerSymbol: String? = nil
    var tickerPrice: String? = nil

    var body: some View {
        HStack {
            // Back button and optional ticker info
            HStack(spacing: AppSpacing.sm) {
                Button(action: {
                    onBackTapped?()
                }) {
                    Image(systemName: "chevron.left")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 40, height: 40)
                }
                .buttonStyle(PlainButtonStyle())

                // Ticker symbol and price (shown when pinned)
                if let symbol = tickerSymbol, let price = tickerPrice {
                    HStack(spacing: AppSpacing.xs) {
                        Text(symbol)
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.textPrimary)

                        Text(price)
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .transition(.opacity.combined(with: .move(edge: .leading)))
                }
            }

            Spacer()

            // Right side buttons
            HStack(spacing: AppSpacing.md) {
                // Notification bell
                Button(action: {
                    onNotificationTapped?()
                }) {
                    Image(systemName: "bell")
                        .font(.system(size: 18, weight: .regular))
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 40, height: 40)
                }
                .buttonStyle(PlainButtonStyle())

                // Favorite star
                Button(action: {
                    onFavoriteTapped?()
                }) {
                    Image(systemName: isFavorite ? "star.fill" : "star")
                        .font(.system(size: 18, weight: .regular))
                        .foregroundColor(isFavorite ? AppColors.neutral : AppColors.textPrimary)
                        .frame(width: 40, height: 40)
                }
                .buttonStyle(PlainButtonStyle())

                // More options
                Button(action: {
                    onMoreTapped?()
                }) {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: 40, height: 40)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(.horizontal, AppSpacing.sm)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        TickerDetailHeader(isFavorite: false)
        TickerDetailHeader(isFavorite: true)
        TickerDetailHeader(
            isFavorite: false,
            tickerSymbol: "AAPL",
            tickerPrice: "$178.42"
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
