//
//  CreditHistoryView.swift
//  ios
//
//  Screen: the credit statement behind the Account balance — every spend, refund, grant
//  and purchase, newest first, grouped by day.
//
//  WHY IT EXISTS. The Account screen showed a balance and nothing else, and chat spends
//  silently BY DESIGN: SYSTEM_DESIGN_GUIDELINES §9b.8 renders no price on a normally
//  charged turn, because "putting a price on every answer turns chat into a meter". That
//  decision stands — and it is exactly why a separate, opt-in statement has to exist. A
//  TestFlight tester asked for this in as many words.
//
//  ⚠️ This screen is NOT a per-answer meter and must not become one. Nothing here is
//  surfaced inside chat; the user comes looking.
//
//  ⚠️ ONE `LazyVStack`, rows as a direct `ForEach`. Do not nest another lazy stack inside
//  it and do not move the rows behind an intermediate `View` struct — that combination is
//  the documented 100%-CPU main-thread hang (see the header of
//  `Views/Organisms/NotificationInboxSection.swift`). That is also why each day's rounded
//  group is drawn BY ITS ROWS (`CreditHistoryRow` + `RowGroupPosition`) rather than by a
//  per-day container: a container would grow in place every time "Load more" appends to
//  the last day.
//
//  COMPACT + "LOAD MORE" (developer request, 2026-09-24): rows went from ~100pt cards to
//  ~52pt two-line segments, and paging moved from scroll-triggered to an explicit button,
//  50 rows a page. The history goes back to the account's FIRST movement —
//  `credit_transactions` is never trimmed — so the end of the list says so explicitly
//  rather than just stopping.
//
//  All user-facing copy for a row (`title`, `subtitle`, `poolNote`) is BACKEND-authored
//  and rendered verbatim, so a ledger reason added after this build ships still reads
//  correctly here. See `Models/CreditHistoryModels.swift`.
//

import SwiftUI

struct CreditHistoryView: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel: CreditHistoryViewModel

    /// Takes the REPOSITORY, not the ViewModel, and is `@MainActor`.
    ///
    /// `CreditHistoryViewModel.init` is MainActor-isolated, and a default argument is
    /// evaluated at the CALL SITE under nonisolated checking — so `= CreditHistoryViewModel()`
    /// as a default here does not compile. Injecting the repository keeps previews and tests
    /// substitutable without that problem.
    @MainActor
    init(repository: CreditHistoryRepositoryProtocol? = nil) {
        _viewModel = StateObject(wrappedValue: CreditHistoryViewModel(repository: repository))
    }

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                // THE one lazy stack. Zero spacing: rows of a day abut to form one group, and
                // the day headers carry their own padding.
                LazyVStack(alignment: .leading, spacing: 0) {
                    content
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.sm)
                .padding(.bottom, AppSpacing.xxxl)
            }
        }
        .navigationTitle("Credit History")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .refreshable { await viewModel.loadAndWait() }
        .task { await viewModel.loadAndWait() }
        // auth.md §7 — this is one account's spending. Without the clear, the next person
        // to sign in on this device would find the previous account's statement sitting in
        // a live ViewModel.
        .reloadOnIdentityChange { _ in
            viewModel.reset()
            await viewModel.loadAndWait()
        }
        // Heal a load that RACED session restore: arriving here at cold launch can run the
        // first load while auth is still `.restoring`, which renders "Reconnecting…" with
        // nothing scheduled to re-run it.
        .onChange(of: appState.auth.status) { _, status in
            guard status == .authenticated, isAuthBlocked else { return }
            Task { await viewModel.loadAndWait() }
        }
    }

    private var isAuthBlocked: Bool {
        viewModel.state == .reconnecting || viewModel.state == .signedOut
    }

    // MARK: - States

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {
        case .loading:
            ProgressView()
                .tint(AppColors.textSecondary)
                .frame(maxWidth: .infinity)
                .padding(.top, AppSpacing.xxl)

        case .empty:
            InlineRetryNotice(
                message: "No credit activity yet. Once you generate a report or ask Cay AI, "
                    + "every credit spent, refunded or added shows up here.",
                systemImage: "clock.arrow.circlepath",
                // `textMuted`, not `caution` — an empty statement is not a failure, and a
                // warning colour next to this copy reads as a bug to report.
                iconColor: AppColors.textMuted
            )

        case .reconnecting:
            InlineRetryNotice(
                message: "Reconnecting your account…",
                systemImage: "arrow.clockwise",
                iconColor: AppColors.textMuted
            )

        case .signedOut:
            InlineRetryNotice(
                message: "Sign in to see your credit history.",
                systemImage: "person.crop.circle.badge.checkmark",
                iconColor: AppColors.textMuted,
                retryTitle: "Sign In",
                onRetry: { appState.requestSignIn(for: "see your credit history") }
            )

        case .error(let message):
            InlineRetryNotice(message: message) { viewModel.load() }

        case .loaded:
            rows
        }
    }

    // MARK: - Rows

    @ViewBuilder
    private var rows: some View {
        ForEach(viewModel.days) { day in
            // Same muted band as the Reports and Chat history lists.
            // UNIFORM padding, never "0 for the first day": a refresh that adds a new day at
            // the top would then change the old first header's height IN PLACE — the lazy-
            // child resize this screen must never do.
            Text(day.label)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textMuted)
                .padding(.top, AppSpacing.md)
                .padding(.bottom, AppSpacing.xs)
                .padding(.leading, AppSpacing.xs)
                .accessibilityAddTraits(.isHeader)

            // Direct lazy children — see the header. `enumerated()` only to tell each row
            // where it sits in the day's group; the id stays the ledger row's.
            ForEach(Array(day.items.enumerated()), id: \.element.id) { index, item in
                CreditHistoryRow(
                    item: item,
                    position: RowGroupPosition(index: index, count: day.items.count)
                )
            }
        }

        footer
            .padding(.top, AppSpacing.lg)
    }

    /// Three states, and never silence: a list that just STOPS reads as "maybe it didn't
    /// load", which on a statement means "maybe I was never charged for that".
    ///
    /// All three are 44pt tall, so switching between them never changes this lazy child's
    /// height.
    @ViewBuilder
    private var footer: some View {
        if viewModel.isLoadingMore {
            ProgressView()
                .tint(AppColors.textSecondary)
                .frame(maxWidth: .infinity, minHeight: 44)
        } else if viewModel.hasMore {
            Button {
                viewModel.loadMore()
            } label: {
                Text(viewModel.loadMoreFailed ? "Couldn't load more · Try again" : "Load more")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
                    .frame(maxWidth: .infinity, minHeight: 44)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large, style: .continuous)
                            .cardFill()
                    )
                    // A Button hit-tests what its label DRAWS; the shape makes the whole
                    // 44pt bar the target, not just the words.
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        } else {
            Text("You've reached the start of your credit history.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity, minHeight: 44)
        }
    }
}

