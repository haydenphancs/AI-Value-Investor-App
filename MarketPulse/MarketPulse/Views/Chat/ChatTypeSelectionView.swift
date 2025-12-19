import SwiftUI

struct ChatTypeSelectionView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = ChatCreationViewModel()
    @State private var navigateToChat = false

    var body: some View {
        NavigationView {
            VStack(spacing: AppConstants.paddingLarge) {
                Text("Start a New Chat")
                    .font(.title2)
                    .fontWeight(.bold)
                    .padding(.top, AppConstants.paddingLarge)

                Text("Choose what you'd like to discuss")
                    .font(.subheadline)
                    .foregroundColor(.secondary)

                VStack(spacing: AppConstants.paddingMedium) {
                    ChatTypeCard(
                        icon: "📚",
                        title: "Ask About Education Content",
                        description: "Chat about investment books and articles",
                        action: {
                            Task {
                                await viewModel.createSession(type: .education)
                                if viewModel.createdSession != nil {
                                    navigateToChat = true
                                }
                            }
                        }
                    )

                    NavigationLink(destination: StockSearchForChatView(viewModel: viewModel)) {
                        ChatTypeCardButton(
                            icon: "📈",
                            title: "Analyze a Stock",
                            description: "Deep dive into a specific stock"
                        )
                    }
                    .buttonStyle(PlainButtonStyle())

                    ChatTypeCard(
                        icon: "💬",
                        title: "General Questions",
                        description: "Ask anything about investing",
                        action: {
                            Task {
                                await viewModel.createSession(type: .general)
                                if viewModel.createdSession != nil {
                                    navigateToChat = true
                                }
                            }
                        }
                    )
                }
                .padding(.horizontal)

                if viewModel.isCreating {
                    ProgressView()
                        .padding()
                }

                Spacer()
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Cancel") {
                        dismiss()
                    }
                }
            }
            .background(
                NavigationLink(
                    destination: viewModel.createdSession.map { session in
                        ChatConversationView(sessionId: session.id)
                    },
                    isActive: $navigateToChat
                ) {
                    EmptyView()
                }
                .hidden()
            )
        }
    }
}

struct ChatTypeCard: View {
    let icon: String
    let title: String
    let description: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ChatTypeCardButton(icon: icon, title: title, description: description)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

struct ChatTypeCardButton: View {
    let icon: String
    let title: String
    let description: String

    var body: some View {
        HStack(spacing: AppConstants.paddingMedium) {
            Text(icon)
                .font(.system(size: 40))

            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.headline)
                    .foregroundColor(.primary)

                Text(description)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Spacer()

            Image(systemName: "chevron.right")
                .foregroundColor(.secondary)
        }
        .padding()
        .background(Color(.secondarySystemBackground))
        .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct StockSearchForChatView: View {
    @ObservedObject var viewModel: ChatCreationViewModel
    @StateObject private var searchViewModel = StockSearchViewModel()
    @Environment(\.dismiss) private var dismiss
    @State private var navigateToChat = false

    var body: some View {
        VStack {
            // Search Bar
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(.secondary)

                TextField("Search stocks...", text: $searchViewModel.searchQuery)
                    .onChange(of: searchViewModel.searchQuery) { _ in
                        searchViewModel.search()
                    }
                    .textInputAutocapitalization(.never)

                if !searchViewModel.searchQuery.isEmpty {
                    Button(action: {
                        searchViewModel.searchQuery = ""
                        searchViewModel.searchResults = []
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
            if searchViewModel.isSearching {
                LoadingView(message: "Searching...")
            } else if !searchViewModel.searchResults.isEmpty {
                List(searchViewModel.searchResults) { stock in
                    Button(action: {
                        Task {
                            await viewModel.createSession(type: .stockAnalysis, stockId: stock.id)
                            if viewModel.createdSession != nil {
                                navigateToChat = true
                            }
                        }
                    }) {
                        StockSearchRow(stock: stock)
                    }
                }
                .listStyle(.plain)
            } else if searchViewModel.searchQuery.isEmpty {
                EmptyStateView(
                    icon: "magnifyingglass",
                    title: "Search for a Stock",
                    message: "Enter a ticker or company name to start analyzing"
                )
            }
        }
        .navigationTitle("Select Stock")
        .background(
            NavigationLink(
                destination: viewModel.createdSession.map { session in
                    ChatConversationView(sessionId: session.id)
                },
                isActive: $navigateToChat
            ) {
                EmptyView()
            }
            .hidden()
        )
    }
}

struct ChatTypeSelectionView_Previews: PreviewProvider {
    static var previews: some View {
        ChatTypeSelectionView()
    }
}
