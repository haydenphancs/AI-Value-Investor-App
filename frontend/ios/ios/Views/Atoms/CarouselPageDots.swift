//
//  CarouselPageDots.swift
//  ios
//
//  Atom: a paging indicator for horizontal carousels — the active page is a wide
//  blue pill, the rest are small grey dots. Optionally tappable to jump pages.
//  Shared by the Home "Daily Scanners" and "Trending Themes" carousels.
//

import SwiftUI

struct CarouselPageDots: View {
    let count: Int
    let activeIndex: Int
    /// When set, each dot is a button that requests a jump to that page.
    var onSelect: ((Int) -> Void)? = nil

    var body: some View {
        HStack(spacing: 6) {
            ForEach(0..<max(count, 0), id: \.self) { index in
                if let onSelect {
                    Button { onSelect(index) } label: { dot(isActive: index == activeIndex) }
                        .buttonStyle(.plain)
                } else {
                    dot(isActive: index == activeIndex)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .animation(.easeInOut(duration: 0.2), value: activeIndex)
    }

    private func dot(isActive: Bool) -> some View {
        Capsule()
            .fill(isActive ? AppColors.primaryBlue : Color(hex: "39414D"))
            .frame(width: isActive ? 20 : 6, height: 6)
    }
}

#Preview {
    VStack(spacing: 20) {
        CarouselPageDots(count: 3, activeIndex: 0)
        CarouselPageDots(count: 5, activeIndex: 2)
    }
    .padding()
    .background(AppColors.background)
}
