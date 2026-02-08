//
//  ReportThreatLevelBar.swift
//  ios
//
//  Molecule: DEFCON-style horizontal threat level indicator.
//  Shows all 5 levels with the active one highlighted and pulsing.
//

import SwiftUI

struct ReportThreatLevelBar: View {
    let level: ThreatLevel

    private let allLevels = ThreatLevel.allCases

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // THREAT LEVEL header
            HStack(spacing: AppSpacing.sm) {
                Text("THREAT LEVEL")
                    .font(AppTypography.captionBold)
                    .foregroundColor(AppColors.textMuted)
                    .tracking(1.5)
            }

            // Level segments
            HStack(spacing: 3) {
                ForEach(allLevels, id: \.rawValue) { lvl in
                    VStack(spacing: AppSpacing.xs) {
                        RoundedRectangle(cornerRadius: 2)
                            .fill(segmentFill(for: lvl))
                            .frame(height: 8)
                            .overlay(
                                RoundedRectangle(cornerRadius: 2)
                                    .stroke(
                                        lvl == level ? level.color : Color.clear,
                                        lineWidth: 1
                                    )
                            )

                        Text(lvl.rawValue)
                            .font(.system(size: 8, weight: lvl == level ? .bold : .regular))
                            .foregroundColor(lvl == level ? level.color : AppColors.textMuted)
                    }
                }
            }
        }
    }

    private func segmentFill(for lvl: ThreatLevel) -> Color {
        if lvl.numericLevel <= level.numericLevel {
            return lvl.numericLevel == level.numericLevel ? level.color : level.color.opacity(0.5)
        }
        return AppColors.cardBackgroundLight
    }
}

#Preview {
    VStack(spacing: AppSpacing.xxl) {
        ReportThreatLevelBar(level: .elevated)
        ReportThreatLevelBar(level: .severe)
        ReportThreatLevelBar(level: .low)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
