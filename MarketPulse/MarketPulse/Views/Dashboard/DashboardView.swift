import SwiftUI

struct DashboardView: View {
    @StateObject private var viewModel = DashboardViewModel()

    var body: some View {
        NavigationView {
            ZStack {
                if viewModel.isLoading && viewModel.widgetUpdate == nil {
                    LoadingView(message: "Loading dashboard...")
                } else {
                    ScrollView {
                        VStack(spacing: AppConstants.paddingLarge) {
                            // Widget Headline
                            if let widget = viewModel.widgetUpdate {
                                WidgetCard(widget: widget)
                            }

                            // Breaking News
                            if !viewModel.breakingNews.isEmpty {
                                BreakingNewsSection(news: viewModel.breakingNews)
                            }

                            // Watchlist Preview
                            if !viewModel.watchlistPreview.isEmpty {
                                WatchlistPreviewSection(items: viewModel.watchlistPreview)
                            }

                            // Recent Reports
                            if !viewModel.reportsPreview.isEmpty {
                                ReportsPreviewSection(reports: viewModel.reportsPreview)
                            }

                            if viewModel.widgetUpdate == nil && viewModel.breakingNews.isEmpty {
                                EmptyStateView(
                                    icon: "chart.line.uptrend.xyaxis",
                                    title: "Welcome to MarketPulse",
                                    message: "Your personalized investment insights will appear here."
                                )
                            }
                        }
                        .padding(.vertical, AppConstants.paddingMedium)
                    }
                    .refreshable {
                        await viewModel.refresh()
                    }
                }
            }
            .navigationTitle("Dashboard")
            .task {
                await viewModel.loadDashboard()
            }
        }
    }
}

// MARK: - Widget Card

struct WidgetCard: View {
    let widget: WidgetUpdate

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            HStack {
                Text(widget.emoji)
                    .font(.largeTitle)

                Spacer()

                SentimentBadge(sentiment: widget.sentiment)
            }

            Text(widget.headline)
                .font(.title3)
                .fontWeight(.bold)
                .lineLimit(3)

            Text(widget.dailyTrend)
                .font(.body)
                .foregroundColor(.secondary)
                .lineLimit(2)

            if let summary = widget.marketSummary {
                Text(summary)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(3)
                    .padding(.top, AppConstants.paddingSmall)
            }

            Text(widget.publishedAt.timeAgo())
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
        .shadow(radius: 2)
        .padding(.horizontal)
    }
}

// MARK: - Breaking News Section

struct BreakingNewsSection: View {
    let news: [BreakingNews]

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            HStack {
                Text("Breaking News")
                    .font(.title2)
                    .fontWeight(.bold)

                Spacer()

                NavigationLink(destination: NewsFeedView()) {
                    Text("See All")
                        .font(.subheadline)
                        .foregroundColor(.accentColor)
                }
            }
            .padding(.horizontal)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppConstants.paddingMedium) {
                    ForEach(news) { item in
                        NavigationLink(destination: NewsDetailView(newsId: item.newsId)) {
                            BreakingNewsCard(news: item)
                        }
                        .buttonStyle(PlainButtonStyle())
                    }
                }
                .padding(.horizontal)
            }
        }
    }
}

struct BreakingNewsCard: View {
    let news: BreakingNews

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
            HStack {
                Text(news.sentimentEmoji)
                Spacer()
                if let ticker = news.ticker {
                    Text(ticker)
                        .font(.caption)
                        .fontWeight(.bold)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.accentColor.opacity(0.2))
                        .cornerRadius(AppConstants.cornerRadiusSmall)
                }
            }

            Text(news.headline)
                .font(.subheadline)
                .fontWeight(.medium)
                .lineLimit(3)
                .foregroundColor(.primary)

            Text(news.publishedAt.timeAgo())
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(width: 250)
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

// MARK: - Watchlist Preview

struct WatchlistPreviewSection: View {
    let items: [WatchlistItem]

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            HStack {
                Text("Watchlist")
                    .font(.title2)
                    .fontWeight(.bold)

                Spacer()

                NavigationLink(destination: WatchlistView()) {
                    Text("See All")
                        .font(.subheadline)
                        .foregroundColor(.accentColor)
                }
            }
            .padding(.horizontal)

            VStack(spacing: 0) {
                ForEach(items) { item in
                    NavigationLink(destination: StockDetailView(ticker: item.stock.ticker)) {
                        WatchlistRowView(item: item)
                    }
                    .buttonStyle(PlainButtonStyle())

                    if item.id != items.last?.id {
                        Divider()
                            .padding(.leading, 60)
                    }
                }
            }
            .background(Color(.systemBackground))
            .cornerRadius(AppConstants.cornerRadiusMedium)
            .padding(.horizontal)
        }
    }
}

struct WatchlistRowView: View {
    let item: WatchlistItem

    var body: some View {
        HStack(spacing: AppConstants.paddingMedium) {
            // Logo
            AsyncImage(url: URL(string: item.stock.logoUrl ?? "")) { image in
                image.resizable().scaledToFit()
            } placeholder: {
                Image(systemName: AppImages.logoPlaceholder)
                    .foregroundColor(.secondary)
            }
            .frame(width: AppConstants.logoSmall, height: AppConstants.logoSmall)

            // Stock info
            VStack(alignment: .leading, spacing: 4) {
                Text(item.stock.ticker)
                    .font(.headline)

                Text(item.stock.companyName)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }

            Spacer()

            // Breaking news badge
            if item.hasBreakingNews {
                Image(systemName: "exclamationmark.circle.fill")
                    .foregroundColor(.red)
            }

            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding()
    }
}

// MARK: - Reports Preview

struct ReportsPreviewSection: View {
    let reports: [ResearchReport]

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            HStack {
                Text("Recent Reports")
                    .font(.title2)
                    .fontWeight(.bold)

                Spacer()

                NavigationLink(destination: ResearchListView()) {
                    Text("See All")
                        .font(.subheadline)
                        .foregroundColor(.accentColor)
                }
            }
            .padding(.horizontal)

            VStack(spacing: AppConstants.paddingSmall) {
                ForEach(reports) { report in
                    NavigationLink(destination: ResearchDetailView(reportId: report.id)) {
                        ReportPreviewCard(report: report)
                    }
                    .buttonStyle(PlainButtonStyle())
                }
            }
            .padding(.horizontal)
        }
    }
}

struct ReportPreviewCard: View {
    let report: ResearchReport

    var body: some View {
        HStack(spacing: AppConstants.paddingMedium) {
            Text(report.personaEmoji)
                .font(.title)

            VStack(alignment: .leading, spacing: 4) {
                if let title = report.title {
                    Text(title)
                        .font(.subheadline)
                        .fontWeight(.medium)
                        .lineLimit(2)
                }

                Text(report.personaDisplayName)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct DashboardView_Previews: PreviewProvider {
    static var previews: some View {
        DashboardView()
    }
}
