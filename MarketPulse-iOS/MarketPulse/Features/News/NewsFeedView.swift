import SwiftUI

struct NewsFeedView: View {
  @State private var articles: [NewsItem] = []
  @State private var sentiment: Sentiment?
  @State private var isLoading = false
  @State private var page = 1
  @State private var canLoadMore = true
  @State private var isLoadingMore = false
  private let service = NewsService.mock

  var body: some View {
    NavigationStack {
      List {
        ForEach(articles) { a in
          NavigationLink(destination: NewsDetailView(newsID: a.id)) {
            VStack(alignment: .leading, spacing: 6) {
              Text(a.title).font(.headline)
              HStack {
                Text(a.source_name).font(.caption)
                Text(" • ")
                Text(a.published_at, style: .relative).font(.caption).foregroundColor(.secondary)
                Spacer()
                Text(a.sentiment_emoji)
              }
            }
          }
        }
        if isLoadingMore { HStack { Spacer(); ProgressView(); Spacer() } }
        Color.clear.frame(height: 1).onAppear { Task { await loadMoreIfNeeded() } }
      }
      .navigationTitle("News")
      .toolbar {
        Menu("Filter") {
          Button("All") { Task { sentiment = nil; await load(reset: true) } }
          Button("Bullish") { Task { sentiment = .bullish; await load(reset: true) } }
          Button("Bearish") { Task { sentiment = .bearish; await load(reset: true) } }
          Button("Neutral") { Task { sentiment = .neutral; await load(reset: true) } }
        }
      }
      .task { await load(reset: true) }
      .refreshable { await load(reset: true) }
      .onAppear { if articles.isEmpty { Task { await load(reset: true) } } }
    }
  }

  private func load(reset: Bool) async {
    if reset { page = 1; articles = []; canLoadMore = true }
    guard !isLoading else { return }
    isLoading = true
    defer { isLoading = false }
    do {
      let result = try await service.fetchFeed(page, sentiment)
      articles = result.items
      canLoadMore = result.nextCursor != nil
    } catch {
    }
  }

  private func loadMoreIfNeeded() async {
    guard !isLoadingMore, canLoadMore else { return }
    isLoadingMore = true
    defer { isLoadingMore = false }
    do {
      let nextPage = page + 1
      let result = try await service.fetchFeed(nextPage, sentiment)
      page = nextPage
      articles.append(contentsOf: result.items)
      canLoadMore = result.nextCursor != nil
    } catch {
    }
  }
}

struct NewsDetailView: View {
  let newsID: String
  @State private var detail: NewsDetail?
  @State private var isLoading = false
  private let service = NewsService.mock
 
  var body: some View {
    ScrollView {
      if let d = detail {
        VStack(alignment: .leading, spacing: 12) {
          Text(d.title).font(.title2).bold()
          HStack { Text(d.source_name).font(.subheadline); Text(d.published_at, style: .date).foregroundColor(.secondary) }
          if !d.ai_summary_bullets.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
              ForEach(d.ai_summary_bullets, id: \.self) { bullet in
                HStack(alignment: .top) { Text("•").bold(); Text(bullet) }
              }
            }
          }
          if let content = d.content { Text(content) }
        }
        .padding()
      } else if isLoading {
        ProgressView()
      }
    }
    .navigationTitle("Article")
    .task { await load() }
  }
 
  private func load() async {
    guard !isLoading else { return }
    isLoading = true
    defer { isLoading = false }
    do { detail = try await service.fetchDetail(newsID) } catch { }
  }
}
