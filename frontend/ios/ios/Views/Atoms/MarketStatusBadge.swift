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
            // The New York date, like the server's clock and label beside it. This formatted
            // the date in the PHONE's zone: a 4:00 PM ET close read "Oct 29, 4:00 PM EDT" in
            // Tokyo, a day the market never closed at that time.
            return MarketStatus.closeLine(date: date, time: time, timezone: timezone)
        default:
            return nil
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        MarketStatusBadge(status: .open)
        // A fixed close inside EDT, so the label is true whatever day the canvas renders.
        MarketStatusBadge(status: .closed(
            date: Calendar(identifier: .gregorian).date(from: DateComponents(
                timeZone: TimeZone(identifier: "America/New_York"),
                year: 2026, month: 10, day: 28, hour: 16
            ))!,
            time: "4:00 PM",
            timezone: "EDT"
        ))
        MarketStatusBadge(status: .preMarket)
        MarketStatusBadge(status: .afterHours)
    }
    .padding()
    .background(AppColors.background)
}
