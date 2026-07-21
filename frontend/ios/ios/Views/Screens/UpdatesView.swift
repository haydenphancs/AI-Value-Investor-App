//
//  UpdatesView.swift
//  ios
//
//  Main Updates/News screen combining all organisms
//

import SwiftUI

struct UpdatesView: View {
    @Environment(\.appState) private var appState
    /// True only while the Updates tab is the visible one. `ContentView` keeps all
    /// five tabs alive and toggles opacity, so `.onAppear` fires once at launch
    /// for every tab — this is the only reliable "became visible" signal.
    @Environment(\.isActiveTab) private var isActiveTab
    @StateObject private var viewModel = UpdatesViewModel()
    @Binding var selectedTab: HomeTab
    @State private var showManageAssetsSheet = false
    @State private var showProfile = false
    @State private var showSearch = false
    @State private var selectedNewsArticle: NewsArticle?

    var body: some View {
        NavigationStack {
            ZStack(alignment: .bottom) {
                // Background
                AppColors.background
                    .ignoresSafeArea()

                // Main Content
                VStack(spacing: 0) {
                    // Header
                    UpdatesHeader(
                        onSearchTapped: handleSearchTapped,
                        onProfileTapped: handleProfileTapped
                    )

                    // Tab Bar with tickers
                    UpdatesTabBar(
                        tabs: viewModel.filterTabs,
                        selectedTab: $viewModel.selectedTab,
                        onManageAssets: handleManageAssets
                    )

                    // Static "Live News" Header (non-scrolling, stays at top)
                    LiveNewsHeader(
                        filterLabel: viewModel.filterOptions.chipLabel,
                        hasActiveFilters: viewModel.filterOptions.hasActiveFilters,
                        onFilterTapped: handleFilterTapped
                    )

                    // Scrollable Content with sticky section headers
                    ScrollView(showsIndicators: false) {
                        LazyVStack(spacing: 0) {
                            // Insights Summary Card (scrollable)
                            if let summary = viewModel.insightSummary {
                                InsightsSummaryCard(summary: summary)
                                    .padding(.horizontal, AppSpacing.lg)
                                    .padding(.vertical, AppSpacing.sm)
                            }

                            if viewModel.isLoading && viewModel.groupedNews.isEmpty {
                                // Skeleton rows, not a blocking overlay: the old
                                // full-screen LoadingOverlay swallowed taps
                                // (including Back) — the same problem already
                                // fixed on the asset-detail screens.
                                loadingSkeleton
                            } else if let message = viewModel.error, viewModel.groupedNews.isEmpty {
                                errorState(message)
                            } else if viewModel.groupedNews.isEmpty {
                                emptyState
                            } else {
                                LiveNewsTimeline(
                                    groupedNews: viewModel.groupedNews,
                                    onArticleTapped: handleArticleTapped
                                )
                            }

                            // Bottom spacing for tab bar
                            Spacer()
                                .frame(height: 100)
                        }
                    }
                    .refreshable {
                        await viewModel.refresh()
                    }

                    // Tab Bar
                    CustomTabBar(selectedTab: $selectedTab)
                }
            }
            .navigationBarHidden(true)
            .task(id: isActiveTab) {
                guard isActiveTab else { return }
                await viewModel.loadIfNeeded()
            }
            .navigationDestination(item: $selectedNewsArticle) { article in
                // Pass the CURRENT scope. Cache rows are partitioned by scope
                // (`ticker = cache_key`), so enriching an AAPL article under
                // "__MARKET__" matches zero rows and the Key Takeaways section
                // silently never appears.
                NewsDetailView(
                    article: article,
                    scope: viewModel.selectedTab?.scope ?? UpdatesScope.market
                )
            }
            .onChange(of: viewModel.selectedTab) { oldValue, newValue in
                if let newTab = newValue {
                    viewModel.selectTab(newTab)
                }
            }
            .sheet(isPresented: $viewModel.showFilterSheet) {
                NewsFilterSheet(
                    filterOptions: $viewModel.filterOptions,
                    availableSources: viewModel.availableSources,
                    onApply: {
                        viewModel.showFilterSheet = false
                    }
                )
            }
            .alert(
                "Couldn't update your tickers",
                isPresented: Binding(
                    get: { viewModel.watchlistError != nil },
                    set: { if !$0 { viewModel.watchlistError = nil } }
                )
            ) {
                Button("OK", role: .cancel) { viewModel.watchlistError = nil }
            } message: {
                // Watchlist writes previously failed silently: the sheet showed
                // nothing and the error leaked into the FEED's empty state.
                Text(viewModel.watchlistError ?? "")
            }
            .sheet(isPresented: $showManageAssetsSheet) {
                ManageAssetsSheet(
                    tickers: viewModel.filterTabs.filter { !$0.isMarketTab },
                    onDismiss: { showManageAssetsSheet = false },
                    onAddTicker: { ticker in
                        Task { await viewModel.addTicker(ticker) }
                    },
                    onRemoveTicker: { ticker in
                        Task { await viewModel.removeTicker(ticker) }
                    }
                )
            }
            .fullScreenCover(isPresented: $showProfile) {
                ProfileView()
                    .environment(appState)
                    .environment(\.appState, appState)
                    .preferredColorScheme(.dark)
            }
            .fullScreenCover(isPresented: $showSearch) {
                SearchView()
                    .preferredColorScheme(.dark)
            }
        }
    }

