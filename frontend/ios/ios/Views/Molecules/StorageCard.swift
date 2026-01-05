//
//  StorageCard.swift
//  ios
//
//  Molecule: Card displaying storage usage with progress bar and upgrade option
//

import SwiftUI

struct StorageCard: View {
    let storageInfo: StorageInfo
    var onUpgrade: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header row: Title + Percentage
            HStack {
                Text("Storage")
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Text(storageInfo.formattedPercentage)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Progress bar
            StorageProgressBar(progress: storageInfo.progress)

            // Bottom row: Usage text + Upgrade button
            HStack {
                Text(storageInfo.formattedUsed)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)

                Spacer()

                Button(action: {
                    onUpgrade?()
                }) {
                    Text("Upgrade")
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        StorageCard(storageInfo: StorageInfo(usedGB: 3.2, totalGB: 4.4))
        StorageCard(storageInfo: StorageInfo(usedGB: 1.5, totalGB: 5.0))
        StorageCard(storageInfo: StorageInfo(usedGB: 4.8, totalGB: 5.0))
    }
    .padding(.horizontal, AppSpacing.lg)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
