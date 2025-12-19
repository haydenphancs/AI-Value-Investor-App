import SwiftUI

struct NewsDetailView: View {
    @StateObject private var viewModel: NewsDetailViewModel
    @Environment(\.dismiss) private var dismiss

    init(newsId: String) {
        _viewModel = StateObject(wrappedValue: NewsDetailViewModel(newsId: newsId))
    }

    var body: some View {
        ZStack {
            if viewModel.isLoading {
                LoadingView(message: "Loading article...")
            } else if let article = viewModel.article {
                ScrollView {
                    VStack(alignment: .leading, spacing: AppConstants.paddingLarge) {
                        // Header
                        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
                            // Source and time
                            HStack {
                                Text(article.sourceName)
                                    .font(.caption)
                                    .foregroundColor(.secondary)

                                Spacer()

                                Text(article.publishedAt.timeAgo())
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }

                            // Title
                            Text(article.title)
                                .font(.title2)
                                .fontWeight(.bold)

                            // Sentiment
                            if let sentiment = article.sentiment {
                                SentimentBadge(sentiment: sentiment)
                            }
                        }

                        // Image
                        if let imageUrl = article.imageUrl {
                            AsyncImage(url: URL(string: imageUrl)) { image in
                                image
                                    .resizable()
                                    .scaledToFill()
                            } placeholder: {
                                Rectangle()
                                    .fill(Color(.systemGray5))
                                    .overlay(ProgressView())
                            }
                            .frame(maxWidth: .infinity)
                            .frame(height: 250)
                            .clipped()
                            .cornerRadius(AppConstants.cornerRadiusMedium)
                        }

                        // AI Summary
                        if let summary = article.aiSummary {
                            VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                                Text("AI Summary")
                                    .font(.headline)

                                Text(summary)
                                    .font(.body)
                                    .foregroundColor(.secondary)
                            }
                            .padding()
                            .background(Color(.secondarySystemBackground))
                            .cornerRadius(AppConstants.cornerRadiusMedium)
                        }

                        // Bullet points
                        if let bullets = article.aiSummaryBullets, !bullets.isEmpty {
                            VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                                Text("Key Points")
                                    .font(.headline)

                                ForEach(bullets, id: \.self) { bullet in
                                    HStack(alignment: .top, spacing: 8) {
                                        Text("•")
                                        Text(bullet)
                                            .font(.body)
                                    }
                                }
                            }
                        }

                        // Related Stocks
                        if let stocks = article.relatedStocks, !stocks.isEmpty {
                            VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
                                Text("Related Stocks")
                                    .font(.headline)

                                ScrollView(.horizontal, showsIndicators: false) {
                                    HStack(spacing: AppConstants.paddingMedium) {
                                        ForEach(stocks) { stock in
                                            NavigationLink(destination: StockDetailView(ticker: stock.ticker)) {
                                                RelatedStockCard(stock: stock)
                                            }
                                            .buttonStyle(PlainButtonStyle())
                                        }
                                    }
                                }
                            }
                        }

                        // Full Content
                        if let content = article.content {
                            VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                                Text("Full Article")
                                    .font(.headline)

                                Text(content)
                                    .font(.body)
                            }
                        }

                        // Disclaimer
                        Text("This is for educational purposes only. Not financial advice.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .italic()
                            .padding()
                            .background(Color.yellow.opacity(0.1))
                            .cornerRadius(AppConstants.cornerRadiusSmall)
                    }
                    .padding()
                }
            } else if viewModel.errorMessage != nil {
                EmptyStateView(
                    icon: "exclamationmark.triangle",
                    title: "Error",
                    message: viewModel.errorMessage ?? "Failed to load article"
                )
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if let article = viewModel.article {
                    ShareLink(item: URL(string: article.sourceUrl) ?? URL(string: "https://example.com")!) {
                        Image(systemName: "square.and.arrow.up")
                    }
                }
            }
        }
        .task {
            await viewModel.loadArticle()
        }
    }
}

struct RelatedStockCard: View {
    let stock: RelatedStock

    var body: some View {
        VStack(spacing: AppConstants.paddingSmall) {
            AsyncImage(url: URL(string: stock.logoUrl ?? "")) { image in
                image.resizable().scaledToFit()
            } placeholder: {
                Image(systemName: AppImages.logoPlaceholder)
                    .foregroundColor(.secondary)
            }
            .frame(width: AppConstants.logoMedium, height: AppConstants.logoMedium)

            Text(stock.ticker)
                .font(.caption)
                .fontWeight(.bold)

            Text(stock.companyName)
                .font(.caption2)
                .foregroundColor(.secondary)
                .lineLimit(2)
                .multilineTextAlignment(.center)
        }
        .frame(width: 100)
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct NewsDetailView_Previews: PreviewProvider {
    static var previews: some View {
        NewsDetailView(newsId: "sample-id")
    }
}
