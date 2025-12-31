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

    var body: some View {
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

                // Insights Summary Card (non-scrolling)
                if let summary = viewModel.insightSummary {
                    InsightsSummaryCard(summary: summary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                }

                // Static "Live News" Header (non-scrolling)
                LiveNewsHeader(onFilterTapped: handleFilterTapped)

                // Scrollable Content with sticky section headers
                ScrollView(showsIndicators: false) {
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
                onDismiss: { showManageAssetsSheet = false }
            )
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
        print("Article tapped: \(article.headline)")
    }
}

// MARK: - Manage Assets Sheet
struct ManageAssetsSheet: View {
    let tickers: [NewsFilterTab]
    var onDismiss: (() -> Void)?

    var body: some View {
        NavigationView {
            List {
                Section("Your Tickers") {
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
                    }
                    .onDelete { _ in
                        // Handle delete
                        print("Delete ticker")
                    }
                }

                Section {
                    Text("Swipe left to remove a ticker from your watchlist.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }
            .listStyle(InsetGroupedListStyle())
            .navigationTitle("Manage Assets")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        onDismiss?()
                    }
                    .fontWeight(.semibold)
                }
            }
        }
        .presentationDetents([.medium])
    }
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
