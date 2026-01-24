//
//  StudyScheduleTimeLabel.swift
//  ios
//
//  Atom: Time label for study schedule (e.g., "9:00 AM")
//

import SwiftUI

struct StudyScheduleTimeLabel: View {
    let time: String
    var isEditable: Bool = true
    var color: Color = AppColors.primaryBlue

    var body: some View {
        Text(time)
            .font(AppTypography.calloutBold)
            .foregroundColor(color)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        StudyScheduleTimeLabel(time: "9:00 AM")
        StudyScheduleTimeLabel(time: "8:00 PM", color: AppColors.textSecondary)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
