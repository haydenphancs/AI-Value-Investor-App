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
                        .font(AppTypography.bodyEmphasis)
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
                        .font(AppTypography.iconLarge)
                        .foregroundColor(AppColors.textSecondary)
                }
            }
        } else if let ticker = item.ticker {
            // Stock ticker badge
            ZStack {
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    // Hairline so a brand colour that happens to match the card
                    // still reads as a chip — Apple's #1E1E1E is 1.06:1 against
                    // #1E2330 and was otherwise invisible in dark mode.
                    .cardFill(tickerBackgroundColor(for: ticker))
                    .frame(width: 44, height: 44)

                Text(ticker)
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textOnAccent)
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
                .font(AppTypography.iconSmall).fontWeight(.semibold)
                .foregroundColor(AppColors.textMuted)
        }
    }

    /// Real brand colours, kept verbatim — these are trade dress, so they are
    /// deliberately NOT routed through the palette. They all carry white text
    /// and clear 4.5:1 for it. What they cannot do is separate from the card:
    /// Apple's #1E1E1E is 1.06:1 against #1E2330, so the chip disappeared in
    /// dark mode. The caller bounds them with a hairline instead of retinting.
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
                .overlay(AppColors.cardBackgroundLight)
        }
    }
    .padding(.horizontal, AppSpacing.lg)
    .background(AppColors.background)
}
