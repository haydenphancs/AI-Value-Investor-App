//
//  SentimentPercentageBadge.swift
//  ios
//
//  Atom: Sentiment badge showing Bullish/Bearish percentage
//

import SwiftUI

struct SentimentPercentageBadge: View {
    let sentiment: SentimentAnalysis.SentimentType
    let percentage: Int

    var body: some View {
        Text("\(sentiment.rawValue) \(percentage)%")
            .font(AppTypography.calloutBold)
            .foregroundColor(sentiment.color)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        SentimentPercentageBadge(sentiment: .bullish, percentage: 68)
        SentimentPercentageBadge(sentiment: .bearish, percentage: 45)
        SentimentPercentageBadge(sentiment: .neutral, percentage: 50)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
