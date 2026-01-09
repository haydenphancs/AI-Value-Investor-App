//
//  PriceTargetRange.swift
//  ios
//
//  Price target range bar with current price indicator
//

import SwiftUI

struct PriceTargetRange: View {
    let priceTarget: AnalystPriceTarget

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Labels row
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Low")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text(priceTarget.formattedLow)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bearish)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text("High")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text(priceTarget.formattedHigh)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.bullish)
                }
            }

            // Price range bar
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    // Gradient background bar
                    LinearGradient(
                        colors: [AppColors.bearish, AppColors.neutral, AppColors.bullish],
                        startPoint: .leading,
                        endPoint: .trailing
                    )
                    .frame(height: 8)
                    .cornerRadius(4)

                    // Current price indicator
                    let position = priceTarget.currentPricePosition
                    let indicatorX = geometry.size.width * position

                    VStack(spacing: AppSpacing.xxs) {
                        // Current price label
                        Text("Current: \(priceTarget.formattedCurrent)")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                            .offset(x: clampedOffset(for: indicatorX, in: geometry.size.width, labelWidth: 90))

                        // Triangle indicator
                        Triangle()
                            .fill(AppColors.textPrimary)
                            .frame(width: 10, height: 6)
                            .offset(y: -2)
                    }
                    .position(x: indicatorX, y: -12)
                    
                    // Average price indicator
                    let averagePosition = priceTarget.averagePricePosition
                    let averageX = geometry.size.width * averagePosition

                    VStack(spacing: AppSpacing.xxs) {
                        // Triangle indicator (pointing down)
                        Triangle()
                            .fill(AppColors.textMuted)
                            .frame(width: 10, height: 6)
                            .rotationEffect(.degrees(180))
                            .offset(y: 2)
                        
                        // Average price label
                        Text("Average: \(priceTarget.formattedAverage)")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .offset(x: clampedOffset(for: averageX, in: geometry.size.width, labelWidth: 90))
                    }
                    .position(x: averageX, y: 20)
                }
            }
            .frame(height: 8)
            .padding(.top, AppSpacing.xxl)
            .padding(.bottom, AppSpacing.lg)
        }
    }

    private func clampedOffset(for x: CGFloat, in width: CGFloat, labelWidth: CGFloat) -> CGFloat {
        let halfLabel = labelWidth / 2
        if x < halfLabel {
            return halfLabel - x
        } else if x > width - halfLabel {
            return (width - halfLabel) - x
        }
        return 0
    }
}

// MARK: - Triangle Shape
struct Triangle: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.midX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.closeSubpath()
        return path
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        PriceTargetRange(priceTarget: AnalystPriceTarget.sampleData)
            .padding()
    }
}
