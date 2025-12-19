import SwiftUI

struct WatchlistView: View {
    @StateObject private var viewModel = WatchlistViewModel()

    var body: some View {
        NavigationView {
            ZStack {
                if viewModel.isLoading {
                    LoadingView(message: "Loading watchlist...")
                } else if viewModel.watchlistItems.isEmpty {
                    EmptyStateView(
                        icon: "star",
                        title: "Your Watchlist is Empty",
                        message: "Search for stocks and add them to your watchlist to track them here.",
                        actionTitle: "Search Stocks",
                        action: {
                            // Navigate to search
                        }
                    )
                } else {
                    List {
                        ForEach(viewModel.watchlistItems) { item in
                            NavigationLink(destination: StockDetailView(ticker: item.stock.ticker)) {
                                WatchlistItemRow(item: item)
                            }
                        }
                        .onDelete { indexSet in
                            for index in indexSet {
                                let item = viewModel.watchlistItems[index]
                                Task {
                                    await viewModel.removeFromWatchlist(item)
                                }
                            }
                        }
                    }
                    .refreshable {
                        await viewModel.loadWatchlist()
                    }
                }
            }
            .navigationTitle("Watchlist")
            .task {
                await viewModel.loadWatchlist()
            }
        }
    }
}

struct WatchlistItemRow: View {
    let item: WatchlistItem

    var body: some View {
        HStack(spacing: AppConstants.paddingMedium) {
            AsyncImage(url: URL(string: item.stock.logoUrl ?? "")) { image in
                image.resizable().scaledToFit()
            } placeholder: {
                Image(systemName: AppImages.logoPlaceholder)
                    .foregroundColor(.secondary)
            }
            .frame(width: AppConstants.logoMedium, height: AppConstants.logoMedium)

            VStack(alignment: .leading, spacing: 4) {
                Text(item.stock.ticker)
                    .font(.headline)
                    .fontWeight(.bold)

                Text(item.stock.companyName)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)

                if let notes = item.customNotes {
                    Text(notes)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .italic()
                        .lineLimit(1)
                }
            }

            Spacer()

            if item.hasBreakingNews {
                Image(systemName: "exclamationmark.circle.fill")
                    .foregroundColor(.red)
            }
        }
        .padding(.vertical, 4)
    }
}

struct WatchlistView_Previews: PreviewProvider {
    static var previews: some View {
        WatchlistView()
    }
}
