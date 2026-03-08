//
//  ChartGridLines.swift
//  ios
//
//  Horizontal grid lines for chart background
//

import SwiftUI

struct ChartGridLines: View {
    var lineCount: Int = 4

    var body: some View {
        VStack(spacing: 0) {
            ForEach(0..<lineCount, id: \.self) { index in
                Rectangle()
                    .fill(AppColors.cardBackgroundLight.opacity(0.5))
                    .frame(height: 1)
                if index < lineCount - 1 {
                    Spacer()
                }
            }
        }
    }
}
