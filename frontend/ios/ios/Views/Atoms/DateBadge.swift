//
//  DateBadge.swift
//  ios
//
//  Atom: Calendar-style date badge
//

import SwiftUI

struct DateBadge: View {
    let day: String
    let month: String

    init(day: String, month: String) {
        self.day = day
        self.month = month
    }

    init(from date: Date) {
        let formatter = DateFormatter()
        formatter.dateFormat = "d"
        self.day = formatter.string(from: date)
        formatter.dateFormat = "MMM"
        self.month = formatter.string(from: date).uppercased()
    }

    var body: some View {
        VStack(spacing: 0) {
            Text(day)
                .font(AppTypography.dataHeading)
                .foregroundColor(AppColors.textOnAccent)

            Text(month)
                .font(AppTypography.captionSmallEmphasis)
                .foregroundColor(AppColors.textOnAccent.opacity(0.8))
        }
        .frame(width: 48, height: 48)
        // `primaryFill`, not `alertBlue` (which forwards to the TEXT-safe `primaryBlue`,
        // #60A5FA in dark — white on it is 2.24:1). Both halves move together.
        .background(AppColors.primaryFill)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    VStack(spacing: 20) {
        DateBadge(day: "24", month: "FEB")
        DateBadge(from: Date())
    }
    .padding()
    .background(AppColors.background)
}
