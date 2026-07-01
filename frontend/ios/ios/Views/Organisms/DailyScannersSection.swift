//
//  DailyScannersSection.swift
//  ios
//
//  Organism: the "Daily Scanners" swipeable carousel of scanner cards plus a
//  paging indicator. Owns the active-page state and lets the dots scroll the
//  carousel.
//
//  IMPORTANT — why there is NO `.scrollPosition(id:)` here:
//  `.scrollPosition` is documented to keep the focused item in place when the
//  scroll view's content size changes, which it does by WRITING its binding
//  back during layout. When a card expands in place (animated height change),
//  that per-frame size change makes `.scrollPosition` rewrite state mid-layout
//  → re-invalidate layout → … a non-terminating loop that freezes the main
//  thread. So we DON'T use it: the active page is derived READ-ONLY from the
//  scroll offset (a PreferenceKey), and dot-taps scroll via a ScrollViewReader.
//

import SwiftUI

struct DailyScannersSection: View {
    let scanners: [DailyScanner]
    var onEntryTap: ((ScannerEntry) -> Void)? = nil
    /// Which card is expanded (nil = none). Lifted to the Home screen so a tap
    /// ANYWHERE outside the card collapses it; also enforces one-open-at-a-time.
    @Binding var expandedCardID: DailyScanner.ID?

    @State private var activeIndex: Int = 0
    @State private var viewportWidth: CGFloat = 0

    private let cardSpacing: CGFloat = 14
    private let widthFactor: CGFloat = 0.86
    private static let coordSpace = "dailyScannersCarousel"

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Daily Scanners")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)
                Spacer()
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, 12)

            ScrollViewReader { proxy in
                VStack(alignment: .leading, spacing: 0) {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(alignment: .top, spacing: cardSpacing) {
                            ForEach(Array(scanners.enumerated()), id: \.element.id) { index, scanner in
                                ScannerCard(
                                    scanner: scanner,
                                    onEntryTap: onEntryTap,
                                    isExpanded: Binding(
                                        get: { expandedCardID == scanner.id },
                                        set: { expandedCardID = $0 ? scanner.id : nil }
                                    )
                                )
                                    // ~86% of the container width so the next card peeks.
                                    .containerRelativeFrame(.horizontal) { length, _ in length * widthFactor }
                                    // Each card reports its center-X in the carousel's
                                    // coordinate space; the card nearest the viewport
                                    // center is the active page (drives the dots). This
                                    // recomputes every scroll frame — no scroll-offset
                                    // math and no default-value race to freeze it.
                                    .background(
                                        GeometryReader { g in
                                            Color.clear.preference(
                                                key: ScannerActiveCardKey.self,
                                                value: [ActiveCardCandidate(
                                                    index: index,
                                                    midX: g.frame(in: .named(Self.coordSpace)).midX
                                                )]
                                            )
                                        }
                                    )
                                    .id(scanner.id)
                            }
                        }
                        .scrollTargetLayout()
                    }
                    .scrollTargetBehavior(.viewAligned)
                    .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)
                    .coordinateSpace(name: Self.coordSpace)
                    .background(
                        GeometryReader { g in
                            Color.clear
                                .onAppear { viewportWidth = g.size.width }
                                .onChange(of: g.size.width) { _, w in viewportWidth = w }
                        }
                    )
                    .onPreferenceChange(ScannerActiveCardKey.self) { candidates in
                        guard viewportWidth > 0, !candidates.isEmpty else { return }
                        // The card whose center is nearest the viewport center is
                        // the active page.
                        let center = viewportWidth / 2
                        if let best = candidates.min(by: {
                            abs($0.midX - center) < abs($1.midX - center)
                        }) {
                            activeIndex = min(max(best.index, 0), max(scanners.count - 1, 0))
                        }
                    }

                    CarouselPageDots(count: scanners.count, activeIndex: activeIndex) { index in
                        guard scanners.indices.contains(index) else { return }
                        withAnimation(.easeInOut(duration: 0.3)) {
                            proxy.scrollTo(scanners[index].id, anchor: .leading)
                        }
                    }
                    .padding(.top, 13)
                }
            }
        }
        .onChange(of: scanners.count) { _, count in
            activeIndex = min(activeIndex, max(count - 1, 0))
        }
    }
}

/// One card's index + its center-X in the carousel's coordinate space. The
/// section picks the candidate nearest the viewport center as the active page —
/// a READ-ONLY derivation (no `.scrollPosition` binding, which would deadlock
/// layout when a card expands in place).
private struct ActiveCardCandidate: Equatable {
    let index: Int
    let midX: CGFloat
}

private struct ScannerActiveCardKey: PreferenceKey {
    static var defaultValue: [ActiveCardCandidate] = []
    static func reduce(value: inout [ActiveCardCandidate], nextValue: () -> [ActiveCardCandidate]) {
        value.append(contentsOf: nextValue())
    }
}

#Preview {
    DailyScannersSectionPreviewHost()
}

/// Stateful host so the carousel preview can actually expand/collapse a card.
private struct DailyScannersSectionPreviewHost: View {
    @State private var expandedID: DailyScanner.ID?
    var body: some View {
        DailyScannersSection(
            scanners: [
                MockHomeRepository.movers,
                MockHomeRepository.heavyTraffic,
                MockHomeRepository.skepticalMoney
            ],
            expandedCardID: $expandedID
        )
        .padding(.vertical)
        .background(AppColors.background)
    }
}