    // MARK: - States

    private var loadingSkeleton: some View {
        VStack(spacing: AppSpacing.md) {
            ForEach(0..<5, id: \.self) { _ in
                TickerNewsShimmerCard()
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.sm)
    }

    private func errorState(_ message: String) -> some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "wifi.exclamationmark")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.textMuted)

            Text("Couldn't load the news")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            Button("Try Again") {
                Task { await viewModel.refresh() }
            }
            .font(AppTypography.bodyEmphasis)
            .foregroundColor(AppColors.primaryBlue)
            .padding(.top, AppSpacing.xs)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, AppSpacing.xl)
        .padding(.vertical, AppSpacing.xxl)
    }

    private var emptyState: some View {
        VStack(spacing: AppSpacing.sm) {
            Image(systemName: "newspaper")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.textMuted)

            Text(viewModel.filterOptions.hasActiveFilters
                 ? "No stories match your filters"
                 : "No recent stories")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            Text(viewModel.filterOptions.hasActiveFilters
                 ? "Try clearing a filter."
                 : "Check back shortly — the feed refreshes through the day.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
    }

    // MARK: - Action Handlers
    private func handleSearchTapped() {
        showSearch = true
    }

    private func handleProfileTapped() {
        showProfile = true
    }

    private func handleAddTicker() {
        print("Add ticker tapped")
    }

    private func handleManageAssets() {
        showManageAssetsSheet = true
    }

    private func handleFilterTapped() {
        viewModel.openFilterOptions()
    }

    private func handleArticleTapped(_ article: NewsArticle) {
        selectedNewsArticle = article
    }
}

// MARK: - Manage Assets Sheet
struct ManageAssetsSheet: View {
    let tickers: [NewsFilterTab]
    var onDismiss: (() -> Void)?
    var onAddTicker: ((String) -> Void)?
    var onRemoveTicker: ((String) -> Void)?