// MARK: - Previews

private struct PreviewCreditHistoryRepository: CreditHistoryRepositoryProtocol {
    let page: CreditHistoryDTO
    func fetchCreditHistory(limit: Int, before: String?) async throws -> CreditHistoryDTO {
        before == nil ? page : CreditHistoryDTO(items: [])
    }
}

private func previewPage() -> CreditHistoryDTO {
    let now = ISO8601DateFormatter()
    now.formatOptions = [.withInternetDateTime]
    let stamp = { (offset: TimeInterval) in now.string(from: Date().addingTimeInterval(offset)) }
    return CreditHistoryDTO(items: [
        CreditTransactionDTO(
            id: "9", createdAt: stamp(-1_800), delta: -20, kind: "spend",
            title: "Deep research report", subtitle: "NVDA", reason: "report_charge"
        ),
        CreditTransactionDTO(
            id: "8", createdAt: stamp(-3_600), delta: -1, kind: "spend",
            title: "Ask Cay AI", isReversed: true, reason: "chat_charge"
        ),
        CreditTransactionDTO(
            id: "7", createdAt: stamp(-3_590), delta: 1, kind: "refund",
            title: "Refund · answer was already cached", reason: "chat_cache_hit"
        ),
        CreditTransactionDTO(
            id: "6", createdAt: stamp(-90_000), delta: -20, kind: "spend",
            title: "Deep research report", subtitle: "AAPL",
            poolNote: "15 monthly + 5 purchased", reason: "report_charge"
        ),
        CreditTransactionDTO(
            id: "5", createdAt: stamp(-400_000), delta: 540, kind: "purchase",
            title: "Credit pack", subtitle: "Power", poolNote: "Never expires",
            reason: "pack_purchase"
        ),
        CreditTransactionDTO(
            id: "4", createdAt: stamp(-1_900_000), delta: 100_000, kind: "grant",
            title: "Monthly credits", reason: "monthly_reset"
        ),
    ], nextCursor: nil)
}

#Preview("Loaded") {
    NavigationStack {
        CreditHistoryView(
            repository: PreviewCreditHistoryRepository(page: previewPage())
        )
    }
    .environment(AppState())
}

#Preview("Empty") {
    NavigationStack {
        CreditHistoryView(
            repository: PreviewCreditHistoryRepository(page: CreditHistoryDTO(items: []))
        )
    }
    .environment(AppState())
}
