//
//  AlertsEventsSection.swift
//  ios
//
//  Organism: Alerts & Upcoming Events section
//

import SwiftUI

struct AlertsEventsSection: View {
    let alerts: [AppAlert]
    var onAlertTapped: ((AppAlert) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section Header
            Text("Alerts & Upcoming Events")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Alert Cards
            VStack(spacing: AppSpacing.md) {
                ForEach(alerts) { alert in
                    AlertCardView(alert: alert) {
                        onAlertTapped?(alert)
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        AlertsEventsSection(
            alerts: AppAlert.sampleData
        )
        .padding(.top, AppSpacing.lg)
    }
    .background(AppColors.background)
}
