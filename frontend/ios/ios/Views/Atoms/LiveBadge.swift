//
//  LiveBadge.swift
//  ios
//
//  Atom: the red "LIVE" capsule — a report whose results come out today (Tracking → Alerts →
//  Upcoming, and the live results sheet). Owner request 2026-10-29: "mark a 'live' red color
//  label at the card on the left, so users can know this is live".
//
//  Red TEXT (`AppColors.loss`, text-safe) on an OPAQUE `cardBackgroundLight` capsule — never
//  red on its own tint, which no text token survives in light mode (`TintedTagBadge` header).
//  The pulsing dot is `LiveIndicator`, the one `LiveNewsHeader` uses; with Reduce Motion it is
//  a still dot. Shown ONLY for `EarningsLiveStatus.live` or the server's LIVE label: from 03:00 ET
//  (earlier once a figure is in) to midnight ET on the report day, its results in or not (owner,
//  2026-11-14: "make the 'live' all day long, the day that company report earning", then "3 AM
//  New York"; 2026-11-20: "LIVE all day until midnight", `earnings_live_label` / `live_label`),
//  never from a clock on the device.
//

import SwiftUI

struct LiveBadge: View {
    var text: String = "LIVE"

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 3) {
            if reduceMotion {
                Circle()
                    .fill(AppColors.loss)
                    .frame(width: 6, height: 6)
            } else {
                LiveIndicator()
                    .scaleEffect(0.7)
                    .frame(width: 10, height: 10)
            }
            Text(text)
                .font(AppTypography.captionSmallEmphasis)
                .tracking(0.4)
                .foregroundColor(AppColors.loss)
                .lineLimit(1)
                .fixedSize()
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(AppColors.cardBackgroundLight)
        .clipShape(Capsule())
        .overlay(Capsule().strokeBorder(AppColors.loss.opacity(0.35), lineWidth: 1))
        // The row/sheet says "Live" in words; the dot itself is decoration.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Live")
    }
}

#Preview {
    VStack(spacing: 20) {
        LiveBadge()
        // As `ActivityRow` hangs it from the glyph.
        AlertCategoryIcon(systemName: "bell.fill", color: AppColors.primaryBlue)
            .overlay(alignment: .bottom) {
                LiveBadge()
                    .fixedSize()
                    .frame(height: 0, alignment: .top)
                    .offset(y: -2)
            }
    }
    .padding(40)
    .background(AppColors.cardBackground)
}
