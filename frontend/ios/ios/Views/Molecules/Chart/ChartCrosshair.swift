//
//  ChartCrosshair.swift
//  ios
//
//  Interactive crosshair overlay — drag on chart to see exact date+price.
//  Publishes the selected index so the price header can update.
//

import Combine
import SwiftUI

/// State object shared between the chart and price header
class CrosshairState: ObservableObject {
    @Published var selectedIndex: Int? = nil
    @Published var isDragging: Bool = false
}

/// Transparent gesture overlay that captures drag on the chart area
struct ChartCrosshairGesture: View {
    let pricePoints: [StockPricePoint]
    let selectedRange: ChartTimeRange
    @ObservedObject var crosshairState: CrosshairState

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size

            ZStack {
                // Invisible touch surface
                Color.clear
                    .contentShape(Rectangle())
                    .gesture(
                        DragGesture(minimumDistance: 0)
                            .onChanged { value in
                                let count = pricePoints.count
                                guard count > 1 else { return }
                                let x = min(max(value.location.x, 0), size.width)
                                let idx = Int(round(x / size.width * CGFloat(count - 1)))
                                let clampedIdx = min(max(idx, 0), count - 1)
                                if crosshairState.selectedIndex != clampedIdx {
                                    crosshairState.selectedIndex = clampedIdx
                                }
                                if !crosshairState.isDragging {
                                    crosshairState.isDragging = true
                                }
                            }
                            .onEnded { _ in
                                crosshairState.isDragging = false
                                crosshairState.selectedIndex = nil
                            }
                    )

                // Draw crosshair lines + tooltip when active
                if let idx = crosshairState.selectedIndex, crosshairState.isDragging,
                   idx >= 0, idx < pricePoints.count {
                    let count = pricePoints.count
                    let point = pricePoints[idx]
                    let x = CGFloat(idx) * size.width / CGFloat(max(1, count - 1))

                    // Compute y from close price
                    let closes = pricePoints.map { $0.close }
                    let minVal = closes.min() ?? 0
                    let maxVal = closes.max() ?? 1
                    let range = max(maxVal - minVal, Double.ulpOfOne)
                    let normalized = (point.close - minVal) / range
                    let y = size.height - (CGFloat(normalized) * size.height * 0.9) - size.height * 0.05

                    // Vertical line
                    Path { path in
                        path.move(to: CGPoint(x: x, y: 0))
                        path.addLine(to: CGPoint(x: x, y: size.height))
                    }
                    .stroke(AppColors.textMuted.opacity(0.6), style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))

                    // Horizontal line
                    Path { path in
                        path.move(to: CGPoint(x: 0, y: y))
                        path.addLine(to: CGPoint(x: size.width, y: y))
                    }
                    .stroke(AppColors.textMuted.opacity(0.4), style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))

                    // Price dot
                    Circle()
                        .fill(AppColors.textPrimary)
                        .frame(width: 7, height: 7)
                        .position(x: x, y: y)

                    // Tooltip
                    let tooltipText = crosshairTooltip(point: point)
                    let tooltipX = clampTooltipX(x: x, width: size.width)

                    Text(tooltipText)
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundColor(AppColors.textPrimary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 3)
                        .background(
                            RoundedRectangle(cornerRadius: 4)
                                .fill(AppColors.cardBackground.opacity(0.95))
                        )
                        .position(x: tooltipX, y: -2)
                }
            }
        }
    }

    private func crosshairTooltip(point: StockPricePoint) -> String {
        let dateStr = selectedRange.formatDateForCrosshair(point.date)
        let priceStr: String
        if point.close >= 1 {
            priceStr = String(format: "$%.2f", point.close)
        } else {
            priceStr = String(format: "$%.6f", point.close)
        }
        return "\(dateStr)  \(priceStr)"
    }

    private func clampTooltipX(x: CGFloat, width: CGFloat) -> CGFloat {
        let halfTooltip: CGFloat = 80
        return min(max(x, halfTooltip), width - halfTooltip)
    }
}
