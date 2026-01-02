//
//  ReadTimeLabel.swift
//  ios
//
//  Atom: Label showing estimated reading/learning time
//

import SwiftUI

struct ReadTimeLabel: View {
    let minutes: Int
    var showIcon: Bool = true
    var style: Style = .compact

    enum Style {
        case compact
        case expanded
    }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if showIcon {
                Image(systemName: "clock")
                    .font(.system(size: 10, weight: .medium))
            }

            Text(formattedTime)
                .font(AppTypography.caption)
        }
        .foregroundColor(AppColors.textSecondary)
    }

    private var formattedTime: String {
        switch style {
        case .compact:
            return "\(minutes) min read"
        case .expanded:
            return "\(minutes) minutes"
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ReadTimeLabel(minutes: 8)
        ReadTimeLabel(minutes: 12, style: .expanded)
        ReadTimeLabel(minutes: 4, showIcon: false)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
