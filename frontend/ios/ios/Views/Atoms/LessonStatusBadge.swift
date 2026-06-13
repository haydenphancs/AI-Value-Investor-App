//
//  LessonStatusBadge.swift
//  ios
//
//  Atom: Badge showing lesson status (Completed, Up Next, Not Started)
//

import SwiftUI

struct LessonStatusBadge: View {
    let status: LessonStatus

    var body: some View {
        switch status {
        case .completed:
            // Just a green check — the full "Completed" label was too long and got clipped
            // on the lesson card. The checkmark alone reads clearly as done.
            Image(systemName: "checkmark.circle.fill")
                .font(AppTypography.iconSmall)
                .foregroundColor(status.color)

        case .upNext:
            Text(status.rawValue)
                .font(AppTypography.caption)
                .fontWeight(.medium)
                .foregroundColor(status.color)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(status.backgroundColor)
                .cornerRadius(AppCornerRadius.small)

        case .notStarted:
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
