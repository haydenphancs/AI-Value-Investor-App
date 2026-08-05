//
//  ShimmerEffect.swift
//  ios
//
//  Atom: Reusable shimmer/skeleton loading effect
//

import SwiftUI

extension View {
    func shimmer() -> some View {
        self.modifier(ShimmerModifier())
    }
}

struct ShimmerModifier: ViewModifier {
    @State private var phase: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .overlay(
                GeometryReader { geometry in
                    // The sweep must be lighter than the skeleton in dark mode
                    // and DARKER than it in light. A hardcoded white sweep is
                    // invisible on a light skeleton, so every loading state in
                    // the app read as frozen. `textPrimary` inverts with the
                    // appearance, so one value covers both.
                    LinearGradient(
                        gradient: Gradient(colors: [
                            Color.clear,
                            AppColors.textPrimary.opacity(0.08),
                            Color.clear
                        ]),
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(width: geometry.size.width * 2)
                    .offset(x: -geometry.size.width + phase * geometry.size.width * 2)
                    .onAppear {
                        withAnimation(
                            Animation.linear(duration: 1.5)
                                .repeatForever(autoreverses: false)
                        ) {
                            phase = 1
                        }
                    }
                }
            )
            .clipped()
    }
}
