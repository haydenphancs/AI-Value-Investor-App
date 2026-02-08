//
//  ReportScoreGauge.swift
//  ios
//
//  Atom: Circular score display for the report quality rating (e.g. 4.1/5)
//

import SwiftUI

struct ReportScoreGauge: View {
    let score: Double
    let maxScore: Double
    let label: String

    private var progress: Double {
        score / maxScore
    }

    private var scoreColor: Color {
        switch score {
        case 4.5...5.0: return AppColors.bullish
        case 3.5..<4.5: return AppColors.bullish
        case 2.5..<3.5: return AppColors.neutral
        default: return AppColors.bearish
        }
    }

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            ZStack {
                // Background circle
                Circle()
                    .stroke(AppColors.cardBackgroundLight, lineWidth: 6)
                    .frame(width: 72, height: 72)

                // Progress arc
                Circle()
                    .trim(from: 0, to: progress)
                    .stroke(
                        scoreColor,
                        style: StrokeStyle(lineWidth: 6, lineCap: .round)
                    )
                    .frame(width: 72, height: 72)
                    .rotationEffect(.degrees(-90))

                // Score text
                VStack(spacing: 0) {
                    Text(String(format: "%.1f", score))
                        .font(.system(size: 24, weight: .bold, design: .rounded))
                        .foregroundColor(AppColors.textPrimary)
                    Text("/ \(Int(maxScore))")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }

            Text(label)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

#Preview {
    ReportScoreGauge(score: 4.1, maxScore: 5.0, label: "Good quality business")
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
