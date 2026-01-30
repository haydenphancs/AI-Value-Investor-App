//
//  MarqueeText.swift
//  ios
//
//  Atom: Auto-scrolling marquee text for long titles
//  Used when text overflows its container
//

import SwiftUI

struct MarqueeText: View {
    let text: String
    var font: Font = AppTypography.bodyBold
    var color: Color = AppColors.textPrimary
    var speed: Double = 30 // points per second
    var delayBeforeScroll: Double = 2.0
    var spacing: CGFloat = 40

    @State private var textWidth: CGFloat = 0
    @State private var containerWidth: CGFloat = 0
    @State private var offset: CGFloat = 0
    @State private var isAnimating: Bool = false

    private var needsScrolling: Bool {
        textWidth > containerWidth
    }

    var body: some View {
        GeometryReader { geometry in
            let containerW = geometry.size.width

            ZStack(alignment: .leading) {
                if needsScrolling {
                    // Scrolling text with duplicate for seamless loop
                    HStack(spacing: spacing) {
                        textView
                        textView
                    }
                    .offset(x: offset)
                    .onAppear {
                        containerWidth = containerW
                        startScrollingIfNeeded()
                    }
                    .onChange(of: text) { _, _ in
                        resetAnimation()
                    }
                } else {
                    // Static text when it fits
                    textView
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .clipped()
            .onAppear {
                containerWidth = containerW
            }
        }
        .frame(height: measureTextHeight())
    }

    private var textView: some View {
        Text(text)
            .font(font)
            .foregroundColor(color)
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
            .background(
                GeometryReader { proxy in
                    Color.clear
                        .onAppear {
                            textWidth = proxy.size.width
                        }
                }
            )
    }

    private func measureTextHeight() -> CGFloat {
        // Approximate height based on font
        return 20
    }

    private func startScrollingIfNeeded() {
        guard needsScrolling, !isAnimating else { return }
        isAnimating = true

        // Initial delay before starting scroll
        DispatchQueue.main.asyncAfter(deadline: .now() + delayBeforeScroll) {
            animateScroll()
        }
    }

    private func animateScroll() {
        guard needsScrolling else { return }

        let totalWidth = textWidth + spacing
        let animationDuration = totalWidth / speed

        withAnimation(.linear(duration: animationDuration)) {
            offset = -totalWidth
        }

        // Reset and repeat
        DispatchQueue.main.asyncAfter(deadline: .now() + animationDuration) {
            offset = 0
            // Small pause at reset point
            DispatchQueue.main.asyncAfter(deadline: .now() + delayBeforeScroll) {
                if needsScrolling {
                    animateScroll()
                }
            }
        }
    }

    private func resetAnimation() {
        isAnimating = false
        offset = 0
        textWidth = 0

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            startScrollingIfNeeded()
        }
    }
}

// MARK: - Preview
#Preview {
    VStack(spacing: AppSpacing.xl) {
        // Short text (no scroll)
        MarqueeText(text: "Short Title")
            .frame(width: 200)
            .background(AppColors.cardBackground)

        // Long text (scrolls)
        MarqueeText(text: "The Future of Digital Finance: Exploring the Intersection of Fintech Innovation")
            .frame(width: 200)
            .background(AppColors.cardBackground)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
