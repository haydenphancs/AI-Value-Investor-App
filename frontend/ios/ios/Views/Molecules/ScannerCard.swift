//
//  ScannerCard.swift
//  ios
//
//  Molecule: one card in the Home "Daily Scanners" carousel. Renders a header
//  (with an optional tappable "i" popover for cards that carry an explainer), a
//  kind-specific hero metric + sparkline, and an expandable leaderboard. Owns its
//  own ephemeral UI state (gainers/losers toggle, expand, info popover) — the
//  data comes from a single `DailyScanner` model.
//

import SwiftUI

struct ScannerCard: View {
    let scanner: DailyScanner
    var onEntryTap: ((ScannerEntry) -> Void)? = nil
    /// Fired when a tap lands on the card BODY (not a child control). The card
    /// swallows the tap so it can't bubble to the Home collapse gesture and close
    /// THIS card — but that tap is still "outside" every OTHER expandable, so the
    /// Home screen uses this to collapse the expanded App-Exclusive Signals row.
    var onBodyTap: (() -> Void)? = nil

    @State private var moversMode: MoversMode = .gainers
    /// Lifted to the parent (via `DailyScannersSection` → the Home screen) so a
    /// tap OUTSIDE the card can collapse it, and only one card expands at a time.
    @Binding var isExpanded: Bool
    @State private var showInfo = false

    private var list: [ScannerEntry] {
        switch scanner.kind {
        case .movers: return moversMode == .gainers ? scanner.gainers : scanner.losers
        case .volume, .shorts: return scanner.entries
        }
    }

    private var head: ScannerEntry? { list.first }

    private var heroSparkColor: Color {
        switch scanner.kind {
        case .movers, .volume: return (head?.isPositive ?? true) ? AppColors.bullish : AppColors.bearish
        case .shorts: return scanner.accent
        }
    }

    /// Direction the spark tint is CLAIMING, or `nil` where the tint is a category accent
    /// carrying no sentiment. Mirrors `heroSparkColor` exactly — they must stay in step,
    /// because this is the non-colour half of the same encoding.
    private var heroSparkDirection: Bool? {
        switch scanner.kind {
        case .movers, .volume: return head?.isPositive ?? true
        case .shorts: return nil
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.bottom, 13)

            heroRow

            expandButton
                .padding(.top, 13)

            // Expands IN PLACE to reveal the full leaderboard. This is safe now
            // that the carousel (DailyScannersSection) no longer uses
            // `.scrollPosition(id:)` — that modifier's layout-time state write was
            // what deadlocked SwiftUI when a card resized inside the carousel.
            if isExpanded {
                leaderboard
                    // ⚠️ `.opacity` ALONE — do not put `.move(edge: .top)` back.
                    // This content is revealed at the BOTTOM of a clipped container, so a top-edge move
                    // starts it offset UPWARD by its own height and slides it down THROUGH everything
                    // above it, translucent the whole way. On the Daily Scanners card that meant the
                    // leaderboard swept across the card's own header, hero and CTA, and was reported from
                    // TestFlight as "words coming from the background ... looks like a bug".
                    // A fade moves nothing and cannot overlap anything.
                    // (Genuinely top-anchored things — the audio status island, a banner pinned to the top
                    // of a screen — are the opposite case and keep their `.move(edge: .top)`.)
                    .transition(.opacity)
            }
        }
        .padding(15)
        .background(AppColors.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        // Light-only edge, matching every other screen. Overlay rather than
        // `cardSurface` because the clip above is load-bearing: the expanded
        // leaderboard has to be bounded by the same radius.
        .cardBorder(cornerRadius: 18)
        // Swallow taps that land ON the card so a tap OUTSIDE it (caught at the
        // Home screen or a sibling section) collapses it, while a tap on the card
        // itself never does. Inner controls (toggle, expand button, ticker rows)
        // are child buttons and still win the tap; a drag still scrolls the
        // carousel since this is tap-only. The swallowed tap is forwarded via
        // onBodyTap so the Home screen can collapse OTHER expanded sections.
        .contentShape(Rectangle())
        .onTapGesture { onBodyTap?() }
        .animation(.easeInOut(duration: 0.25), value: isExpanded)
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 9) {
            IconTile(systemName: scanner.iconSystemName, accent: scanner.accent,
                     size: 30, cornerRadius: 9, iconPointSize: 17)

            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    // Two lines, not one. This title shares its row with a `fixedSize`
                    // toggle, so at larger content sizes the shortfall lands here — it
                    // reflowed to four lines ("Today's Top Movers") before the toggle
                    // stopped refusing to compress. Two is acceptable, four is not, and
                    // the scale factor keeps a long title on two.
                    Text(scanner.title)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(2)
                        .minimumScaleFactor(0.85)
                        // ⚠️ Load-bearing, and NOT a duplicate of `lineLimit`. Without it this
                        // title SHAKES on every expand.
                        //
                        // `.animation(_:value: isExpanded)` on the card body animates the whole
                        // subtree, so the VStack's height is interpolated. Mid-flight the card is
                        // shorter than its own content, and SwiftUI resolves the shortfall by
                        // compressing whichever child is compressible — which is exactly this
                        // Text, because `minimumScaleFactor` lets it scale and `lineLimit(2)`
                        // lets it drop to one line. Measured at 60fps on Today's Top Movers, the
                        // title went 79px -> 32px (one squeezed line) -> 81px in 250ms, and the
                        // ~26pt the header lost dragged the hero, CTA and leaderboard with it —
                        // reported as "the whole card is shaking".
                        //
                        // `fixedSize(vertical:)` makes it report and occupy its IDEAL height, so
                        // a short proposal can no longer reflow it. The two modifiers above stay:
                        // they handle Dynamic Type, which is driven by WIDTH and is unaffected.
                        .fixedSize(horizontal: false, vertical: true)

                    // Tappable info affordance — shown only when this card carries
                    // an explainer. Tapping pops over the note instead of always
                    // occupying a box in the card.
                    if let note = scanner.infoNote {
                        Button { showInfo = true } label: {
                            Image(systemName: "info.circle.fill")
                                .font(.system(size: 14))
                                .foregroundColor(scanner.accent)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("More about \(scanner.title)")
                        .popover(isPresented: $showInfo) {
                            Text(note)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                                .multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(14)
                                .frame(maxWidth: 260)
                                .presentationCompactAdaptation(.popover)
                        }
                    }
                }
                // Subtitle is optional — an empty string (e.g. the Top Movers card)
                // hides the row without affecting the other cards.
                if !scanner.subtitle.isEmpty {
                    Text(scanner.subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer(minLength: 6)

            if scanner.kind == .movers {
                MoversToggle(mode: $moversMode)
            } else if let badge = scanner.badgeText {
                TintedTagBadge(text: badge, color: scanner.accent)
            }
        }
    }

    // MARK: - Hero row (metric + sparkline)

    private var heroRow: some View {
        HStack(alignment: .center, spacing: 12) {
            heroMetric
                .frame(maxWidth: .infinity, alignment: .leading)

            TintedSparkline(
                points: head?.spark ?? [],
                color: heroSparkColor,
                showBaseline: scanner.kind == .movers,
                showEndDot: true,
                lineWidth: 2.2,
                isPositive: heroSparkDirection,
                spanFrom: head?.sparkFrom ?? 0,
                spanTo: head?.sparkTo ?? 1
            )
            .frame(width: 104, height: 48)
        }
    }

    @ViewBuilder
    private var heroMetric: some View {
        if let head {
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Text(head.symbol)
                        .font(AppTypography.dataLarge)
                        .foregroundColor(AppColors.textPrimary)
                    Text(head.name)
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(1)
                }

                switch scanner.kind {
                case .movers:
                    Text(head.primaryText)
                        .font(AppTypography.dataDisplay)
                        .foregroundColor(head.isPositive ? AppColors.bullish : AppColors.bearish)
                    Text("\(head.secondaryText) · #1 today")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)

                case .volume:
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(head.primaryText)
                            .font(AppTypography.dataDisplay)
                            .foregroundColor(AppColors.textPrimary)
                        Text(head.secondaryText)
                            .font(AppTypography.dataMedium)
                            .foregroundColor(head.isPositive ? AppColors.bullish : AppColors.bearish)
                    }
                    Text("avg daily volume · spiking")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)

