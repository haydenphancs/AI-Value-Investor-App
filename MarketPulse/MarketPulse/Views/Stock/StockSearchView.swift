import SwiftUI

struct StockSearchView: View {
    @StateObject private var viewModel = StockSearchViewModel()
    @FocusState private var isSearchFocused: Bool

    var body: some View {
        NavigationView {
            VStack {
                // Search Bar
                HStack {
                    Image(systemName: "magnifyingglass")
                        .foregroundColor(.secondary)

                    TextField("Search stocks by ticker or company name...", text: $viewModel.searchQuery)
                        .focused($isSearchFocused)
                        .onChange(of: viewModel.searchQuery) { _ in
                            viewModel.search()
                        }
                        .textInputAutocapitalization(.never)

                    if !viewModel.searchQuery.isEmpty {
                        Button(action: {
                            viewModel.searchQuery = ""
                            viewModel.searchResults = []
                        }) {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundColor(.secondary)
                        }
                    }
                }
                .padding()
                .background(Color(.systemGray6))
                .cornerRadius(AppConstants.cornerRadiusMedium)
                .padding()

                // Results
                ZStack {
                    if viewModel.searchQuery.isEmpty {
                        EmptyStateView(
                            icon: "magnifyingglass",
                            title: "Search Stocks",
                            message: "Enter a stock ticker or company name to start searching."
                        )
                    } else if viewModel.isSearching {
                        LoadingView(message: "Searching...")
                    } else if viewModel.searchResults.isEmpty && !viewModel.searchQuery.isEmpty {
                        EmptyStateView(
                            icon: "exclamationmark.magnifyingglass",
                            title: "No Results",
                            message: "No stocks found matching \"\(viewModel.searchQuery)\""
                        )
                    } else {
                        List(viewModel.searchResults) { stock in
                            NavigationLink(destination: StockDetailView(ticker: stock.ticker)) {
                                StockSearchRow(stock: stock)
                            }
                        }
                        .listStyle(.plain)
                    }
                }
            }
            .navigationTitle("Search Stocks")
        }
    }
}

struct StockSearchRow: View {
    let stock: StockSearchResult

    var body: some View {
        HStack(spacing: AppConstants.paddingMedium) {
            // Logo
            AsyncImage(url: URL(string: stock.logoUrl ?? "")) { image in
                image.resizable().scaledToFit()
            } placeholder: {
                Image(systemName: AppImages.logoPlaceholder)
                    .foregroundColor(.secondary)
            }
            .frame(width: AppConstants.logoMedium, height: AppConstants.logoMedium)

            // Info
            VStack(alignment: .leading, spacing: 4) {
                Text(stock.ticker)
                    .font(.headline)
                    .fontWeight(.bold)

                Text(stock.companyName)
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                    .lineLimit(1)

                if let sector = stock.sector {
                    Text(sector)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            Spacer()

            // Market Cap
            if let marketCap = stock.marketCap {
                let value = NSDecimalNumber(decimal: marketCap).doubleValue
                Text(formatMarketCap(value))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.vertical, AppConstants.paddingSmall)
    }

    private func formatMarketCap(_ value: Double) -> String {
        if value >= 1_000_000_000_000 {
            return String(format: "$%.2fT", value / 1_000_000_000_000)
        } else if value >= 1_000_000_000 {
            return String(format: "$%.2fB", value / 1_000_000_000)
        } else if value >= 1_000_000 {
            return String(format: "$%.2fM", value / 1_000_000)
        }
        return String(format: "$%.0f", value)
    }
}

struct StockSearchView_Previews: PreviewProvider {
    static var previews: some View {
        StockSearchView()
    }
}
