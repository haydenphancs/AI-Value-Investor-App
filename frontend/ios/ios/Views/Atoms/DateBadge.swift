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
                .font(.system(size: 20, weight: .bold, design: .rounded))
                .foregroundColor(.white)

            Text(month)
                .font(.system(size: 10, weight: .semibold))
                .foregroundColor(.white.opacity(0.8))
        }
        .frame(width: 48, height: 48)
        .background(AppColors.alertBlue)
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
