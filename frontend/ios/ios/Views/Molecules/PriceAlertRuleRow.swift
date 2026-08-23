//
//  PriceAlertRuleRow.swift
//  ios
//
//  Molecule: one price-alert RULE — summary, why it is quiet, an on/off toggle and delete.
//
//  Shared by the per-ticker bell sheet (`PriceAlertsSheet`) and the cross-ticker list in
//  Tracking → Alerts. Extracted rather than copied because the two differ in exactly one
//  thing — whether the ticker is already implied by the surrounding screen — and a forked
//  copy would drift on the parts that matter (the `armed == false` explainer, and the fact
//  that an inactive rule must dim its TEXT rather than swap its fill).
//

import SwiftUI

struct PriceAlertRuleRow: View {
    let alert: PriceAlertDTO
    /// The per-ticker sheet is already titled with the ticker, so repeating it in every row is
    /// noise. The cross-ticker list has nothing else to tell two rules apart.
    var showsTicker: Bool = false
    let onToggle: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                if showsTicker {
                    Text(alert.ticker)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Text(alert.summary)
                    .font(AppTypography.body)
                    .foregroundColor(alert.isActive ? AppColors.textPrimary : AppColors.textMuted)

                // Surfaces `armed == false`. Without it, a rule that is active but latched
                // after firing looks simply broken.
                if let reason = alert.quietReason {
                    Text(reason)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                } else {
                    Text(alert.repeatRule.label)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer()

            Toggle("", isOn: Binding(
                get: { alert.isActive },
                set: { _ in onToggle() }
            ))
            .labelsHidden()
            .tint(AppColors.primaryBlue)

            Button(action: onDelete) {
                Image(systemName: "trash")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.loss)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Delete alert \(alert.summary)")
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
    }
}
