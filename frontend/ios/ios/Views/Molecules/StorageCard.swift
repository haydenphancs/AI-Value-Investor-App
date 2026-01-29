//
//  StorageCard.swift
//  ios
//
//  Molecule: Card displaying storage usage with progress bar and upgrade option
//

import SwiftUI

// MARK: - StorageInfo Model
struct StorageInfo {
    let usedGB: Double
    let totalGB: Double
    
    var progress: Double {
        guard totalGB > 0 else { return 0 }
        return min(usedGB / totalGB, 1.0)
    }
    
    var formattedPercentage: String {
        let percentage = Int(progress * 100)
        return "\(percentage)%"
    }
    
    var formattedUsed: String {
        return String(format: "%.1f GB of %.1f GB used", usedGB, totalGB)
    }
}

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
