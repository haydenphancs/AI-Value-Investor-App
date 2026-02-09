//
//  PriceActionSparkline.swift
//  ios
//
//  Molecule: Lightweight sparkline (Path) with gradient fill and an optional
//  event dot marking when a catalyst occurred.
//

import SwiftUI

struct PriceActionSparkline: View {
    let data: [Double]
    let eventIndex: Int?
    let trendColor: Color

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            let minVal = data.min() ?? 0
            let maxVal = data.max() ?? 1
            let range = max(maxVal - minVal, 0.01)

            // Gradient fill under the line
            sparklineFill(w: w, h: h, minVal: minVal, range: range)

            // Line stroke
            sparklinePath(w: w, h: h, minVal: minVal, range: range)
                .stroke(trendColor, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))

            // Event dot
            if let idx = eventIndex, idx >= 0, idx < data.count {
                let pos = point(for: idx, w: w, h: h, minVal: minVal, range: range)
                Circle()
                    .fill(Color.white)
                    .frame(width: 8, height: 8)
                    .shadow(color: trendColor.opacity(0.8), radius: 6)
                    .position(x: pos.x, y: pos.y)
            }
        }
        .frame(height: 60)
    }

    // MARK: - Helpers

    private func sparklinePath(w: CGFloat, h: CGFloat, minVal: Double, range: Double) -> Path {
        Path { path in
            for (i, val) in data.enumerated() {
                let pt = point(for: i, w: w, h: h, minVal: minVal, range: range)
                if i == 0 { path.move(to: pt) }
                else { path.addLine(to: pt) }
            }
        }
    }

    private func sparklineFill(w: CGFloat, h: CGFloat, minVal: Double, range: Double) -> some View {
        Path { path in
            for (i, val) in data.enumerated() {
                let pt = point(for: i, w: w, h: h, minVal: minVal, range: range)
                if i == 0 { path.move(to: pt) }
                else { path.addLine(to: pt) }
            }
            // Close to bottom-right → bottom-left
            path.addLine(to: CGPoint(x: w, y: h))
            path.addLine(to: CGPoint(x: 0, y: h))
            path.closeSubpath()
        }
        .fill(
            LinearGradient(
                colors: [trendColor.opacity(0.25), trendColor.opacity(0.0)],
                startPoint: .top,
                endPoint: .bottom
            )
        )
    }

    private func point(for index: Int, w: CGFloat, h: CGFloat, minVal: Double, range: Double) -> CGPoint {
        let x = w * CGFloat(index) / CGFloat(max(data.count - 1, 1))
        let y = h - (h * CGFloat((data[index] - minVal) / range))
        return CGPoint(x: x, y: y)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        // Event-driven decline
        PriceActionSparkline(
            data: [163.2, 162.8, 164.1, 159.2, 155.3, 150.1, 148.6, 145.2, 143.8, 142.8],
            eventIndex: 2,
            trendColor: AppColors.bearish
        )

        // Rally, no event
        PriceActionSparkline(
            data: [100, 102, 105, 108, 112, 118, 122],
            eventIndex: nil,
            trendColor: AppColors.bullish
        )
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
