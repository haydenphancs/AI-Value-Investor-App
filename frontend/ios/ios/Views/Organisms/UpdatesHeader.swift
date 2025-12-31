//
//  UpdatesHeader.swift
//  ios
//
//  Organism: Header for Updates screen with logo, title, and profile
//

import SwiftUI

struct UpdatesHeader: View {
    var onProfileTapped: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Logo placeholder
            ZStack {
                Circle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 36, height: 36)

                Text("logo")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundColor(AppColors.textMuted)
            }

            // Title with icon
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "sparkles")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(AppColors.primaryBlue)

                Text("News Updates")
                    .font(AppTypography.headline)
                    .foregroundColor(AppColors.textPrimary)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.sm)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.pill)

            Spacer()

            // Profile Button
            Button(action: { onProfileTapped?() }) {
                ZStack {
                    Circle()
                        .stroke(AppColors.textMuted.opacity(0.5), lineWidth: 1)
                        .frame(width: 36, height: 36)

                    Image(systemName: "person.circle")
                        .font(.system(size: 24, weight: .light))
                        .foregroundColor(AppColors.textSecondary)
                }
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
    }
}

#Preview {
    VStack {
        UpdatesHeader()
        Spacer()
    }
    .background(AppColors.background)
}
