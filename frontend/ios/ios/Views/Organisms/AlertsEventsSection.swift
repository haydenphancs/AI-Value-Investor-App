//
//  AlertsEventsSection.swift
//  ios
//
//  Organism: the "Upcoming & Events" digest — earnings dates, whale trades, analyst grade
//  changes and insider transactions computed over the tracking feed.
//
//  It is NOT notifications and NOT price-alert rules. It used to be titled "Alerts & Upcoming
//  Events" and live in the ASSETS tab, which made three different features share one word
//  across two tabs. It sits in Tracking → Alerts now, titled for what it actually is.
//
//  Rendered inside the Alerts tab's single `LazyVStack`, so the stack here is a plain `VStack`
//  on purpose — a nested lazy stack renders eagerly and buys nothing for a bounded list.
//

import SwiftUI

struct AlertsEventsSection: View {
    let alerts: [AppAlert]
    var onAlertTapped: ((AppAlert) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            SectionHeader(title: "Upcoming & Events")
                .padding(.horizontal, AppSpacing.lg)

            if alerts.isEmpty {
                // This section used to render its header unconditionally over nothing, which
                // reads as a broken screen rather than a quiet week.
                InlineRetryNotice(
                    message: "Nothing coming up for what you track. Earnings dates, whale "
                        + "trades and analyst changes will appear here.",
                    systemImage: "calendar",
                    iconColor: AppColors.textMuted
                )
                .padding(.horizontal, AppSpacing.lg)
            } else {
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
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.lg) {
            AlertsEventsSection(alerts: AppAlert.sampleData)
            AlertsEventsSection(alerts: [])
        }
        .padding(.top, AppSpacing.lg)
    }
    .background(AppColors.background)
}
