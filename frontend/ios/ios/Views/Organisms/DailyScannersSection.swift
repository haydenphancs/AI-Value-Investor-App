//
//  DailyScannersSection.swift
//  ios
//
//  Organism: the "Daily Scanners" swipeable carousel of scanner cards plus a
//  paging indicator. Owns the active-page state and lets the dots scroll the
//  carousel.
//

import SwiftUI

struct DailyScannersSection: View {
    let scanners: [DailyScanner]
    var onEntryTap: ((ScannerEntry) -> Void)? = nil

    @State private var activeId: DailyScanner.ID?

    private var activeIndex: Int {
        guard let activeId,
              let idx = scanners.firstIndex(where: { $0.id == activeId }) else { return 0 }
        return idx
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

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 14) {
                    ForEach(scanners) { scanner in
                        ScannerCard(scanner: scanner, onEntryTap: onEntryTap)
                            // ~86% of the container width, so the next card peeks. Closure
                            // form keeps the width independent of the HStack's 14pt gap.
                            .containerRelativeFrame(.horizontal) { length, _ in length * 0.86 }
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.viewAligned)
            .scrollPosition(id: $activeId)
            .contentMargins(.horizontal, AppSpacing.lg, for: .scrollContent)

            CarouselPageDots(count: scanners.count, activeIndex: activeIndex) { index in
                withAnimation(.easeInOut(duration: 0.3)) { activeId = scanners[index].id }
            }
            .padding(.top, 13)
        }
        .onAppear {
            if activeId == nil { activeId = scanners.first?.id }
        }
    }
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
