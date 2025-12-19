import SwiftUI

struct EducationLibraryView: View {
    @StateObject private var viewModel = EducationLibraryViewModel()

    var body: some View {
        NavigationView {
            VStack(spacing: 0) {
                // Search Bar
                HStack {
                    Image(systemName: "magnifyingglass")
                        .foregroundColor(.secondary)

                    TextField("Search books and articles...", text: $viewModel.searchQuery)
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

                // Tabs (only show if not searching)
                if viewModel.searchQuery.isEmpty {
                    Picker("Content Type", selection: $viewModel.selectedTab) {
                        Text("All").tag(0)
                        Text("Books").tag(1)
                        Text("Articles").tag(2)
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal)
                }

                // Content List
                ZStack {
                    if viewModel.isLoading && viewModel.allContent.isEmpty {
                        LoadingView(message: "Loading library...")
                    } else if viewModel.isSearching {
                        LoadingView(message: "Searching...")
                    } else if viewModel.displayedContent.isEmpty {
                        if !viewModel.searchQuery.isEmpty {
                            EmptyStateView(
                                icon: "magnifyingglass",
                                title: "No Results",
                                message: "No content found matching \"\(viewModel.searchQuery)\""
                            )
                        } else {
                            EmptyStateView(
                                icon: "book",
                                title: "No Content Available",
                                message: "Educational content will appear here once added."
                            )
                        }
                    } else {
                        ScrollView {
                            LazyVStack(spacing: AppConstants.paddingMedium) {
                                ForEach(viewModel.displayedContent) { content in
                                    NavigationLink(destination: EducationContentDetailView(contentId: content.id)) {
                                        EducationContentCard(content: content)
                                    }
                                    .buttonStyle(PlainButtonStyle())
                                }
                            }
                            .padding()
                        }
                        .refreshable {
                            await viewModel.loadContent()
                        }
                    }
                }
            }
            .navigationTitle("Education")
            .task {
                await viewModel.loadContent()
            }
        }
    }
}

struct EducationContentCard: View {
    let content: EducationContent

    var body: some View {
        HStack(alignment: .top, spacing: AppConstants.paddingMedium) {
            // Cover Image
            if let coverUrl = content.coverImageUrl {
                AsyncImage(url: URL(string: coverUrl)) { image in
                    image
                        .resizable()
                        .scaledToFill()
                } placeholder: {
                    Rectangle()
                        .fill(Color(.systemGray5))
                        .overlay(
                            Image(systemName: content.type == .book ? "book.fill" : "doc.text.fill")
                                .foregroundColor(.secondary)
                        )
                }
                .frame(width: 80, height: 120)
                .clipped()
                .cornerRadius(AppConstants.cornerRadiusSmall)
            } else {
                Rectangle()
                    .fill(Color(.systemGray5))
                    .frame(width: 80, height: 120)
                    .overlay(
                        Image(systemName: content.type == .book ? "book.fill" : "doc.text.fill")
                            .font(.largeTitle)
                            .foregroundColor(.secondary)
                    )
                    .cornerRadius(AppConstants.cornerRadiusSmall)
            }

            // Content Info
            VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                // Type Badge
                Text(content.type.displayName)
                    .font(.caption2)
                    .fontWeight(.medium)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(content.type == .book ? Color.blue.opacity(0.2) : Color.green.opacity(0.2))
                    .foregroundColor(content.type == .book ? .blue : .green)
                    .cornerRadius(4)

                // Title
                Text(content.title)
                    .font(.headline)
                    .foregroundColor(.primary)
                    .lineLimit(2)

                // Author
                if let author = content.author {
                    Text(author)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }

                // Publication Year
                if let year = content.publicationYear {
                    Text("Published: \(year)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                // Summary
                if let summary = content.truncatedSummary {
                    Text(summary)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(2)
                }

                // Topics
                if let topics = content.topics, !topics.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(topics.prefix(3), id: \.self) { topic in
                                Text(topic)
                                    .font(.caption2)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(Color(.systemGray5))
                                    .cornerRadius(4)
                            }
                        }
                    }
                }

                // Processing Status
                if !content.isProcessed {
                    HStack(spacing: 4) {
                        ProgressView()
                            .scaleEffect(0.7)
                        Text("Processing...")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
            }

            Spacer()
        }
        .padding()
        .background(Color(.systemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
        .shadow(radius: 2)
    }
}

struct EducationLibraryView_Previews: PreviewProvider {
    static var previews: some View {
        EducationLibraryView()
    }
}
