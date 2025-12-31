//
//  AssetRow.swift
//  ios
//
//  Molecule: Asset row displaying ticker, sparkline, price and change
//

import SwiftUI

struct AssetRow: View {
    let asset: TrackedAsset
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.lg) {
                // Ticker Info
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(asset.ticker)
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)

                    Text(asset.companyName)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(1)
                }
                .frame(width: 80, alignment: .leading)

                // Sparkline Chart
                SparklineView(
                    data: asset.sparklineData,
                    isPositive: asset.isPositive
                )
                .frame(height: 32)

                Spacer()

                // Price Info
                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text(asset.formattedPrice)
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)

                    PriceChangeLabel(changePercent: asset.changePercent)
                }
            }
            .padding(.vertical, AppSpacing.md)
            .padding(.horizontal, AppSpacing.lg)
            .background(AppColors.cardBackground)
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    VStack(spacing: 0) {
        ForEach(TrackedAsset.sampleData) { asset in
            AssetRow(asset: asset)
            if asset.id != TrackedAsset.sampleData.last?.id {
                Divider()
                    .background(AppColors.cardBackgroundLight)
            }
        }
    }
    .background(AppColors.background)
}
