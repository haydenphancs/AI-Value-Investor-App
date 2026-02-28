//
//  ReportChatBar.swift
//  ios
//
//  Molecule: Bottom floating chat bar for interacting with the report
//

import SwiftUI

struct ReportChatBar: View {
    let onTapped: () -> Void

    var body: some View {
        Button(action: onTapped) {
            HStack(spacing: AppSpacing.md) {
                Image(systemName: "bubble.left.fill")
                    .font(AppTypography.iconDefault)
                    .foregroundColor(AppColors.primaryBlue)

                Text("Chat with the report")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)

                Spacer()

                Image(systemName: "mic.fill")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                    .fill(AppColors.cardBackground)
                    .shadow(color: .black.opacity(0.2), radius: 8, y: -2)
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack {
        Spacer()
        ReportChatBar(onTapped: {})
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, AppSpacing.lg)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
