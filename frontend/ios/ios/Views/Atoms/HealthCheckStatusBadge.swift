//
//  HealthCheckStatusBadge.swift
//  ios
//
//  Atom: Badge displaying health check rating status (e.g., "[2/4] Mix")
//

import SwiftUI

struct HealthCheckStatusBadge: View {
    let rating: HealthCheckRating
    let passedCount: Int
    let totalCount: Int

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            // Checkmark icon
            Image(systemName: rating.iconName)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(rating.color)

            // Rating text: [2/4] Mix
            Text("[\(passedCount)/\(totalCount)] \(rating.rawValue)")
                .font(AppTypography.calloutBold)
                .foregroundColor(rating.color)
        }
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.xs)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .fill(rating.color.opacity(0.15))
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            HealthCheckStatusBadge(rating: .excellent, passedCount: 4, totalCount: 4)
            HealthCheckStatusBadge(rating: .good, passedCount: 3, totalCount: 4)
            HealthCheckStatusBadge(rating: .mix, passedCount: 2, totalCount: 4)
            HealthCheckStatusBadge(rating: .caution, passedCount: 1, totalCount: 4)
            HealthCheckStatusBadge(rating: .poor, passedCount: 0, totalCount: 4)
        }
        .padding()
    }
}
