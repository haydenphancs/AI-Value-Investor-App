import SwiftUI

struct ReportsListView: View {
  @State private var reports: [ResearchReport] = []
  @State private var isLoading = true

  var body: some View {
    NavigationStack {
      List {
        ForEach(reports) { r in
          NavigationLink(destination: ReportDetailView(reportID: r.id)) {
            VStack(alignment: .leading) {
              Text(r.title).bold()
              Text(r.stock.company_name).font(.caption).foregroundColor(.secondary)
            }
          }
        }
      }
      .navigationTitle("Reports")
      .toolbar { Button("New") {} }
      .task { await load() }
      .refreshable { await load() }
    }
  }
  private func load() async { try? await Task.sleep(nanoseconds: 300_000_000); isLoading = false }
}

struct ReportDetailView: View {
  let reportID: String
  @State private var report: ResearchReport?
  @State private var isLoading = true

  var body: some View {
    ScrollView {
      if let r = report {
        VStack(alignment: .leading, spacing: 12) {
          Text(r.title).font(.title2).bold()
          Text(r.executive_summary)
          Button("Rate ★★★★☆") {}
          Button("Share") {}
          Button("Delete") {}
        }
        .padding()
      } else if isLoading { ProgressView() }
    }
    .navigationTitle("Report")
    .task { await load() }
  }
  private func load() async { try? await Task.sleep(nanoseconds: 300_000_000); isLoading = false }
}
