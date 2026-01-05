//
//  NewsDetailContent.swift
//  ios
//
//  Organism: Main content area for news detail screen
//

import SwiftUI

struct NewsDetailContent: View {
    let article: NewsArticleDetail
    var onTickerTapped: ((String) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xl) {
            // Headline
            Text(article.headline)
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .lineSpacing(4)

            // Meta Row (Date, Read Time, Sentiment)
            NewsDetailMetaRow(
                date: article.formattedDate,
                readTimeMinutes: article.readTimeMinutes,
                sentiment: article.sentiment
            )

            // Hero Image
            NewsDetailHeroImage(imageName: article.heroImageName)

            // Related Tickers
            if !article.relatedTickers.isEmpty {
                RelatedTickersRow(
                    tickers: article.relatedTickers,
                    onTickerTapped: onTickerTapped
                )
            }

            // Key Takeaways
            if !article.keyTakeaways.isEmpty {
                KeyTakeawaysSection(takeaways: article.keyTakeaways)
            }
        }
    }
}

#Preview {
    ScrollView {
        NewsDetailContent(
            article: NewsArticleDetail(
                headline: "NVIDIA Announces Record Q4 Earnings, Missed Expectations and CEO step down",
                source: NewsSource(name: "CNBC", iconName: nil),
                sentiment: .negative,
                publishedAt: Date(),
                readTimeMinutes: 4,
                heroImageName: nil,
                relatedTickers: ["APPL", "ORCL", "TSLA"],
                keyTakeaways: [
                    KeyTakeaway(index: 1, text: "Despite record Q4 results, missing expectations signals slowing growth and weaker-than-hoped execution."),
                    KeyTakeaway(index: 2, text: "A miss in a flagship quarter raises doubts about forward demand and near-term visibility."),
                    KeyTakeaway(index: 3, text: "Leadership transition at this scale introduces strategic and execution risk during a critical AI cycle."),
                    KeyTakeaway(index: 4, text: "With expectations priced for perfection, even a small miss could trigger outsized market pressure.")
                ],
                articleURL: nil
            ),
            onTickerTapped: { ticker in
                print("Tapped ticker: \(ticker)")
            }
        )
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