                case .shorts:
                    Text(head.primaryText)
                        .font(AppTypography.dataDisplay)
                        .foregroundColor(scanner.accent)
                    Text("of float sold short")
                        .font(AppTypography.labelSmall)
                        .foregroundColor(AppColors.textMuted)
                }
            }
        } else {
            // No rows on this side (e.g. the Losers tab on a day with no quality
            // losers). Show a calm empty state instead of a blank hero.
            Text(emptyStateText)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
        }
    }

    private var emptyStateText: String {
        switch scanner.kind {
        case .movers:
            return moversMode == .gainers
                ? "No notable gainers right now."
                : "No notable losers right now."
        case .volume:
            return "No unusual volume right now."
        case .shorts:
            return "No short-interest data right now."
        }
    }

    // MARK: - Expand button

    private var expandButton: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.25)) { isExpanded.toggle() }
        } label: {
            HStack(spacing: 6) {
                Text(scanner.expandCTA)
                    .font(AppTypography.labelSmallEmphasis)
                Image(systemName: "chevron.down")
                    .font(.system(size: 12, weight: .semibold))
                    .rotationEffect(.degrees(isExpanded ? 180 : 0))
            }
            .foregroundColor(AppColors.primaryBlue)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 9)
            .background(AppColors.surfaceRecessed)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    // MARK: - Leaderboard (expanded, in place)

    private var leaderboard: some View {
        VStack(spacing: 0) {
            ForEach(list) { entry in
                ScannerLeaderboardRow(entry: entry, kind: scanner.kind) {
                    onEntryTap?(entry)
                }
            }
        }
        .padding(.top, 6)
    }
}

#Preview {
    ScannerCardPreviewHost()
}

/// Gives each card a real expand binding (one open at a time) for the preview.
private struct ScannerCardPreviewHost: View {
    @State private var expandedID: DailyScanner.ID?
    private let cards = [
        MockHomeRepository.movers,
        MockHomeRepository.heavyTraffic,
        MockHomeRepository.skepticalMoney,
    ]
    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                ForEach(cards) { card in
                    ScannerCard(
                        scanner: card,
                        isExpanded: Binding(
                            get: { expandedID == card.id },
                            set: { expandedID = $0 ? card.id : nil }
                        )
                    )
                }
            }
            .padding()
        }
        .background(AppColors.background)
    }
}
