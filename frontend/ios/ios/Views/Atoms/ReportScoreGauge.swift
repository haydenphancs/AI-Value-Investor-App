//
//  ReportScoreGauge.swift
//  ios
//
//  Atom: Circular score display for the report quality rating (e.g. 82)
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
            case .regular: return AppTypography.labelSmall
            }
        }

        var spacing: CGFloat {
            switch self {
            case .small: return AppSpacing.xxs
            case .regular: return AppSpacing.sm
            }
        }
    }

    /// Integer actually displayed — rounded ONCE so the number, the arc color,
    /// and the caller-passed label all key off the same value. Mirrors
    /// `ReportQualityRating.displayScore`.
    private var displayScore: Int { Int(score.rounded()) }

    private var progress: Double {
        guard maxScore > 0 else { return 0 }
        return min(1.0, max(0.0, score / maxScore))
    }

    /// Color comes from `QualityBand` (the SAME source the label uses), keyed
    /// off `displayScore` — so a rounded "50" can never render under a
    /// "Weak"/orange arc.
    private var scoreColor: Color {
        QualityBand.forScore(displayScore).color
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

                // Score text — integer, matching the band that colors the arc.
                Text("\(displayScore)")
                    .font(.system(size: size.scoreFontSize, weight: .bold, design: .rounded))
                    .foregroundColor(AppColors.textPrimary)
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
