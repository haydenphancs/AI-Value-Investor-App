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
                    .font(.system(size: 10, weight: .bold))
            }

            Text(status.rawValue)
                .font(AppTypography.caption)
                .fontWeight(.semibold)
        }
        .foregroundColor(status.color)
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.xs)
        .background(
            Capsule()
                .fill(status.backgroundColor)
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
    .preferredColorScheme(.dark)
}
