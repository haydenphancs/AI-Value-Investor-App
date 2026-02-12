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
        case 90...100: return AppColors.bullish          // Excellent Quality Business
        case 75..<90: return AppColors.bullish           // Strong Quality Business
        case 50..<75: return AppColors.neutral           // Fair Quality Business
        case 30..<50: return AppColors.alertOrange       // Weak Quality Business
        default: return AppColors.bearish                // Distressed Quality Business
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
                    Text(String(format: "%.0f", score))
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
    ReportScoreGauge(score: 82, maxScore: 100, label: "Strong Quality Business")
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
