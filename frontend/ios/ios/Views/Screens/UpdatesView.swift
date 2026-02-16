//
//  UpdatesView.swift
//  ios
//
//  Main Updates/News screen combining all organisms
//

import SwiftUI

struct UpdatesView: View {
    @StateObject private var viewModel = UpdatesViewModel()
    @Binding var selectedTab: HomeTab
    @State private var showManageAssetsSheet = false
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
                        onProfileTapped: handleProfileTapped
                    )

                    // Tab Bar with tickers
                    UpdatesTabBar(
                        tabs: viewModel.filterTabs,
                        selectedTab: $viewModel.selectedTab,
                        onManageAssets: handleManageAssets
                    )

                    // Static "Live News" Header (non-scrolling, stays at top)
                    LiveNewsHeader(onFilterTapped: handleFilterTapped)

                    // Scrollable Content with sticky section headers
                    ScrollView(showsIndicators: false) {
                        // Insights Summary Card (scrollable)
                        if let summary = viewModel.insightSummary {
                            InsightsSummaryCard(summary: summary)
                                .padding(.horizontal, AppSpacing.lg)
                                .padding(.vertical, AppSpacing.sm)
                        }

                        LiveNewsTimeline(
                            groupedNews: viewModel.groupedNews,
                            onArticleTapped: handleArticleTapped
                        )

                        // Bottom spacing for tab bar
                        Spacer()
                            .frame(height: 100)
                    }
                    .refreshable {
                        await viewModel.refresh()
                    }

                    // Tab Bar
                    CustomTabBar(selectedTab: $selectedTab)
                }

                // Loading overlay
                if viewModel.isLoading {
                    LoadingOverlay()
                }
            }
            .navigationBarHidden(true)
            .navigationDestination(item: $selectedNewsArticle) { article in
                NewsDetailView(article: article)
            }
            .onChange(of: viewModel.selectedTab) { oldValue, newValue in
                if let newTab = newValue {
                    viewModel.selectTab(newTab)
                }
            }
            .sheet(isPresented: $viewModel.showFilterSheet) {
                NewsFilterSheet(
                    filterOptions: $viewModel.filterOptions,
                    onApply: {
                        viewModel.showFilterSheet = false
                    }
                )
            }
            .sheet(isPresented: $showManageAssetsSheet) {
                ManageAssetsSheet(
                    tickers: viewModel.filterTabs.filter { !$0.isMarketTab },
                    onDismiss: { showManageAssetsSheet = false },
                    onAddTicker: { ticker in viewModel.addTicker(ticker) }
                )
            }
        }
    }

    // MARK: - Action Handlers
    private func handleProfileTapped() {
        print("Profile tapped")
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

    @State private var showTickerSearch = false

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                List {
                    Section {
                        ForEach(tickers) { ticker in
                            HStack {
                                Text(ticker.title)
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textPrimary)

                                Spacer()

                                if let change = ticker.formattedChange {
                                    Text(change)
                                        .font(AppTypography.callout)
                                        .foregroundColor(ticker.isPositive ? AppColors.bullish : AppColors.bearish)
                                }
                            }
                            .listRowBackground(AppColors.cardBackground)
                        }
                        .onDelete { _ in
                            // Handle delete
                            print("Delete ticker")
                        }
                    }

                    Section {
                        Button(action: { showTickerSearch = true }) {
                            HStack {
                                Image(systemName: "plus.circle.fill")
                                    .font(.system(size: 22))
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
    @Environment(\.dismiss) private var dismiss

    private let popularTickers: [TickerSearchItem] = [
        TickerSearchItem(ticker: "AAPL", companyName: "Apple Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "MSFT", companyName: "Microsoft Corp.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "GOOGL", companyName: "Alphabet Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "AMZN", companyName: "Amazon.com Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "TSLA", companyName: "Tesla Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "NVDA", companyName: "NVIDIA Corp.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "META", companyName: "Meta Platforms Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "JPM", companyName: "JPMorgan Chase & Co.", exchange: "NYSE"),
        TickerSearchItem(ticker: "V", companyName: "Visa Inc.", exchange: "NYSE"),
        TickerSearchItem(ticker: "BRK.B", companyName: "Berkshire Hathaway", exchange: "NYSE"),
        TickerSearchItem(ticker: "WMT", companyName: "Walmart Inc.", exchange: "NYSE"),
        TickerSearchItem(ticker: "DIS", companyName: "Walt Disney Co.", exchange: "NYSE"),
        TickerSearchItem(ticker: "NFLX", companyName: "Netflix Inc.", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "AMD", companyName: "Advanced Micro Devices", exchange: "NASDAQ"),
        TickerSearchItem(ticker: "INTC", companyName: "Intel Corp.", exchange: "NASDAQ"),
    ]

    private var filteredResults: [TickerSearchItem] {
        if searchText.isEmpty {
            return popularTickers
        }
        let query = searchText.lowercased()
        return popularTickers.filter {
            $0.ticker.lowercased().contains(query) ||
            $0.companyName.lowercased().contains(query)
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
                                                    .font(AppTypography.bodyBold)
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
    var onApply: (() -> Void)?

    @State private var selectedSources: Set<String> = []
    @State private var selectedSentiments: Set<NewsSentiment> = []

    private let availableSources = ["Reuters", "CNBC", "Bloomberg", "WSJ", "Zacks", "MarketWatch"]

    var body: some View {
        NavigationView {
            List {
                // Sources Section
                Section("Sources") {
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
