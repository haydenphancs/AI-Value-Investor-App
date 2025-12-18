import SwiftUI

struct WatchlistView: View {
  @State private var items: [WatchlistItem] = []
  @State private var isLoading = true

  var body: some View {
    NavigationStack {
      Group {
        if items.isEmpty && !isLoading {
          ContentUnavailableView("No stocks yet", systemImage: "star")
        } else {
          List {
            ForEach(items) { item in
              NavigationLink(destination: StockDetailView(ticker: item.stock.ticker)) {
                HStack {
                  VStack(alignment: .leading) {
                    Text(item.stock.ticker).bold()
                    Text(item.stock.company_name).font(.caption)
                  }
                  Spacer()
                  if item.has_breaking_news { Text("Breaking").foregroundColor(.red) }
                }
              }
            }
            .onDelete { _ in }
          }
        }
      }
      .navigationTitle("Watchlist")
      .toolbar { EditButton() }
      .task { await load() }
      .refreshable { await load() }
    }
  }

  private func load() async { try? await Task.sleep(nanoseconds: 300_000_000); isLoading = false }
}
