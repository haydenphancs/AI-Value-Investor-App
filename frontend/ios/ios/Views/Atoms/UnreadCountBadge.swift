//
//  UnreadCountBadge.swift
//  ios
//
//  Atom: the red unread-count pill, used by every surface that counts the same thing.
//

import SwiftUI

/// The unread-notification count badge. Renders nothing at `count <= 0`.
///
/// ONE atom on purpose. The bottom tab bar and the Tracking → Alerts segment both display
/// `AppState.unreadNotificationCount`, and they are visible at the same time on the same screen —
/// so the moment the recipe is written twice, one number can render in two colours. Callers supply
/// only the count and their own `.offset`.
///
/// ⚠️ COLOUR — `lossFill` + `textOnFill`, and they move together.
///
/// This badge was blue (`primaryFill` + `textOnAccent`) because the obvious red pairing,
/// `lossFill` + `textOnAccent`, is **wrong**: `lossFill` is ADAPTIVE and its dark arm is a light
/// red (#F87171), which puts white ink at 2.77:1 — the theme guard caught it, and that rejection is
/// what the old comment on `TabBarItem` recorded.
///
/// `textOnFill` is the ink that exists for exactly this case (`.claude/rules/ios-swiftui.md`, and
/// AppTheme's fill/ink table): white in light, near-black in dark. On `lossFill` it measures
/// 5.55:1 light and 6.41:1 dark, so the badge is red in BOTH appearances and legible in both.
/// A TestFlight tester asked for a red count; this is the pairing that delivers it without
/// re-opening the contrast bug. Do not swap the ink back to `textOnAccent`.
struct UnreadCountBadge: View {
    let count: Int

    var body: some View {
        if count > 0 {
            Text(count > 99 ? "99+" : "\(count)")
                .font(AppTypography.captionSmallEmphasis)
                .foregroundColor(AppColors.textOnFill)
                .padding(.horizontal, 5)
                .padding(.vertical, 1)
                .background(
                    Capsule().fill(AppColors.lossFill)
                )
                // The count is announced by the enclosing control's accessibility label —
                // reading it twice is worse than not reading it here.
                .accessibilityHidden(true)
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        UnreadCountBadge(count: 1)
        UnreadCountBadge(count: 12)
        UnreadCountBadge(count: 250)
        UnreadCountBadge(count: 0)
    }
    .padding()
    .background(AppColors.background)
}
