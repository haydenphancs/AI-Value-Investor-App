import SwiftUI

struct EducationLibraryView: View {
  enum Tab: String, CaseIterable { case all = "All", books = "Books", articles = "Articles" }
  @State private var selected: Tab = .all
  @State private var items: [EducationContent] = []
  @State private var query = ""

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        Picker("Filter", selection: $selected) {
          ForEach(Tab.allCases, id: \.self) { Text($0.rawValue).tag($0) }
        }
        .pickerStyle(.segmented)
        .padding()
        List {
          ForEach(items) { item in
            NavigationLink(destination: EducationDetailView(contentID: item.id)) {
              VStack(alignment: .leading) {
                Text(item.title).bold()
                if let author = item.author { Text(author).font(.caption).foregroundColor(.secondary) }
              }
            }
          }
        }
      }
      .navigationTitle("Education")
      .searchable(text: $query)
      .task { await load() }
    }
  }

  private func load() async { try? await Task.sleep(nanoseconds: 300_000_000) }
}

struct EducationDetailView: View {
  let contentID: String
  @State private var content: EducationContent?
  @State private var isLoading = true

  var body: some View {
    ScrollView {
      if let c = content {
        VStack(alignment: .leading, spacing: 12) {
          Text(c.title).font(.title2).bold()
          if let author = c.author { Text(author).foregroundColor(.secondary) }
          Text(c.summary)
          Button("Start Chat") {}
        }
        .padding()
      } else if isLoading { ProgressView() }
    }
    .navigationTitle("Content")
    .task { await load() }
  }
  private func load() async { try? await Task.sleep(nanoseconds: 300_000_000); isLoading = false }
}
