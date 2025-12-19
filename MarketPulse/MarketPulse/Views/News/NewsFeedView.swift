import SwiftUI

struct NewsFeedView: View {
    @StateObject private var viewModel = NewsViewModel()

    var body: some View {
        NavigationView {
            ZStack {
                if viewModel.isLoading && viewModel.newsItems.isEmpty {
                    LoadingView(message: "Loading news...")
                } else if viewModel.newsItems.isEmpty {
                    EmptyStateView(
                        icon: "newspaper",
                        title: "No News Available",
                        message: "There are no news articles matching your filter."
                    )
                } else {
                    ScrollView {
                        LazyVStack(spacing: AppConstants.paddingMedium) {
                            ForEach(viewModel.newsItems) { item in
                                NavigationLink(destination: NewsDetailView(newsId: item.id)) {
                                    NewsCardView(news: item)
                                }
                                .buttonStyle(PlainButtonStyle())
                            }

                            if viewModel.isLoadingMore {
                                ProgressView()
                                    .padding()
                            } else if viewModel.hasMore {
                                Color.clear
                                    .frame(height: 1)
                                    .onAppear {
                                        Task {
                                            await viewModel.loadNews()
                                        }
                                    }
                            }
                        }
                        .padding()
                    }
                    .refreshable {
                        await viewModel.loadNews(refresh: true)
                    }
                }
            }
            .navigationTitle("News Feed")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Menu {
                        Button(action: {
                            Task {
                                await viewModel.filterBySentiment(nil)
                            }
                        }) {
                            Label("All", systemImage: "list.bullet")
                        }

                        Button(action: {
                            Task {
                                await viewModel.filterBySentiment(.bullish)
                            }
                        }) {
                            Label("Bullish", systemImage: "arrow.up.circle.fill")
                        }

                        Button(action: {
                            Task {
                                await viewModel.filterBySentiment(.bearish)
                            }
                        }) {
                            Label("Bearish", systemImage: "arrow.down.circle.fill")
                        }

                        Button(action: {
                            Task {
                                await viewModel.filterBySentiment(.neutral)
                            }
                        }) {
                            Label("Neutral", systemImage: "minus.circle.fill")
                        }
                    } label: {
                        Image(systemName: "line.3.horizontal.decrease.circle")
                    }
                }
            }
            .task {
                await viewModel.loadNews()
            }
        }
    }
}

struct NewsCardView: View {
    let news: NewsFeedItem

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            // Header
            HStack {
                Text(news.sourceName)
                    .font(.caption)
                    .foregroundColor(.secondary)

                Spacer()

                if let sentiment = news.sentiment {
                    SentimentBadge(sentiment: sentiment)
                }
            }

            // Image
            if let imageUrl = news.imageUrl {
                AsyncImage(url: URL(string: imageUrl)) { image in
                    image
                        .resizable()
                        .scaledToFill()
                } placeholder: {
                    Rectangle()
                        .fill(Color(.systemGray5))
                        .overlay(ProgressView())
                }
                .frame(height: 200)
                .clipped()
                .cornerRadius(AppConstants.cornerRadiusMedium)
            }

            // Title
            Text(news.title)
                .font(.headline)
                .lineLimit(3)
                .foregroundColor(.primary)

            // Bullet points
            if let bullets = news.aiSummaryBullets, !bullets.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(bullets.prefix(3), id: \.self) { bullet in
                        HStack(alignment: .top, spacing: 8) {
                            Text("•")
                                .foregroundColor(.secondary)
                            Text(bullet)
                                .font(.subheadline)
                                .foregroundColor(.secondary)
                                .lineLimit(2)
                        }
                    }
                }
            }

            // Footer
            Text(news.publishedAt.timeAgo())
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
        .shadow(radius: 2)
    }
}

struct NewsFeedView_Previews: PreviewProvider {
    static var previews: some View {
        NewsFeedView()
    }
}
