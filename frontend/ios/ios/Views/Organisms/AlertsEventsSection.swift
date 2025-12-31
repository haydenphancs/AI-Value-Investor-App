//
//  AlertsEventsSection.swift
//  ios
//
//  Organism: Alerts & Upcoming Events section
//

import SwiftUI

struct AlertsEventsSection: View {
    let alerts: [AlertEvent]
    let smartMoneyAlert: SmartMoneyAlert?
    var onAlertTapped: ((AlertEvent) -> Void)?
    var onSmartMoneyTapped: (() -> Void)?

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
                    AlertEventCard(alert: alert) {
                        onAlertTapped?(alert)
                    }
                }

                // Smart Money Card
                if let smartMoney = smartMoneyAlert {
                    SmartMoneyCard(alert: smartMoney) {
                        onSmartMoneyTapped?()
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
            alerts: AlertEvent.sampleData,
            smartMoneyAlert: SmartMoneyAlert.sampleData
        )
        .padding(.top, AppSpacing.lg)
    }
    .background(AppColors.background)
}