    @State private var showTickerSearch = false

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                List {
                    Section {
                        if tickers.isEmpty {
                            Text("No tickers yet. Add one below to get a dedicated news feed for it.")
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.textMuted)
                                .listRowBackground(AppColors.cardBackground)
                        }
                        ForEach(tickers) { ticker in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(ticker.title)
                                        .font(AppTypography.body)
                                        .foregroundColor(AppColors.textPrimary)
                                    if let name = ticker.companyName, !name.isEmpty {
                                        Text(name)
                                            .font(AppTypography.caption)
                                            .foregroundColor(AppColors.textSecondary)
                                            .lineLimit(1)
                                    }
                                }

                                Spacer()

                                if let change = ticker.formattedChange {
                                    Text(change)
                                        .font(AppTypography.bodySmall)
                                        .foregroundColor(ticker.isPositive ? AppColors.bullish : AppColors.bearish)
                                }
                            }
                            .listRowBackground(AppColors.cardBackground)
                        }
                        .onDelete { offsets in
                            // Actually removes from the watchlist. This used to
                            // be a `print("Delete ticker")` — the row animated
                            // away and came straight back on the next refresh.
                            for index in offsets where index < tickers.count {
                                onRemoveTicker?(tickers[index].scope)
                            }
                        }
                    }

                    Section {
                        Button(action: { showTickerSearch = true }) {
                            HStack {
                                Image(systemName: "plus.circle.fill")
                                    .font(AppTypography.iconXL)
                                    .foregroundColor(AppColors.primaryBlue)
                                Text("Add Ticker")
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                        .listRowBackground(AppColors.cardBackground)
                    }

                    Section {
                        Text("Swipe left to remove a ticker from your watchlist.")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .listRowBackground(Color.clear)
                    }
                }
                .listStyle(InsetGroupedListStyle())
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Your Tickers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        onDismiss?()
                    }
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showTickerSearch) {
            TickerSearchSheet { ticker in
                onAddTicker?(ticker)
                showTickerSearch = false
            }
        }
    }
}

// MARK: - Ticker Search Sheet
struct TickerSearchSheet: View {
    var onSelectTicker: ((String) -> Void)?

    @State private var searchText = ""
    @State private var searchResults: [TickerSearchItem] = []
    @State private var isSearching = false
    @State private var searchTask: Task<Void, Never>?
    @Environment(\.dismiss) private var dismiss

    /// Shown before the user types anything. These are a genuine starting
    /// point, not search results — everything below the fold comes from the
    /// live `/stocks/search` endpoint, so the user is not limited to 15 names.
    private let popularTickers: [TickerSearchItem] = [
        TickerSearchItem(ticker: "AAPL", companyName: "Apple Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "MSFT", companyName: "Microsoft Corp.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "GOOGL", companyName: "Alphabet Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "AMZN", companyName: "Amazon.com Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "TSLA", companyName: "Tesla Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "NVDA", companyName: "NVIDIA Corp.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "META", companyName: "Meta Platforms Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "BRK.B", companyName: "Berkshire Hathaway", exchange: "NYSE"),
    ]

    private var filteredResults: [TickerSearchItem] {
        searchText.isEmpty ? popularTickers : searchResults
    }

    /// Debounced live search. The previous implementation filtered a hardcoded
    /// list of 15 symbols, so searching for anything else returned "No results".
    private func runSearch(_ query: String) {
        searchTask?.cancel()
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 1 else {
            searchResults = []
            isSearching = false
            return
        }
        searchTask = Task {
            try? await Task.sleep(nanoseconds: 300_000_000)   // debounce keystrokes
            if Task.isCancelled { return }
            isSearching = true
            defer { isSearching = false }
            do {
                let results: [StockSearchResult] = try await APIClient.shared.request(
                    endpoint: .searchStocks(query: trimmed, limit: 25),
                    responseType: [StockSearchResult].self
                )
                if Task.isCancelled { return }
                searchResults = results
                    // Equities only. The endpoint also returns crypto pairs,
                    // which the watchlist news feed does not cover.
                    .filter { ($0.type ?? "stock") == "stock" }
                    .map {
                        TickerSearchItem(
                            ticker: $0.ticker,
                            companyName: $0.companyName,
                            exchange: $0.exchange ?? ""
                        )
                    }
                print("✅ TickerSearch: \(searchResults.count) results for '\(trimmed)'")
            } catch {
                if Task.isCancelled { return }
                searchResults = []
                print("⚠️ TickerSearch: search failed for '\(trimmed)': \(AppError.from(error).message)")
            }
        }
    }

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                VStack(spacing: 0) {
                    // Search bar
                    HStack(spacing: AppSpacing.sm) {
                        Image(systemName: "magnifyingglass")
                            .foregroundColor(AppColors.textMuted)
                        TextField("Search ticker or company", text: $searchText)
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textPrimary)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.characters)
                            .onChange(of: searchText) { _, newValue in
                                runSearch(newValue)
                            }

                        if isSearching {
                            ProgressView().tint(AppColors.textMuted)
                        }
                    }
                    .padding(AppSpacing.md)
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.medium)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.sm)

                    if !searchText.isEmpty && filteredResults.isEmpty {
                        Spacer()
                        Text("No results found")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textMuted)
                        Spacer()
                    } else {
                        List {
                            Section(header: Text(searchText.isEmpty ? "Popular" : "Results")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                            ) {
                                ForEach(filteredResults) { item in
                                    Button {
                                        onSelectTicker?(item.ticker)
                                    } label: {
                                        HStack {
                                            VStack(alignment: .leading, spacing: 2) {
                                                Text(item.ticker)
                                                    .font(AppTypography.bodyEmphasis)
                                                    .foregroundColor(AppColors.textPrimary)
                                                Text(item.companyName)
                                                    .font(AppTypography.caption)
                                                    .foregroundColor(AppColors.textSecondary)
                                            }
                                            Spacer()
                                            Text(item.exchange)
                                                .font(AppTypography.caption)
                                                .foregroundColor(AppColors.textMuted)
                                        }
                                    }
                                    .listRowBackground(AppColors.cardBackground)
                                }
                            }
                        }
                        .listStyle(InsetGroupedListStyle())
                        .scrollContentBackground(.hidden)
                    }
                }
            }
            .navigationTitle("Add Ticker")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

