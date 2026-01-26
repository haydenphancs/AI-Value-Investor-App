//
//  AIVoiceOrb.swift
//  ios
//
//  Atom: Animated AI voice orb similar to Siri visualization
//  Displays a glowing, animated sphere that indicates AI is speaking
//

import SwiftUI

struct AIVoiceOrb: View {
    let isAnimating: Bool
    var size: CGFloat = 120

    @State private var phase: CGFloat = 0
    @State private var innerPhase: CGFloat = 0
    @State private var glowOpacity: Double = 0.6

    // Gradient colors for the orb
    private let primaryGradient = LinearGradient(
        colors: [
            Color(hex: "06B6D4"),   // Cyan
            Color(hex: "3B82F6"),   // Blue
            Color(hex: "8B5CF6"),   // Purple
            Color(hex: "EC4899")    // Pink
        ],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    private let innerGradient = LinearGradient(
        colors: [
            Color(hex: "06B6D4").opacity(0.8),
            Color(hex: "3B82F6").opacity(0.6),
            Color(hex: "EC4899").opacity(0.4)
        ],
        startPoint: .top,
        endPoint: .bottom
    )

    var body: some View {
        ZStack {
            // Outer glow
            Circle()
                .fill(
                    RadialGradient(
                        colors: [
                            Color(hex: "06B6D4").opacity(0.3),
                            Color(hex: "3B82F6").opacity(0.1),
                            Color.clear
                        ],
                        center: .center,
                        startRadius: size * 0.3,
                        endRadius: size * 0.8
                    )
                )
                .frame(width: size * 1.6, height: size * 1.6)
                .opacity(glowOpacity)

            // Main orb background
            Circle()
                .fill(
                    RadialGradient(
                        colors: [
                            Color(hex: "1E3A5F"),
                            Color(hex: "0F172A")
                        ],
                        center: .center,
                        startRadius: 0,
                        endRadius: size * 0.5
                    )
                )
                .frame(width: size, height: size)

            // Wave layers
            ForEach(0..<3, id: \.self) { index in
                WaveLayer(
                    phase: phase + CGFloat(index) * 0.3,
                    amplitude: isAnimating ? 8 + CGFloat(index) * 2 : 3,
                    frequency: 3 - CGFloat(index) * 0.5
                )
                .stroke(
                    LinearGradient(
                        colors: [
                            Color(hex: "06B6D4").opacity(0.6 - Double(index) * 0.15),
                            Color(hex: "EC4899").opacity(0.4 - Double(index) * 0.1)
                        ],
                        startPoint: .leading,
                        endPoint: .trailing
                    ),
                    lineWidth: 2 - CGFloat(index) * 0.5
                )
                .frame(width: size * 0.7, height: size * 0.3)
                .offset(y: CGFloat(index - 1) * 8)
            }

            // Inner highlight
            Circle()
                .fill(
                    RadialGradient(
                        colors: [
                            Color.white.opacity(0.15),
                            Color.clear
                        ],
                        center: UnitPoint(x: 0.3, y: 0.3),
                        startRadius: 0,
                        endRadius: size * 0.4
                    )
                )
                .frame(width: size * 0.9, height: size * 0.9)

            // Rim gradient
            Circle()
                .strokeBorder(
                    AngularGradient(
                        colors: [
                            Color(hex: "06B6D4").opacity(0.5),
                            Color(hex: "3B82F6").opacity(0.3),
                            Color(hex: "8B5CF6").opacity(0.4),
                            Color(hex: "EC4899").opacity(0.5),
                            Color(hex: "06B6D4").opacity(0.5)
                        ],
                        center: .center,
                        startAngle: .degrees(phase * 50),
                        endAngle: .degrees(phase * 50 + 360.0)
                    ),
                    lineWidth: 2
                )
                .frame(width: size, height: size)
        }
        .onAppear {
            startAnimations()
        }
        .onChange(of: isAnimating) { _, newValue in
            if newValue {
                startAnimations()
            }
        }
    }

    private func startAnimations() {
        // Wave phase animation
        withAnimation(
            .linear(duration: 2)
            .repeatForever(autoreverses: false)
        ) {
            phase = .pi * 2
        }

        // Glow pulse animation
        withAnimation(
            .easeInOut(duration: 1.5)
            .repeatForever(autoreverses: true)
        ) {
            glowOpacity = isAnimating ? 0.8 : 0.4
        }
    }
}

// MARK: - Wave Layer Shape

struct WaveLayer: Shape {
    var phase: CGFloat
    var amplitude: CGFloat
    var frequency: CGFloat

    var animatableData: CGFloat {
        get { phase }
        set { phase = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let midY = rect.midY
        let width = rect.width

        path.move(to: CGPoint(x: 0, y: midY))

        for x in stride(from: 0, through: width, by: 1) {
            let relativeX = x / width
            let sine = sin((relativeX * frequency * .pi * 2) + phase)
            let y = midY + sine * amplitude
            path.addLine(to: CGPoint(x: x, y: y))
        }

        return path
    }
}

#Preview {
    VStack(spacing: 40) {
        AIVoiceOrb(isAnimating: true, size: 120)
        AIVoiceOrb(isAnimating: false, size: 80)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
