//
//  LearnerCountBadge.swift
//  ios
//
//  Atom: Badge showing number of learners for educational content
//

import SwiftUI

struct LearnerCountBadge: View {
    let count: String
    var showIcon: Bool = true

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if showIcon {
                Image(systemName: "person.2.fill")
                    .font(.system(size: 10, weight: .medium))
            }

            Text("\(count) learners")
                .font(AppTypography.caption)
        }
        .foregroundColor(AppColors.textSecondary)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        LearnerCountBadge(count: "1.4k")
        LearnerCountBadge(count: "500", showIcon: false)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
