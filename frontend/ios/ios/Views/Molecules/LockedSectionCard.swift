//
//  LockedSectionCard.swift
//  ios
//
//  Molecule: one card explaining that a section is behind a plan, tappable to open the
//  upgrade sheet. Lifted verbatim from `WhaleProfileView.lockedSection` (2026-09-17) so
//  the Holders tab's Congress segment (Pro/Max) gets the SAME treatment as the whale
//  profile's Current Picks / Recent Trades — "add a lock-in here just like the other".
//
//  Why a molecule and not the whale screen's private helper: the whale screen keeps its
//  section HEADER outside the card (a section that vanished would read as "no trades",
//  not "paid"); the Holders card already has its own header and segmented picker, so
//  only the card body is shared. Both hosts present `PaywallView` themselves.
//

import SwiftUI

struct LockedSectionCard: View {
    /// Used for the accessibility label only ("Congress, locked").
    let title: String
    let message: String
    /// True when hosted INSIDE another card (the Holders tab's Smart Money and Recent
    /// Activities cards). A card nested in a card must take `cardBackgroundNested` — it
    /// otherwise shares its parent's fill, measures 1.00:1 against it in dark and vanishes
    /// (the Recent Activities rows already did exactly that once). The whale profile hosts
    /// it standalone and keeps the default surface.
    var nested: Bool = false
    let onTap: () -> Void

    init(title: String, message: String, nested: Bool = false, onTap: @escaping () -> Void) {
        self.title = title
        self.message = message
        self.nested = nested
        self.onTap = onTap
    }

    var body: some View {
        Button(action: onTap) {
            VStack(spacing: AppSpacing.sm) {
                // A TEXT-role token — this glyph must clear 4.5:1 in both appearances.
                // A *Graphic token would fail the launch contrast audit.
                Image(systemName: "lock.fill")
                    .font(AppTypography.iconMedium)
                    .foregroundColor(AppColors.primaryBlue)

                Text(message)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)

                Text("Upgrade to unlock")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.xl)
            .padding(.horizontal, AppSpacing.lg)
            .cardSurface(nested ? AppColors.cardBackgroundNested : AppColors.cardBackground,
                         cornerRadius: AppCornerRadius.large)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title), locked")
        .accessibilityHint("Shows upgrade options")
    }
}

#Preview {
    ZStack {
        AppColors.background.ignoresSafeArea()
        LockedSectionCard(
            title: "Congress",
            message: "Congressional trades in this stock are part of a plan. Insider and institutional flow stay free.",
            onTap: {}
        )
        .padding()
    }
}
