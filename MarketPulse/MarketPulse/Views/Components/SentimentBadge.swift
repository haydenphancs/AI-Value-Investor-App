import SwiftUI

struct SentimentBadge: View {
    let sentiment: SentimentType

    var body: some View {
        HStack(spacing: 4) {
            Text(sentiment.emoji)
                .font(.caption)

            Text(sentiment.rawValue.capitalized)
                .font(.caption)
                .fontWeight(.medium)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(backgroundColor.opacity(0.2))
        .foregroundColor(textColor)
        .cornerRadius(AppConstants.cornerRadiusSmall)
    }

    private var backgroundColor: Color {
        Color.sentiment(sentiment)
    }

    private var textColor: Color {
        Color.sentiment(sentiment)
    }
}

struct SentimentBadge_Previews: PreviewProvider {
    static var previews: some View {
        VStack(spacing: 16) {
            SentimentBadge(sentiment: .bullish)
            SentimentBadge(sentiment: .bearish)
            SentimentBadge(sentiment: .neutral)
        }
        .padding()
    }
}
