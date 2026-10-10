//
//  ReportStatusBadge.swift
//  ios
//
//  Atom: Status badge for report cards (Processing, Failed, Ready)
//

import SwiftUI

struct ReportStatusBadge: View {
    let status: ReportStatus

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if status == .processing {
                // Animated dots for processing
                Image(systemName: "ellipsis")
                    .font(AppTypography.iconTiny).fontWeight(.bold)
            }

            Text(status.rawValue)
                .font(AppTypography.caption)
                .fontWeight(.semibold)
        }
        .foregroundColor(status.color)
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.xs)
        .background(
            // Opaque, not `status.backgroundColor` (each status colour on its own 20% tint
            // is 3.89–4.08:1 in light even on a white card — test_ios_theme_parity §6c).
            Capsule()
                .fill(AppColors.cardBackgroundLight)
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ReportStatusBadge(status: .processing)
        ReportStatusBadge(status: .failed)
        ReportStatusBadge(status: .ready)
    }
    .padding()
    .background(AppColors.background)
}
