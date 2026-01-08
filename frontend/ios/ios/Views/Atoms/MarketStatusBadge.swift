//
//  MarketStatusBadge.swift
//  ios
//
//  Atom: Market status indicator badge (Open, Closed, Pre-Market, After Hours)
//

import SwiftUI

struct MarketStatusBadge: View {
    let status: MarketStatus

    private var statusColor: Color {
        switch status {
        case .open:
            return AppColors.bullish
        case .closed:
            return AppColors.textMuted
        case .preMarket, .afterHours:
            return AppColors.neutral
        }
    }

    private var showPulse: Bool {
        if case .open = status {
            return true
        }
        return false
    }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            // Status indicator dot
            Circle()
                .fill(statusColor)
                .frame(width: 6, height: 6)

            // Status text
            Text(status.displayText)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        MarketStatusBadge(status: .open)
        MarketStatusBadge(status: .closed(
            date: Date(),
            time: "4:00 PM",
            timezone: "EST"
        ))
        MarketStatusBadge(status: .preMarket)
        MarketStatusBadge(status: .afterHours)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
