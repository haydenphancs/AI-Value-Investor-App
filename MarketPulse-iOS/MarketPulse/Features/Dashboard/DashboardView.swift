import SwiftUI

struct DashboardView: View {
  @State private var widget: WidgetHeadline?
  @State private var breaking: [NewsItem] = []
  @State private var isLoading = true
  @State private var error: String?

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: 16) {
          if let w = widget {
            VStack(alignment: .leading, spacing: 8) {
              Text(w.headline).font(.title2).bold()
              HStack { Text(w.emoji); Text(w.daily_trend).foregroundColor(.secondary) }
            }
            .padding()
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 12))
          } else if isLoading {
            ProgressView()
          }

          SectionHeader(title: "Breaking News", actionTitle: "See All") {}
          LazyVStack(alignment: .leading, spacing: 12) {
            ForEach(breaking) { item in
              NavigationLink(value: item.id) {
                VStack(alignment: .leading, spacing: 6) {
                  Text(item.title).font(.headline)
                  Text(item.source_name).font(.caption).foregroundColor(.secondary)
                }
                .padding(.vertical, 8)
              }
            }
          }
        }
        .padding()
      }
      .navigationTitle("Home")
      .task { await load() }
      .refreshable { await load() }
    }
  }

  private func load() async {
    isLoading = true
    defer { isLoading = false }
    try? await Task.sleep(nanoseconds: 300_000_000)
  }
}

struct SectionHeader: View {
  let title: String
  let actionTitle: String
  let action: () -> Void
  init(title: String, actionTitle: String, action: @escaping () -> Void) { self.title = title; self.actionTitle = actionTitle; self.action = action }
  var body: some View {
    HStack { Text(title).font(.headline); Spacer(); Button(actionTitle, action: action) }
  }
}
