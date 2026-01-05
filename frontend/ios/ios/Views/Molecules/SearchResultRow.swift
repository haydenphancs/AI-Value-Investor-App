//
//  SearchResultRow.swift
//  ios
//
//  Molecule: Row displaying a search result item (stock or person)
//

import SwiftUI

struct SearchResultRow: View {
    let item: SearchResultItem
    var onTap: (() -> Void)?
    var onFollowTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.md) {
                // Leading icon or image
                leadingView

                // Name and subtitle
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(item.name)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    Text(item.subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                // Trailing action
                trailingView
            }
            .padding(.vertical, AppSpacing.md)
            .contentShape(Rectangle())
        }
        .buttonStyle(PlainButtonStyle())
    }

    @ViewBuilder
    private var leadingView: some View {
        if item.hasProfileImage {
            // Person avatar
            ZStack {
                Circle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 44, height: 44)

                if let imageName = item.imageName {
                    Image(imageName)
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(width: 44, height: 44)
                        .clipShape(Circle())
                } else {
                    Image(systemName: "person.fill")
                        .font(.system(size: 20))
                        .foregroundColor(AppColors.textSecondary)
                }
            }
        } else if let ticker = item.ticker {
            // Stock ticker badge
            ZStack {
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(tickerBackgroundColor(for: ticker))
                    .frame(width: 44, height: 44)

                Text(ticker)
                    .font(AppTypography.captionBold)
                    .foregroundColor(.white)
            }
        }
    }

    @ViewBuilder
    private var trailingView: some View {
        if item.isFollowable {
            FollowButton(isFollowing: item.isFollowing) {
                onFollowTap?()
            }
        } else {
            Image(systemName: "chevron.right")
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(AppColors.textMuted)
        }
    }

    private func tickerBackgroundColor(for ticker: String) -> Color {
        switch ticker {
        case "AAPL":
            return Color(hex: "1E1E1E") // Dark gray/black for Apple
        case "TSLA":
            return Color(hex: "CC0000") // Tesla red
        case "MSFT":
            return Color(hex: "00A4EF") // Microsoft blue
        case "GOOGL", "GOOG":
            return Color(hex: "4285F4") // Google blue
        case "AMZN":
            return Color(hex: "FF9900") // Amazon orange
        case "NVDA":
            return Color(hex: "76B900") // Nvidia green
        default:
            return AppColors.primaryBlue
        }
    }
}

#Preview {
    VStack(spacing: 0) {
        ForEach(SearchResultItem.sampleData) { item in
            SearchResultRow(item: item)
            Divider()
                .background(AppColors.cardBackgroundLight)
        }
    }
    .padding(.horizontal, AppSpacing.lg)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
