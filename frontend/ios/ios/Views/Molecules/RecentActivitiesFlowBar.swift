//
//  RecentActivitiesFlowBar.swift
//  ios
//
//  Molecule: Horizontal bar showing In Flow vs Out Flow
//  Green portion represents buying, red portion represents selling
//

import SwiftUI

struct RecentActivitiesFlowBar: View {
    let inFlowPercent: Double  // 0.0 to 1.0
    let formattedInFlow: String
    let formattedOutFlow: String

    private let barHeight: CGFloat = 36
    private let cornerRadius: CGFloat = 8

    var body: some View {
        GeometryReader { geometry in
            HStack(spacing: 0) {
                // In Flow (Green)
                ZStack(alignment: .leading) {
                    Rectangle()
                        .fill(AppColors.bullish)

                    Text(formattedInFlow)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(.white)
                        .padding(.leading, AppSpacing.md)
                }
                .frame(width: geometry.size.width * inFlowPercent)

                // Out Flow (Red)
                ZStack(alignment: .trailing) {
                    Rectangle()
                        .fill(AppColors.bearish)

                    Text(formattedOutFlow)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(.white)
                        .padding(.trailing, AppSpacing.md)
                }
                .frame(width: geometry.size.width * (1 - inFlowPercent))
            }
            .frame(height: barHeight)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
        }
        .frame(height: barHeight)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            RecentActivitiesFlowBar(
                inFlowPercent: 0.54,
                formattedInFlow: "$2.1B",
                formattedOutFlow: "$1.8B"
            )

            RecentActivitiesFlowBar(
                inFlowPercent: 0.7,
                formattedInFlow: "$3.5B",
                formattedOutFlow: "$1.5B"
            )

            RecentActivitiesFlowBar(
                inFlowPercent: 0.3,
                formattedInFlow: "$0.8B",
                formattedOutFlow: "$1.9B"
            )
        }
        .padding()
    }
}
