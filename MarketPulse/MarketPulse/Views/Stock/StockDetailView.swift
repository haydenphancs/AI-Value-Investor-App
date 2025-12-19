import SwiftUI
import Combine

struct StockDetailView: View {
    @StateObject private var viewModel: StockDetailViewModel

    init(ticker: String) {
        _viewModel = StateObject(wrappedValue: StockDetailViewModel(ticker: ticker))
    }

    var body: some View {
        ZStack {
            if viewModel.isLoading && viewModel.stock == nil {
                LoadingView(message: "Loading stock details...")
            } else if let stock = viewModel.stock {
                ScrollView {
                    VStack(spacing: AppConstants.paddingLarge) {
                        // Header
                        StockHeaderView(stock: stock)

                        // Actions
                        ActionButtonsView(viewModel: viewModel)

                        // Description
                        if let description = stock.description {
                            StockDescriptionView(description: description)
                        }

                        // Fundamentals
                        if !viewModel.fundamentals.isEmpty {
                            FundamentalsSection(fundamentals: viewModel.fundamentals)
                        }

                        // Earnings
                        if !viewModel.earnings.isEmpty {
                            EarningsSection(earnings: viewModel.earnings)
                        }

                        // News
                        if !viewModel.news.isEmpty {
                            StockNewsSection(news: viewModel.news)
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
                    message: viewModel.errorMessage ?? "Failed to load stock"
                )
            }
        }
        .navigationTitle(viewModel.ticker)
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.loadStock()
        }
    }
}

struct StockHeaderView: View {
    let stock: Stock

    var body: some View {
        VStack(spacing: AppConstants.paddingMedium) {
            // Logo
            AsyncImage(url: URL(string: stock.logoUrl ?? "")) { image in
                image.resizable().scaledToFit()
            } placeholder: {
                Image(systemName: AppImages.logoPlaceholder)
                    .foregroundColor(.secondary)
            }
            .frame(width: AppConstants.logoLarge, height: AppConstants.logoLarge)

            // Ticker and Name
            VStack(spacing: 4) {
                Text(stock.ticker)
                    .font(.title)
                    .fontWeight(.bold)

                Text(stock.companyName)
                    .font(.headline)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }

            // Metadata
            HStack(spacing: AppConstants.paddingLarge) {
                if let sector = stock.sector {
                    MetadataItem(label: "Sector", value: sector)
                }

                if let exchange = stock.exchange {
                    MetadataItem(label: "Exchange", value: exchange)
                }

                if let marketCap = stock.formattedMarketCap {
                    MetadataItem(label: "Market Cap", value: marketCap)
                }
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct MetadataItem: View {
    let label: String
    let value: String

    var body: some View {
        VStack(spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundColor(.secondary)

            Text(value)
                .font(.subheadline)
                .fontWeight(.medium)
        }
    }
}

struct ActionButtonsView: View {
    @ObservedObject var viewModel: StockDetailViewModel

    var body: some View {
        VStack(spacing: AppConstants.paddingMedium) {
            Button(action: {
                Task {
                    await viewModel.toggleWatchlist()
                }
            }) {
                Label(
                    viewModel.isInWatchlist ? "Remove from Watchlist" : "Add to Watchlist",
                    systemImage: viewModel.isInWatchlist ? "star.fill" : "star"
                )
                .frame(maxWidth: .infinity)
                .padding()
                .background(viewModel.isInWatchlist ? Color.yellow : Color.accentColor)
                .foregroundColor(viewModel.isInWatchlist ? .black : .white)
                .cornerRadius(AppConstants.cornerRadiusMedium)
            }

            NavigationLink(destination: ResearchGenerationView(stockId: viewModel.stock?.id ?? "")) {
                Label("Generate Research Report", systemImage: "doc.text.magnifyingglass")
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.green)
                    .foregroundColor(.white)
                    .cornerRadius(AppConstants.cornerRadiusMedium)
            }
        }
    }
}

struct StockDescriptionView: View {
    let description: String
    @State private var isExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
            Text("About")
                .font(.headline)

            Text(description)
                .font(.body)
                .foregroundColor(.secondary)
                .lineLimit(isExpanded ? nil : 3)

            if description.count > 200 {
                Button(action: {
                    withAnimation {
                        isExpanded.toggle()
                    }
                }) {
                    Text(isExpanded ? "Show Less" : "Show More")
                        .font(.subheadline)
                        .foregroundColor(.accentColor)
                }
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct FundamentalsSection: View {
    let fundamentals: [Fundamental]

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            Text("Fundamentals")
                .font(.headline)
                .padding(.horizontal)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppConstants.paddingMedium) {
                    ForEach(fundamentals.prefix(10)) { fundamental in
                        FundamentalCard(fundamental: fundamental)
                    }
                }
                .padding(.horizontal)
            }
        }
    }
}

struct FundamentalCard: View {
    let fundamental: Fundamental

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
            Text("Q\(fundamental.fiscalQuarter ?? 0) \(fundamental.fiscalYear)")
                .font(.caption)
                .foregroundColor(.secondary)

