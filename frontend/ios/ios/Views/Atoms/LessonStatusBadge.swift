//
//  LessonStatusBadge.swift
//  ios
//
//  Atom: Badge showing lesson status (Completed, Up Next, Not Started)
//

import SwiftUI

struct LessonStatusBadge: View {
    let status: LessonStatus

    private var showBadge: Bool {
        status != .notStarted
    }

    var body: some View {
        if showBadge {
            HStack(spacing: AppSpacing.xs) {
                if status == .completed {
                    Image(systemName: "checkmark")
                        .font(.system(size: 9, weight: .bold))
                }

                Text(status.rawValue)
                    .font(AppTypography.caption)
                    .fontWeight(.medium)
            }
            .foregroundColor(status.color)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(status.backgroundColor)
            .cornerRadius(AppCornerRadius.small)
        } else {
            Text(status.rawValue)
                .font(AppTypography.caption)
                .foregroundColor(status.color)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        LessonStatusBadge(status: .completed)
        LessonStatusBadge(status: .upNext)
        LessonStatusBadge(status: .notStarted)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
