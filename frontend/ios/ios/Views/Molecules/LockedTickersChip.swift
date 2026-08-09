//
//  LockedTickersChip.swift
//  ios
//
//  Molecule: the "+N more" chip that closes the Updates ticker strip when the user's
//  plan is showing fewer tickers than their active group holds.
//
//  Placed beside `UpdatesTabButton` rather than in Atoms because it is part of that
//  strip and deliberately copies its geometry — same capsule, same padding, same
//  typography — so the locked chip reads as one more pill in the row rather than as a
//  banner bolted onto the end. Anything that drifts here will look misaligned there.
//
//  It shows a COUNT, never a list of the hidden symbols. Naming them would turn a plan
//  boundary into a teaser, and the count is the honest thing the server actually sends
//  (`locked_count` on `GET /updates/tabs`, computed by the code that enforced the limit).
//
//  Colours use TEXT-role tokens (`primaryBlue`), not `*Graphic` ones: every glyph here is
//  read as text and has to clear 4.5:1 in both appearances.
//

import SwiftUI

struct LockedTickersChip: View {
    let count: Int
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "lock.fill")
                    .font(AppTypography.iconXS).fontWeight(.medium)
                    .foregroundColor(AppColors.primaryBlue)

                Text("+\(count) more")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            .background(AppColors.cardBackgroundLight)
            .clipShape(Capsule())
        }
        .buttonStyle(PlainButtonStyle())
        // One label, because VoiceOver reads the chip as a single control. Without it the
        // lock glyph and the text are announced as two unrelated fragments.
        .accessibilityLabel("\(count) more tickers, locked")
        .accessibilityHint("Shows upgrade options")
    }
}

#Preview {
    HStack(spacing: 10) {
        UpdatesTabButton(
            tab: NewsFilterTab(title: "Market", ticker: nil, changePercent: nil, isMarketTab: true),
            isSelected: true,
            action: {}
        )
        UpdatesTabButton(
            tab: NewsFilterTab(title: "BDU", ticker: "BDU", changePercent: 2.4, isMarketTab: false),
            isSelected: false,
            action: {}
        )
        LockedTickersChip(count: 4, action: {})
    }
    .padding()
    .background(AppColors.background)
}
