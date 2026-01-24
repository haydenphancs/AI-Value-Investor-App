//
//  StudyScheduleRow.swift
//  ios
//
//  Molecule: Row in study schedule section with title, subtitle, and time/toggle
//

import SwiftUI

struct StudyScheduleRow: View {
    let title: String
    let subtitle: String

    // For toggle style
    var isToggle: Bool = false
    @Binding var isEnabled: Bool

    // For time style
    var time: String? = nil
    var timeColor: Color = AppColors.primaryBlue

    init(
        title: String,
        subtitle: String,
        isEnabled: Binding<Bool>
    ) {
        self.title = title
        self.subtitle = subtitle
        self.isToggle = true
        self._isEnabled = isEnabled
        self.time = nil
    }

    init(
        title: String,
        subtitle: String,
        time: String,
        timeColor: Color = AppColors.primaryBlue
    ) {
        self.title = title
        self.subtitle = subtitle
        self.isToggle = false
        self._isEnabled = .constant(false)
        self.time = time
        self.timeColor = timeColor
    }

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)

                Text(subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            if isToggle {
                Toggle("", isOn: $isEnabled)
                    .labelsHidden()
                    .tint(AppColors.bullish)
            } else if let time = time {
                StudyScheduleTimeLabel(time: time, color: timeColor)
            }
        }
        .padding(.vertical, AppSpacing.sm)
    }
}

#Preview {
    VStack(spacing: 0) {
        StudyScheduleRow(
            title: "Daily Reminder",
            subtitle: "",
            isEnabled: .constant(true)
        )

        Divider()
            .background(AppColors.cardBackgroundLight)

        StudyScheduleRow(
            title: "Morning Session",
            subtitle: "Best for focus",
            time: "9:00 AM"
        )

        Divider()
            .background(AppColors.cardBackgroundLight)

        StudyScheduleRow(
            title: "Review Time",
            subtitle: "Reinforce learning",
            time: "8:00 PM",
            timeColor: AppColors.textSecondary
        )
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
