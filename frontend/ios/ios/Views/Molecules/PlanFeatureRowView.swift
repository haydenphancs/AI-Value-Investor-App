//
//  PlanFeatureRowView.swift
//  ios
//
//  Molecule: one row on the upgrade screen — icon tile, title carrying the number, and a
//  sentence of detail. Takes exactly one domain model (`PlanFeature`), no network, no
//  AppState.
//
//  ⚠️ NOT `FeatureRow`, despite the obvious overlap. Three things make that molecule the
//  wrong host for server-driven copy:
//    • it is typed to `AnalysisFeature`, whose `iconColor` is a raw `Color` — server data
//      would bypass the accent-key clamping entirely and put an unaudited colour on screen;
//    • it caps `detail` at `.lineLimit(2)`, which truncates a served sentence at large
//      Dynamic Type sizes;
//    • `AnalysisFeature.id = UUID()` mints a new identity on every init, so switching the
//      selected plan re-creates the whole list instead of diffing values — and the
//      value-diff IS this screen's comparison affordance.
//

import SwiftUI

struct PlanFeatureRowView: View {
    let feature: PlanFeature
    /// The row the user was locked out of when the sheet opened.
    var isHighlighted: Bool = false

    /// A locked row is drawn muted with a lock glyph rather than struck through: it is a
    /// capability this plan does not include, not a deleted one, and strikethrough on a
    /// wrapped two-line sentence is close to unreadable.
    private var tint: Color { feature.included ? feature.accent : AppColors.textMuted }

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            ZStack(alignment: .bottomTrailing) {
                if let symbol = feature.symbol {
                    IconTile(systemName: symbol, accent: tint, size: 36,
                             cornerRadius: 10, iconPointSize: 17)
                } else {
                    // Server sent a symbol this OS build doesn't have. Keep the row's
                    // rhythm with an empty tile rather than reflowing the whole list.
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(tint.opacity(0.16))
                        .frame(width: 36, height: 36)
                }

                if !feature.included {
                    Image(systemName: "lock.fill")
                        .font(AppTypography.iconTiny).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                        .padding(3)
                        .background(Circle().fill(AppColors.cardBackground))
                        .offset(x: 4, y: 4)
                        .accessibilityHidden(true)
                }
            }

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(feature.title)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(feature.included ? AppColors.textPrimary : AppColors.textSecondary)
                Text(feature.detail)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }
            // No `lineLimit`. The copy arrives from the server and is read at every
            // Dynamic Type size; `fixedSize` makes the row grow instead of clipping.
            .fixedSize(horizontal: false, vertical: true)

            Spacer(minLength: 0)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .cardFill(AppColors.cardBackgroundNested)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(
                            isHighlighted ? AppColors.primaryBlue.opacity(0.5) : Color.clear,
                            lineWidth: 1.5
                        )
                )
        )
        // One label: VoiceOver otherwise reads the lock badge, the title and the detail as
        // three unrelated fragments, and the lock is the part that changes the meaning.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            feature.included
                ? "\(feature.title). \(feature.detail)"
                : "Locked. \(feature.title). \(feature.detail)"
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.sm) {
        ForEach(
            PlanFeature.bundled(for: .free, monthlyCredits: 50, reportCost: 20, chatCost: 1)
                .filter { !$0.isAlwaysIncluded }
        ) { feature in
            PlanFeatureRowView(feature: feature, isHighlighted: feature.key == "signals")
        }
    }
    .padding()
    .background(AppColors.background)
}
