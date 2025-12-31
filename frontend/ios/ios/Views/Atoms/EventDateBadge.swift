//
//  EventDateBadge.swift
//  ios
//
//  Atom: Date badge for events showing day and month
//

import SwiftUI

struct EventDateBadge: View {
    let day: String
    let month: String

    var body: some View {
        VStack(spacing: 0) {
            Text(day)
                .font(.system(size: 20, weight: .bold))
                .foregroundColor(AppColors.textPrimary)

            Text(month)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(width: 48, height: 48)
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    HStack(spacing: 20) {
        EventDateBadge(day: "22", month: "FEB")
        EventDateBadge(day: "24", month: "FEB")
        EventDateBadge(day: "1", month: "MAR")
    }
    .padding()
    .background(AppColors.background)
}
