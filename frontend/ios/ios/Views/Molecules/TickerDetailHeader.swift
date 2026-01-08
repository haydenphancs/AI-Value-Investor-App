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

    var body: some View {
        HStack {
            // Back button
            Button(action: {
                onBackTapped?()
            }) {
                Image(systemName: "chevron.left")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 40, height: 40)
            }
            .buttonStyle(PlainButtonStyle())

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
    VStack {
        TickerDetailHeader(isFavorite: false)
        TickerDetailHeader(isFavorite: true)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
