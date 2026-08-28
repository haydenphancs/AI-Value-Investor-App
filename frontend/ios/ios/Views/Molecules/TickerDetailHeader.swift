//
//  TickerDetailHeader.swift
//  ios
//
//  Molecule: Navigation header for Ticker Detail screen
//

import SwiftUI

struct TickerDetailHeader: View {
    var onBackTapped: (() -> Void)?
    var onSearchTapped: (() -> Void)?
    var onNotificationTapped: (() -> Void)?
    var onFavoriteTapped: (() -> Void)?
    var onMoreTapped: (() -> Void)?
    var isFavorite: Bool = false

    // Ticker symbol - always shown in header
    var tickerSymbol: String

    // Optional price to show when scrolled (pinned state)
    var tickerPrice: String? = nil

    var body: some View {
        HStack {
            // Back button and ticker info
            HStack(spacing: AppSpacing.xs) {
                Button(action: {
                    onBackTapped?()
                }) {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconMedium).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget)
                        .hitSlop()
                }
                .buttonStyle(PlainButtonStyle())

                // Ticker symbol (always visible)
                Text(tickerSymbol)
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                // Price (shown when pinned/scrolled).
                //
                // MUST stay one line. An index quote is five digits plus decimals
                // ("$26541.35" on ^IXIC, vs "$771.10" for an ETF), which wrapped onto a second
                // line and pushed the header taller than the ticker symbol beside it. It only
                // became visible once the pin state was made reliable — before that this label
                // was mostly never shown. Shrink-to-fit rather than truncate: a clipped price
                // is a wrong number, and a wrong number is worse than a small one.
                if let price = tickerPrice {
                    Text(price)
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .transition(.opacity.combined(with: .move(edge: .leading)))
                }
            }

            Spacer()

            // Right side buttons
            HStack(spacing: AppSpacing.md) {
                // Search
                Button(action: {
                    onSearchTapped?()
                }) {
                    Image(systemName: "magnifyingglass")
                        .font(AppTypography.iconMedium)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget)
                        .hitSlop()
                }
                .buttonStyle(PlainButtonStyle())

                // Notification bell — rendered ONLY when a handler exists. Price
                // alerts aren't built yet, so all five detail screens pass nil and the
                // bell is hidden. A visible control that does nothing reads as a bug
                // (and is an App Review 2.1 risk); the repo already handles the six
                // ticker-analysis "Details" buttons the same way.
                if let onNotificationTapped {
                    Button(action: onNotificationTapped) {
                        Image(systemName: "bell")
                            .font(AppTypography.iconMedium)
                            .foregroundColor(AppColors.textPrimary)
                            .frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget)
                            .hitSlop()
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                // Favorite star
                Button(action: {
                    onFavoriteTapped?()
                }) {
                    Image(systemName: isFavorite ? "star.fill" : "star")
                        .font(AppTypography.iconMedium)
                        .foregroundColor(isFavorite ? AppColors.neutral : AppColors.textPrimary)
                        .frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget)
                        .hitSlop()
                }
                .buttonStyle(PlainButtonStyle())

                // More options
                Button(action: {
                    onMoreTapped?()
                }) {
                    Image(systemName: "square.and.arrow.up")
                        .font(AppTypography.iconMedium)
                        .foregroundColor(AppColors.textPrimary)
                        .frame(width: HitSlop.minimumTarget, height: HitSlop.minimumTarget)
                        .hitSlop()
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(.horizontal, AppSpacing.sm)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        TickerDetailHeader(isFavorite: false, tickerSymbol: "AAPL")
        TickerDetailHeader(isFavorite: true, tickerSymbol: "TSLA")
        TickerDetailHeader(
            isFavorite: false,
            tickerSymbol: "AAPL",
            tickerPrice: "$178.42"
        )
    }
    .background(AppColors.background)
}
