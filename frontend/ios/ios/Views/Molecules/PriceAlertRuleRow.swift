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
//  PAUSED (delisting Phase 2, owner 2026-11-13): the server stops watching a rule whose ticker
//  is delisted and says so in `paused_reason`. Nothing about the rule is stored differently, so
//  the row says "Paused: delisted", mutes its glyph and disables its toggle (flipping a rule the
//  server is not polling would do nothing); delete still works. The caption is a text token on
//  the row's own opaque card, and an ON paused row is NOT dimmed as a whole — that would push
//  the one line explaining it below its contrast bar. A rule the user turned OFF still dims like
//  every other off rule, paused or not: the server sends `paused_reason` for any rule on a
//  delisted ticker (a fired one-shot rule included), and an undimmed off row read as live.
//

import SwiftUI

struct PriceAlertRuleRow: View {
    let alert: PriceAlertDTO
    /// The per-ticker sheet is already titled with the ticker, so repeating it in every
    /// row is noise. The cross-ticker list has nothing else to tell two rules apart.
    var showsTicker: Bool = false
    let onToggle: () -> Void
    let onDelete: () -> Void

    /// The server is not watching this rule: its ticker is delisted.
    private var isPaused: Bool { alert.isPausedForDelisting }

    /// On AND watched — the only state the "armed and waiting" glyph colour may claim.
    private var isLive: Bool { alert.isActive && !isPaused }

    /// An off rule dims as a whole, paused or not; an ON paused rule keeps its caption at full
    /// strength. Pausing never lifts the dim from a rule the user turned off.
    private var rowOpacity: Double { alert.isActive ? 1 : 0.55 }

    /// VoiceOver on the disabled toggle says why it does not switch.
    private var toggleHint: String {
        isPaused ? "Paused because \(alert.ticker) is delisted." : ""
    }

    var body: some View {
        // Same `ActivityRow` as the digest and the notifications. A price rule is the one
        // alert-shaped thing the user CREATED, so it earns the same row as the ones the
        // app sends them.
        ActivityRow(
            systemName: "bell.badge",
            // `caution` reads as "armed and waiting", and matches the price-alert glyph
            // the notification rows use for the same kind.
            iconColor: isLive ? AppColors.caution : AppColors.textMuted,
            title: showsTicker ? "\(alert.ticker) · \(alert.summary)" : alert.summary,
            subtitle: alert.quietReason ?? alert.repeatRule.label,
            footnote: alert.pausedCaption,
            trailing: { trailingControls }
        )
        // An inactive rule dims as a WHOLE — text, glyph and controls together. Dimming
        // only the label (the previous behaviour) left a full-strength toggle beside
        // greyed text, which reads as a broken control rather than a paused rule.
        .opacity(rowOpacity)
    }

    @ViewBuilder
    private var trailingControls: some View {
        HStack(spacing: AppSpacing.md) {
            Toggle("", isOn: Binding(
                get: { alert.isActive },
                set: { _ in onToggle() }
            ))
            .labelsHidden()
            .tint(AppColors.primaryBlue)
            // The server is not polling a paused rule, so switching it would change nothing
            // the user could see. It comes back on its own if the ticker trades again.
            .disabled(isPaused)
            .accessibilityHint(toggleHint)

            Button(action: onDelete) {
                Image(systemName: "trash")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.loss)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Delete alert \(alert.summary)")
        }
    }
}

#Preview("Live and paused") {
    // The wire word lives here, inside the preview: shipped code reads it only through
    // `DelistedListing.from`.
    VStack(spacing: AppSpacing.md) {
        PriceAlertRuleRow(alert: .previewRule(status: nil, pausedReason: nil),
                          onToggle: {}, onDelete: {})
        PriceAlertRuleRow(alert: .previewRule(status: "delisted", pausedReason: "delisted"),
                          showsTicker: true, onToggle: {}, onDelete: {})
        // Turned off AND paused (e.g. a one-shot rule that fired before the delisting): dimmed
        // like any off rule, "Turned off" above "Paused: delisted", toggle disabled.
        PriceAlertRuleRow(alert: .previewRule(status: "delisted", pausedReason: "delisted",
                                              isActive: false),
                          showsTicker: true, onToggle: {}, onDelete: {})
    }
    .padding(AppSpacing.lg)
    .background(AppColors.background)
}

private extension PriceAlertDTO {
    /// Preview only: a ROKU rule, live (nil, nil) or paused for its delisting (NASDAQ, 2026-11-09),
    /// on or turned off.
    static func previewRule(status: String?, pausedReason: String?,
                            isActive: Bool = true) -> PriceAlertDTO {
        let delisted: Bool = status != nil
        let id: String = delisted ? (isActive ? "preview-paused" : "preview-paused-off") : "preview-live"
        return PriceAlertDTO(
            id: id,
            ticker: "ROKU",
            assetType: "stock",
            kind: PriceAlertKind.above.rawValue,
            threshold: 175,
            repeatMode: PriceAlertRepeat.once.rawValue,
            isActive: isActive,
            armed: true,
            lastPrice: nil,
            lastTriggeredAt: nil,
            triggerCount: 0,
            note: nil,
            createdAt: nil,
            listingStatus: status,
            delistedDate: delisted ? "2026-11-09" : nil,
            delistedExchange: delisted ? "NASDAQ" : nil,
            lastTradeDate: delisted ? "2026-11-06" : nil,
            pausedReason: pausedReason
        )
    }
}