            if let revenue = fundamental.revenue {
                MetricRow(label: "Revenue", value: formatCurrency(revenue))
            }

            if let netIncome = fundamental.netIncome {
                MetricRow(label: "Net Income", value: formatCurrency(netIncome))
            }

            if let eps = fundamental.eps {
                MetricRow(label: "EPS", value: "$\(NSDecimalNumber(decimal: eps).doubleValue, default: "%.2f")")
            }
        }
        .padding()
        .frame(width: 200)
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }

    private func formatCurrency(_ value: Decimal) -> String {
        let doubleValue = NSDecimalNumber(decimal: value).doubleValue
        if doubleValue >= 1_000_000_000 {
            return String(format: "$%.2fB", doubleValue / 1_000_000_000)
        } else if doubleValue >= 1_000_000 {
            return String(format: "$%.2fM", doubleValue / 1_000_000)
        }
        return String(format: "$%.0f", doubleValue)
    }
}

struct MetricRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .font(.caption)
                .fontWeight(.medium)
        }
    }
}

struct EarningsSection: View {
    let earnings: [Earnings]

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            Text("Upcoming Earnings")
                .font(.headline)

            ForEach(earnings.prefix(3)) { earning in
                EarningCard(earning: earning)
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct EarningCard: View {
    let earning: Earnings

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let date = earning.earningsDate {
                Text("Date: \(date.formatted())")
                    .font(.subheadline)
                    .fontWeight(.medium)
            }

            HStack {
                if let eps = earning.epsEstimate {
                    VStack(alignment: .leading) {
                        Text("EPS Estimate")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text("$\(NSDecimalNumber(decimal: eps).doubleValue, specifier: "%.2f")")
                            .font(.subheadline)
                    }
                }

                Spacer()

                if let revenue = earning.revenueEstimate {
                    VStack(alignment: .trailing) {
                        Text("Revenue Estimate")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text(formatRevenue(revenue))
                            .font(.subheadline)
                    }
                }
            }
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(AppConstants.cornerRadiusSmall)
    }

    private func formatRevenue(_ value: Decimal) -> String {
        let doubleValue = NSDecimalNumber(decimal: value).doubleValue
        if doubleValue >= 1_000_000_000 {
            return String(format: "$%.2fB", doubleValue / 1_000_000_000)
        }
        return String(format: "$%.2fM", doubleValue / 1_000_000)
    }
}

struct StockNewsSection: View {
    let news: [NewsArticle]

    var body: some View {
        VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
            Text("Recent News")
                .font(.headline)

            ForEach(news.prefix(5)) { article in
                NavigationLink(destination: NewsDetailView(newsId: article.id)) {
                    StockNewsRow(article: article)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct StockNewsRow: View {
    let article: NewsArticle

    var body: some View {
        HStack(alignment: .top, spacing: AppConstants.paddingMedium) {
            VStack(alignment: .leading, spacing: 4) {
                Text(article.title)
                    .font(.subheadline)
                    .fontWeight(.medium)
                    .lineLimit(2)

                Text(article.publishedAt.timeAgo())
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Spacer()

            if let sentiment = article.sentiment {
                Text(sentiment.emoji)
            }
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(AppConstants.cornerRadiusSmall)
    }
}

struct StockDetailView_Previews: PreviewProvider {
    static var previews: some View {
        StockDetailView(ticker: "AAPL")
    }
}
