//
//  ExclusiveSignalsSection.swift
//  ios
//
//  Organism: the glowing "App-Exclusive Signals" card with the CAYDEX badge and
//  a stack of expandable signal rows.
//

import SwiftUI

struct ExclusiveSignalsSection: View {
    let signals: [ExclusiveSignal]
    var accent: Color = AppColors.primaryBlue
    var onLeaderTap: ((String, SignalLeader) -> Void)? = nil
    /// Fired when a tap lands on the panel or row BODY (not a child control).
    ///
    /// ⚠️ The SWALLOW is the load-bearing part, not this closure. `.onTapGesture` below
    /// consumes the tap so it cannot bubble to the Home screen's collapse gesture and close
    /// the very row you just touched — that holds whether or not a handler is supplied.
    ///
    /// The closure itself is currently UNWIRED. It used to carry the cross-section collapse
    /// ("this tap is outside the OTHER section, so close it"), which was the machinery
    /// enforcing one-open-at-a-time. Every card can now be expanded at once, so Home passes
    /// nothing. Kept because the hook is the natural place for a future caller to react.
    var onBodyTap: (() -> Void)? = nil
    /// Fired when a LOCKED row is tapped (Free/guest) — carries the signal kind. The row
    /// does not expand; the Home screen answers this with the paywall.
    var onLockedTap: ((String) -> Void)? = nil
    /// Which signal row is expanded (nil = none). Lifted to the Home screen so a
    /// tap outside the row collapses it; also enforces one-open-at-a-time.
    /// A SET, not a single optional id — every row can be open at once. Same change, and the
    /// same reason, as `DailyScannersSection.expandedCardIDs`.
    @Binding var expandedSignalIDs: Set<ExclusiveSignal.ID>

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text("App-Exclusive Signals")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                TintedTagBadge(text: "CAYDEX", color: accent,
                               systemImage: AppSymbols.ai,
                               backgroundOpacity: 0.14,
                               font: AppTypography.captionSmallEmphasis,
                               tracking: 0.4)
            }

            Text("Signals you won't find on free trackers.")
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textSecondary)
                .padding(.top, 3)
                .padding(.bottom, 14)

            VStack(spacing: 9) {
                ForEach(signals) { signal in
                    SignalDisclosureRow(
                        signal: signal,
                        onLeaderTap: onLeaderTap,
                        onBodyTap: onBodyTap,
                        onLockedTap: onLockedTap,
                        isExpanded: Binding(
                            get: { expandedSignalIDs.contains(signal.id) },
                            set: { isOn in
                                if isOn { expandedSignalIDs.insert(signal.id) }
                                else { expandedSignalIDs.remove(signal.id) }
                            }
                        )
                    )
                }
            }
        }
        .padding(16)
        // ⚠️ The glow is drawn on this background SHAPE, never on the card itself.
        //
        // `.shadow()` is derived from the alpha of everything beneath it, so a `.shadow`
        // applied to the card would force SwiftUI to render the ENTIRE section — title,
        // badge, subtitle, all three rows — into an offscreen layer to compute an 18pt
        // blur. A row expanding animates this section's height, so that layer would be
        // re-allocated and re-rasterized every frame at a new size. That is the classic
        // whole-card flicker, and it was reported as "the title and the whole card is
        // blinking when I expand".
        //
        // Shadowing a filled RoundedRectangle instead rasterizes one shape and leaves the
        // content out of the offscreen pass entirely. Same glow, nothing to re-raster.
        //
        // This is the second half of a problem this file already fixed once: a perpetual
        // `.repeatForever` glow drove stroke/shadow here, entangled with a row's expand
        // `withAnimation` transaction, and hard-froze the main thread on tap-to-expand.
        // The ANIMATED half was removed then; the shadow-over-content half was left.
        // (If a breathing glow is ever wanted back, drive it on a sibling overlay that is
        // NOT an ancestor of the expandable rows.)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(
                    LinearGradient(
                        // Premium banner. Adaptive so the (now theme-aware) text inside
                        // stays readable in Light: a soft blue-tinted light gradient in
                        // Light, the original deep navy in Dark.
                        colors: [Color(lightHex: "FFFFFF", darkHex: "1B2233"),
                                 Color(lightHex: "EEF2FA", darkHex: "161B29")],
                        startPoint: .top, endPoint: .bottom
                    )
                )
                .shadow(color: accent.opacity(0.22), radius: 18, x: 0, y: 0)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(accent.opacity(0.38), lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        // Taps on the card body (padding / title / row gaps) count as "outside the
        // expanded row" → collapse it. Still swallows the tap so it doesn't bubble
        // to the Home scroll's collapse .onTapGesture — but forwards it via
        // onBodyTap so an expanded Daily Scanner card collapses too. Taps ON a row
        // are swallowed inside SignalDisclosureRow; its buttons win their hit area.
        .contentShape(Rectangle())
        .onTapGesture {
            if !expandedSignalIDs.isEmpty {
                // Unanimated, to match the expand above — an animated COLLAPSE resizes
                // the section every frame just as an animated expand does.
                expandedSignalIDs.removeAll()
            }
            onBodyTap?()
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    ExclusiveSignalsSectionPreviewHost()
}

/// Stateful host so the preview can actually expand/collapse a row.
private struct ExclusiveSignalsSectionPreviewHost: View {
    @State private var expandedIDs: Set<ExclusiveSignal.ID> = []
    var body: some View {
        ScrollView {
            ExclusiveSignalsSection(
                signals: MockHomeRepository.signals,
                expandedSignalIDs: $expandedIDs
            )
            .padding(.vertical)
        }
        .background(AppColors.background)
    }
}
