//
//  MarketPulseCard.swift
//  ios
//
//  Molecule: one compact tile in the Home "Markets Open" pulse strip —
//  name, price, mini sparkline, and change %.
//

import SwiftUI

struct MarketPulseCard: View {
    let item: MarketPulseItem
    var onTap: (() -> Void)? = nil

    private var changeColor: Color {
        item.isPositive ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        Button { onTap?() } label: {
            VStack(alignment: .leading, spacing: 3) {
                Text(item.name)
                    .font(AppTypography.captionSmall)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)

                Text(item.priceText)
                    .font(AppTypography.dataMedium)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)

                TintedSparkline(points: item.spark, color: changeColor)
                    .frame(height: 18)

                Text(item.changeText)
                    .font(AppTypography.dataSmall)
                    .foregroundColor(changeColor)
            }
            .frame(width: 88, alignment: .leading)
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    HStack(spacing: 10) {
        MarketPulseCard(item: MockHomeRepository.pulse[0])
        MarketPulseCard(item: MockHomeRepository.pulse[3])
    }
    .padding()
    .background(AppColors.background)
}