// MARK: - Ticker Search Item Model
struct TickerSearchItem: Identifiable {
    let id = UUID()
    let ticker: String
    let companyName: String
    let exchange: String
}

// MARK: - News Filter Sheet
struct NewsFilterSheet: View {
    @Binding var filterOptions: NewsFilterOptions
    /// Publishers actually present in the loaded feed. The previous hardcoded
    /// list ("Reuters", "CNBC", …) frequently matched NOTHING, so applying a
    /// source filter emptied the timeline for no visible reason.
    let availableSources: [String]
    var onApply: (() -> Void)?

    @State private var selectedSources: Set<String> = []
    @State private var selectedSentiments: Set<NewsSentiment> = []

    var body: some View {
        NavigationView {
            List {
                // Sources Section
                Section("Sources") {
                    if availableSources.isEmpty {
                        Text("No publishers in this feed yet.")
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textMuted)
                    }
                    ForEach(availableSources, id: \.self) { source in
                        HStack {
                            Text(source)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)

                            Spacer()

                            if selectedSources.contains(source) {
                                Image(systemName: "checkmark")
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            if selectedSources.contains(source) {
                                selectedSources.remove(source)
                            } else {
                                selectedSources.insert(source)
                            }
                        }
                    }
                }

                // Sentiment Section
                Section("Sentiment") {
                    ForEach(NewsSentiment.allCases, id: \.self) { sentiment in
                        HStack {
                            Text(sentiment.displayName)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)

                            Spacer()

                            if selectedSentiments.contains(sentiment) {
                                Image(systemName: "checkmark")
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture {
                            if selectedSentiments.contains(sentiment) {
                                selectedSentiments.remove(sentiment)
                            } else {
                                selectedSentiments.insert(sentiment)
                            }
                        }
                    }
                }

                // Reset Section
                Section {
                    Button("Reset Filters") {
                        selectedSources.removeAll()
                        selectedSentiments.removeAll()
                    }
                    .foregroundColor(AppColors.bearish)
                }
            }
            .listStyle(InsetGroupedListStyle())
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        onApply?()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Apply") {
                        filterOptions.sources = Array(selectedSources)
                        filterOptions.sentiments = Array(selectedSentiments)
                        onApply?()
                    }
                    .fontWeight(.semibold)
                }
            }
        }
        .presentationDetents([.medium, .large])
        .onAppear {
            selectedSources = Set(filterOptions.sources)
            selectedSentiments = Set(filterOptions.sentiments)
        }
    }
}

// MARK: - Standalone Preview
struct UpdatesViewStandalone: View {
    @State private var selectedTab: HomeTab = .updates

    var body: some View {
        UpdatesView(selectedTab: $selectedTab)
    }
}

#Preview {
    UpdatesViewStandalone()
}
