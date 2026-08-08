//
//  NotificationToggleRow.swift
//  ios
//
//  Moved out of `Views/Screens/NotificationsSettingsView.swift`, where it had been
//  declared at the bottom of a Screen file. It composes atoms and takes no domain model,
//  which is the molecule test — and now that the Notifications screen renders four groups
//  instead of one, it has real reuse rather than a single call site.
//

import SwiftUI

struct NotificationToggleRow: View {
    let title: String
    let subtitle: String
    @Binding var isOn: Bool
    var disabled: Bool = false
    /// Shown instead of `subtitle` when the row is disabled, so a dimmed control explains
    /// itself. A greyed-out toggle with no reason reads as a bug.
    var disabledReason: String?

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.body)
                    .foregroundColor(disabled ? AppColors.textMuted : AppColors.textPrimary)

                Text(disabled ? (disabledReason ?? subtitle) : subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer(minLength: AppSpacing.md)

            Toggle("", isOn: $isOn)
                .labelsHidden()
                .tint(AppColors.primaryBlue)
                .disabled(disabled)
                .opacity(disabled ? 0.4 : 1)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(title)
        .accessibilityHint(disabled ? (disabledReason ?? subtitle) : subtitle)
    }
}

#Preview {
    VStack(spacing: 1) {
        NotificationToggleRow(
            title: "Unusual Price Moves",
            subtitle: "When a stock you track moves far more than it normally does",
            isOn: .constant(true)
        )
        NotificationToggleRow(
            title: "Institutional Filings",
            subtitle: "13F filings — disclosed up to 45 days after the trade",
            isOn: .constant(false),
            disabled: true,
            disabledReason: "Turn on Smart Money to enable this"
        )
    }
    .padding()
    .background(AppColors.background)
}
