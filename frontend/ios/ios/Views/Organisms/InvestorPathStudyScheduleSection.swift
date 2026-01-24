//
//  InvestorPathStudyScheduleSection.swift
//  ios
//
//  Organism: Study schedule section with reminder toggle and time settings
//

import SwiftUI

struct InvestorPathStudyScheduleSection: View {
    @Binding var schedule: StudySchedule
    var onMorningTimeTap: (() -> Void)?
    var onReviewTimeTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section title
            Text("Study Schedule")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Schedule card
            VStack(spacing: 0) {
                // Daily Reminder toggle
                StudyScheduleRow(
                    title: "Daily Reminder",
                    subtitle: "",
                    isEnabled: $schedule.dailyReminderEnabled
                )
                .padding(.horizontal, AppSpacing.lg)

                Divider()
                    .background(AppColors.cardBackgroundLight)
                    .padding(.horizontal, AppSpacing.lg)

                // Morning Session
                Button(action: {
                    onMorningTimeTap?()
                }) {
                    StudyScheduleRow(
                        title: "Morning Session",
                        subtitle: "Best for focus",
                        time: schedule.formattedMorningTime
                    )
                    .padding(.horizontal, AppSpacing.lg)
                }
                .buttonStyle(PlainButtonStyle())

                Divider()
                    .background(AppColors.cardBackgroundLight)
                    .padding(.horizontal, AppSpacing.lg)

                // Review Time
                Button(action: {
                    onReviewTimeTap?()
                }) {
                    StudyScheduleRow(
                        title: "Review Time",
                        subtitle: "Reinforce learning",
                        time: schedule.formattedReviewTime,
                        timeColor: AppColors.textSecondary
                    )
                    .padding(.horizontal, AppSpacing.lg)
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(.vertical, AppSpacing.sm)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    VStack {
        InvestorPathStudyScheduleSection(
            schedule: .constant(.defaultSchedule)
        )
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
