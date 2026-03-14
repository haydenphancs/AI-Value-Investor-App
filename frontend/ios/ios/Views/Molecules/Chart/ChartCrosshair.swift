//
//  ChartCrosshair.swift
//  ios
//
//  Interactive crosshair overlay with pinch-to-zoom and pan support.
//  Single-finger drag = crosshair, pinch = zoom, two-finger drag = pan.
//

import Combine
import SwiftUI

/// State object shared between the chart and price header
class CrosshairState: ObservableObject {
    @Published var selectedIndex: Int? = nil
    @Published var isDragging: Bool = false
}

/// Transparent gesture overlay that captures drag, pinch-to-zoom, and pan
struct ChartCrosshairGesture: View {
    let pricePoints: [StockPricePoint]
    let selectedRange: ChartTimeRange
    @ObservedObject var crosshairState: CrosshairState
    @ObservedObject var viewportState: ChartViewportState

    /// Tracks the viewport snapshot at the start of a pinch gesture
    @State private var zoomAnchorStart: Int = 0
    @State private var zoomAnchorEnd: Int = 0

    /// Tracks the viewport snapshot at the start of a pan gesture
    @State private var panAnchorStart: Int = 0
    @State private var panAnchorEnd: Int = 0

    var body: some View {
        GeometryReader { geometry in
            let size = geometry.size

            ZStack {
                // Invisible touch surface with gestures
                Color.clear
                    .contentShape(Rectangle())
                    // Single-finger long-press + drag = crosshair
                    .gesture(
                        LongPressGesture(minimumDuration: 0.15)
                            .sequenced(before: DragGesture(minimumDistance: 0))
                            .onChanged { value in
                                switch value {
                                case .second(true, let drag):
                                    guard let drag = drag else { return }
                                    updateCrosshair(at: drag.location, in: size)
                                default:
                                    break
                                }
                            }
                            .onEnded { _ in
                                crosshairState.isDragging = false
                                crosshairState.selectedIndex = nil
                            }
                    )
                    // Pinch-to-zoom
                    .gesture(
                        MagnificationGesture()
                            .onChanged { scale in
                                if zoomAnchorStart == 0 && zoomAnchorEnd == 0 {
                                    zoomAnchorStart = viewportState.visibleStart
                                    zoomAnchorEnd = viewportState.visibleEnd
                                }
                                applyZoom(scale: scale)
                            }
                            .onEnded { _ in
                                zoomAnchorStart = 0
                                zoomAnchorEnd = 0
                            }
                    )
                    // Two-finger pan (only when zoomed)
                    .simultaneousGesture(
                        DragGesture(minimumDistance: 5)
                            .onChanged { value in
                                // Only pan when not showing crosshair and zoomed in
                                guard !crosshairState.isDragging, viewportState.isZoomed else { return }
                                if panAnchorStart == 0 && panAnchorEnd == 0 {
                                    panAnchorStart = viewportState.visibleStart
                                    panAnchorEnd = viewportState.visibleEnd
                                }
                                applyPan(translation: value.translation.width, in: size)
                            }
                            .onEnded { _ in
                                panAnchorStart = 0
                                panAnchorEnd = 0
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

    // MARK: - Crosshair

    private func updateCrosshair(at location: CGPoint, in size: CGSize) {
        let count = pricePoints.count
        guard count > 1 else { return }
        let x = min(max(location.x, 0), size.width)
        let idx = Int(round(x / size.width * CGFloat(count - 1)))
        let clampedIdx = min(max(idx, 0), count - 1)
        if crosshairState.selectedIndex != clampedIdx {
            crosshairState.selectedIndex = clampedIdx
        }
        if !crosshairState.isDragging {
            crosshairState.isDragging = true
        }
    }

    // MARK: - Zoom

    private func applyZoom(scale: CGFloat) {
        let anchorCount = zoomAnchorEnd - zoomAnchorStart
        guard anchorCount > 0 else { return }

        let center = Double(zoomAnchorStart + zoomAnchorEnd) / 2.0
        let anchorHalf = Double(anchorCount) / 2.0
        let newHalf = anchorHalf / Double(scale)

        let newStart = Int(max(0, center - newHalf))
        let newEnd = Int(min(Double(viewportState.totalCount - 1), center + newHalf))

        // Enforce minimum visible count
        if newEnd - newStart + 1 >= 10 {
            viewportState.visibleStart = newStart
            viewportState.visibleEnd = newEnd
        }
    }

    // MARK: - Pan

    private func applyPan(translation: CGFloat, in size: CGSize) {
        let anchorCount = panAnchorEnd - panAnchorStart
        guard anchorCount > 0, size.width > 0 else { return }

        // Convert pixel translation to data point delta
        let pointsPerPixel = CGFloat(anchorCount) / size.width
        let delta = Int(-translation * pointsPerPixel)

        var newStart = panAnchorStart + delta
        var newEnd = panAnchorEnd + delta

        // Clamp to bounds
        if newStart < 0 {
            newStart = 0
            newEnd = anchorCount
        }
        if newEnd >= viewportState.totalCount {
            newEnd = viewportState.totalCount - 1
            newStart = max(0, newEnd - anchorCount)
        }

        viewportState.visibleStart = newStart
        viewportState.visibleEnd = newEnd
    }

    // MARK: - Tooltip Helpers

    private func crosshairTooltip(point: StockPricePoint) -> String {
        selectedRange.formatDateForCrosshair(point.date)
    }

    private func clampTooltipX(x: CGFloat, width: CGFloat) -> CGFloat {
        let halfTooltip: CGFloat = 80
        return min(max(x, halfTooltip), width - halfTooltip)
    }
}
