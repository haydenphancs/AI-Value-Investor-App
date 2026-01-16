//
//  GrowthLegendDot.swift
//  ios
//
//  Atom: Legend indicator dot for growth chart
//

import SwiftUI

enum GrowthLegendDotStyle {
    case filled
    case dashed

    var strokeStyle: StrokeStyle? {
        switch self {
        case .filled:
            return nil
        case .dashed:
            return StrokeStyle(lineWidth: 2, dash: [4, 3])
        }
    }
}

struct GrowthLegendDot: View {
    let color: Color
    let style: GrowthLegendDotStyle
    let size: CGFloat

    init(color: Color, style: GrowthLegendDotStyle = .filled, size: CGFloat = 10) {
        self.color = color
        self.style = style
        self.size = size
    }

    var body: some View {
        Group {
            switch style {
            case .filled:
                Circle()
                    .fill(color)
                    .frame(width: size, height: size)
            case .dashed:
                Circle()
                    .stroke(color, style: style.strokeStyle!)
                    .frame(width: size, height: size)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.xl) {
            GrowthLegendDot(color: AppColors.growthYoYYellow)
            GrowthLegendDot(color: AppColors.growthBarBlue)
            GrowthLegendDot(color: AppColors.growthSectorGray, style: .dashed)
        }
        .padding()
    }
}
