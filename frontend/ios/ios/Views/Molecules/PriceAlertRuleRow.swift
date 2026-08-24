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
    /// The per-ticker sheet is already titled with the ticker, so repeating it in every
    /// row is noise. The cross-ticker list has nothing else to tell two rules apart.
    var showsTicker: Bool = false
    let onToggle: () -> Void
    let onDelete: () -> Void

    var body: some View {
        // Same `ActivityRow` as the digest and the notifications. A price rule is the one
        // alert-shaped thing the user CREATED, so it earns the same row as the ones the
        // app sends them.
        ActivityRow(
            systemName: "bell.badge",
            // `caution` reads as "armed and waiting", and matches the price-alert glyph
            // the notification rows use for the same kind.
            iconColor: alert.isActive ? AppColors.caution : AppColors.textMuted,
            title: showsTicker ? "\(alert.ticker) · \(alert.summary)" : alert.summary,
            subtitle: alert.quietReason ?? alert.repeatRule.label,
            trailing: {
                HStack(spacing: AppSpacing.md) {
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
            }
        )
        // An inactive rule dims as a WHOLE — text, glyph and controls together. Dimming
        // only the label (the previous behaviour) left a full-strength toggle beside
        // greyed text, which reads as a broken control rather than a paused rule.
        .opacity(alert.isActive ? 1 : 0.55)
    }
}
