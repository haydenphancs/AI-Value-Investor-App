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

    @State private var activeIndex: Int = 0
    @State private var viewportWidth: CGFloat = 0

    private let cardSpacing: CGFloat = 14
    private let widthFactor: CGFloat = 0.86
    private static let coordSpace = "dailyScannersCarousel"

    // Width of one card and the per-page step, used only to map scroll offset →
    // active page (purely cosmetic dot highlighting; never feeds layout).
    private var step: CGFloat {
        let cardWidth = max((viewportWidth - AppSpacing.lg * 2) * widthFactor, 1)
        return cardWidth + cardSpacing
    }

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
                            ForEach(scanners) { scanner in
                                ScannerCard(scanner: scanner, onEntryTap: onEntryTap)
                                    // ~86% of the container width so the next card peeks.
                                    .containerRelativeFrame(.horizontal) { length, _ in length * widthFactor }
                                    .id(scanner.id)
                            }
                        }
                        .scrollTargetLayout()
                        .background(
                            GeometryReader { g in
                                Color.clear.preference(
                                    key: ScannerCarouselOffsetKey.self,
                                    value: g.frame(in: .named(Self.coordSpace)).minX
                                )
                            }
                        )
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
                    .onPreferenceChange(ScannerCarouselOffsetKey.self) { minX in
                        guard step > 1, !scanners.isEmpty else { return }
                        // Content rests at x = leading inset on page 0; each page
                        // shifts it left by `step`.
                        let idx = Int(((AppSpacing.lg - minX) / step).rounded())
                        activeIndex = min(max(idx, 0), scanners.count - 1)
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

/// Read-only carousel scroll offset (the content's leading edge in the scroll
/// view's coordinate space). Used to highlight the active page dot WITHOUT a
/// `.scrollPosition` binding (which would deadlock layout on card expand).
private struct ScannerCarouselOffsetKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}

#Preview {
    DailyScannersSection(
        scanners: [
            MockHomeRepository.movers,
            MockHomeRepository.heavyTraffic,
            MockHomeRepository.skepticalMoney
        ]
    )
    .padding(.vertical)
    .background(AppColors.background)
}
