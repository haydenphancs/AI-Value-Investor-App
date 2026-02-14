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
        VStack(alignment: .leading, spacing: 2) {
            // Status text
            Text(statusLabel)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
            
            // Additional info (date/time for closed market)
            if let additionalInfo = statusAdditionalInfo {
                Text(additionalInfo)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }
    
    private var statusLabel: String {
        switch status {
        case .open:
            return "Market Open"
        case .closed:
            return "Market Closed"
        case .preMarket:
            return "Pre-Market"
        case .afterHours:
            return "After Hours"
        }
    }
    
    private var statusAdditionalInfo: String? {
        switch status {
        case .closed(let date, let time, let timezone):
            let formatter = DateFormatter()
            formatter.dateFormat = "MMM d"
            return "\(formatter.string(from: date)), \(time) \(timezone)"
        default:
            return nil
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
