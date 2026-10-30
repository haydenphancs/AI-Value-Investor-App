//
//  LiveBadge.swift
//  ios
//
//  Atom: the red "LIVE" capsule — a report whose results are coming in right now (Tracking →
//  Alerts → Upcoming, and the live results sheet). Owner request 2026-10-29: "mark a 'live'
//  red color label at the card on the left, so users can know this is live".
//
//  Red TEXT (`AppColors.loss`, text-safe) on an OPAQUE `cardBackgroundLight` capsule — never
//  red on its own tint, which no text token survives in light mode (`TintedTagBadge` header).
//  The pulsing dot is `LiveIndicator`, the one `LiveNewsHeader` uses; with Reduce Motion it is
//  a still dot. Shown ONLY for `EarningsLiveStatus.live`, which the server sets only while it
//  is genuinely checking for results — the same honesty rule as the "Live News" header.
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
        AlertCategoryIcon(systemName: "bell.fill", color: AppColors.primaryBlue)
            .overlay(alignment: .bottom) { LiveBadge().offset(y: 9) }
    }
    .padding(40)
    .background(AppColors.cardBackground)
}
