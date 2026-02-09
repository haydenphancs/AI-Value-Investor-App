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
    var size: GaugeSize = .regular

    enum GaugeSize {
        case small
        case regular

        var circleSize: CGFloat {
            switch self {
            case .small: return 52
            case .regular: return 72
            }
        }

        var lineWidth: CGFloat {
            switch self {
            case .small: return 4
            case .regular: return 6
            }
        }

        var scoreFontSize: CGFloat {
            switch self {
            case .small: return 16
            case .regular: return 24
            }
        }

        var maxScoreFontSize: CGFloat {
            switch self {
            case .small: return 10
            case .regular: return 12
            }
        }

        var labelFont: Font {
            switch self {
            case .small: return AppTypography.caption
            case .regular: return AppTypography.footnote
            }
        }

        var spacing: CGFloat {
            switch self {
            case .small: return AppSpacing.xxs
            case .regular: return AppSpacing.sm
            }
        }
    }

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
        VStack(spacing: size.spacing) {
            ZStack {
                // Background circle
                Circle()
                    .stroke(AppColors.cardBackgroundLight, lineWidth: size.lineWidth)
                    .frame(width: size.circleSize, height: size.circleSize)

                // Progress arc
                Circle()
                    .trim(from: 0, to: progress)
                    .stroke(
                        scoreColor,
                        style: StrokeStyle(lineWidth: size.lineWidth, lineCap: .round)
                    )
                    .frame(width: size.circleSize, height: size.circleSize)
                    .rotationEffect(.degrees(-90))

                // Score text
                VStack(spacing: 0) {
                    Text(String(format: "%.1f", score))
                        .font(.system(size: size.scoreFontSize, weight: .bold, design: .rounded))
                        .foregroundColor(AppColors.textPrimary)
                    Text("/ \(Int(maxScore))")
                        .font(.system(size: size.maxScoreFontSize))
                        .foregroundColor(AppColors.textSecondary)
                }
            }

            if !label.isEmpty {
                Text(label)
                    .font(size.labelFont)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
            }
        }
    }
}

#Preview {
    ReportScoreGauge(score: 4.1, maxScore: 5.0, label: "Good quality business")
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
