//
//  AccentFilterChip.swift
//  ios
//
//  Atom: a selectable filter pill that carries ITS OWN accent colour.
//

import SwiftUI

/// A filter chip whose colour is supplied per-instance: selected = solid `accentFill` with
/// `textOnAccent` ink, unselected = the text-safe `accent` at 0.15 with `accent` ink.
///
/// WHY THIS IS AN ATOM. This body lived as `ReportsListSection.personaTagChip`, private to one
/// organism, when the Alerts tab needed the same control — and a TestFlight tester had asked for
/// the Activity filters to look "just like in the Report tab". A second copy of the closure is
/// the drift that request is made of, so the body moved here and Reports adopts it.
///
/// Nothing existing fit: `FlowOptionChips` has the right API but one fixed blue and wraps instead
/// of scrolling, `TabPill` is fill-only with no accent, `TintedTagBadge` has the unselected look
/// but no selection or tap.
///
/// ⚠️ The two halves of the selected pair move TOGETHER. `accentFill` must be a `*Fill` token (or
/// a `Color(themedHex:role:.fill)` clamp) so `AppColors.textOnAccent` clears 4.5:1 on it; passing
/// the text-role accent for both is exactly the failure `.claude/rules/ios-swiftui.md` describes
/// (white on the dark-mode arm of a text token lands around 2.3–2.8:1).
///
/// Geometry is identical in both states, so nothing shifts when the selection toggles.
struct AccentFilterChip: View {
    let label: String
    /// Text-role colour (4.5:1). Used as the ink when unselected and, at 0.15, as the resting fill.
    let accent: Color
    /// Fill-role colour. Carries `textOnAccent` when selected.
    let accentFill: Color
    let isSelected: Bool
    var action: (() -> Void)?

    var body: some View {
        Button {
            action?()
        } label: {
            Text(label)
                .font(AppTypography.caption).fontWeight(.semibold)
                .foregroundColor(isSelected ? AppColors.textOnAccent : accent)
                .padding(.horizontal, AppSpacing.sm)
                .padding(.vertical, AppSpacing.xs)
                .background(
                    Capsule().fill(isSelected ? accentFill : accent.opacity(0.15))
                )
                .fixedSize(horizontal: true, vertical: false)   // keep natural width in the scroll
        }
        .buttonStyle(PlainButtonStyle())
        .accessibilityAddTraits(isSelected ? [.isButton, .isSelected] : .isButton)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        HStack(spacing: AppSpacing.xs) {
            AccentFilterChip(label: "Prices", accent: AppColors.caution,
                             accentFill: AppColors.cautionFill, isSelected: true)
            AccentFilterChip(label: "Earnings", accent: AppColors.primaryBlue,
                             accentFill: AppColors.primaryFill, isSelected: false)
            AccentFilterChip(label: "Smart Money", accent: AppColors.alertOrange,
                             accentFill: AppColors.alertOrangeFill, isSelected: false)
            AccentFilterChip(label: "Reports", accent: AppColors.alertPurple,
                             accentFill: AppColors.alertPurpleFill, isSelected: true)
        }
    }
    .padding()
    .background(AppColors.background)
}
