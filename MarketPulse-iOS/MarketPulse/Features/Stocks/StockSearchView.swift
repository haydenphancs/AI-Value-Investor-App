import SwiftUI

struct StockSearchView: View {
  @State private var query = ""
  @State private var results: [StockSearchResult] = []
  @State private var isSearching = false

  var body: some View {
    List {
      ForEach(results) { r in
        NavigationLink(destination: StockDetailView(ticker: r.ticker)) {
          VStack(alignment: .leading) {
            Text(r.ticker).bold()
            Text(r.company_name).font(.subheadline).foregroundColor(.secondary)
          }
        }
      }
    }
    .navigationTitle("Search Stocks")
    .searchable(text: $query, prompt: "Ticker or company")
    .onChange(of: query) { _ in Task { await search() } }
  }

  private func search() async {
    guard query.count >= 2 else { results = []; return }
    isSearching = true
    try? await Task.sleep(nanoseconds: 300_000_000)
    isSearching = false
  }
}

struct StockDetailView: View {
  let ticker: String
  @State private var detail: StockDetail?
  @State private var fundamentals: [FundamentalsPoint] = []
  @State private var earnings: Earnings?
  @State private var news: [NewsItem] = []
  @State private var isLoading = true

  var body: some View {
    ScrollView {
      if let d = detail {
        VStack(alignment: .leading, spacing: 12) {
          Text("\(d.company_name) (\(d.ticker))").font(.title2).bold()
          if let desc = d.description { Text(desc) }
          Button("Add to Watchlist") {}
          Button("Generate Research Report") {}
          Button("Chat About This Stock") {}
        }
        .padding()
      } else if isLoading { ProgressView() }
    }
    .navigationTitle(ticker)
    .task { await load() }
  }

  private func load() async { try? await Task.sleep(nanoseconds: 300_000_000); isLoading = false }
}
