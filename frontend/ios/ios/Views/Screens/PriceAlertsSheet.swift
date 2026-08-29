//
//  PriceAlertsSheet.swift
//  ios
//
//  The bell in the ticker header opens this: set a price target, see the ones you have.
//
//  This is the first notification in the app a user asks for BY NAME — everything else is
//  something the system decided was interesting. That is why it is the only kind delivered
//  `time-sensitive` (it pierces Focus) and the only one exempt from quiet hours: an alert
//  that arrives after the move is over is worthless.
//
//  The list here and the "Price Alerts" section in Tracking → Alerts are the SAME rows, read
//  from the same `PriceAlertStore` — so creating one here shows up there immediately, and the
//  bell that opened this sheet badges the moment it exists.
//

import SwiftUI
import UserNotifications

struct PriceAlertsSheet: View {
    @Environment(\.dismiss) private var dismiss
    /// The draft form only.
    @StateObject private var viewModel: PriceAlertsViewModel
    /// The rows, shared with Tracking → Alerts and with the header bell.
    @ObservedObject private var store = PriceAlertStore.shared

    /// iOS's notification permission, read on appear and re-read after we ask.
    ///
    /// THIS SCREEN IS THE HIGHEST-INTENT MOMENT IN THE PRODUCT for that permission — the user
    /// is typing "tell me when ORCL hits $147" — and until now it neither asked nor admitted
    /// it could not deliver. The onboarding prompt is gated on finishing the flow AND picking
    /// a ticker (`OnboardingView.finish`), which is a defensible choice — iOS asks once, so a
    /// wasted prompt is permanent — but it left no second chance: Skip, or pick nothing, and
    /// the app never asks again. Anyone who installed before that flow existed already has
    /// `has_completed_onboarding = true` and never saw it at all. Measured consequence: 1 of
    /// 16 production users had a device token.
    @State private var permission: UNAuthorizationStatus = .notDetermined

    init(ticker: String, assetType: String = "stock") {
        _viewModel = StateObject(
            wrappedValue: PriceAlertsViewModel(ticker: ticker, assetType: assetType)
        )
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: AppSpacing.xl) {
                        // Only when DENIED. `.notDetermined` is handled by asking after the
                        // first successful create instead — a pre-emptive CTA here would
                        // compete with the button the user actually came to press.
                        if permission == .denied {
                            NotificationPermissionBanner(status: permission) {}
                        }
                        createCard
                        existingSection
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.md)
                    .padding(.bottom, AppSpacing.xxxl)
                }
            }
            .navigationTitle("\(viewModel.ticker) Alerts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            .task {
                await viewModel.load()
                await refreshPermission()
            }
            // Re-read on return from iOS Settings, so the banner clears without a re-entry.
            .onReceive(NotificationCenter.default.publisher(
                for: UIApplication.willEnterForegroundNotification
            )) { _ in
                Task { await refreshPermission() }
            }
        }
    }

    // MARK: - Create

    private func refreshPermission() async {
        permission = await UNUserNotificationCenter.current().notificationSettings()
            .authorizationStatus
    }

    /// Ask iOS, but only when it has never been asked.
    ///
    /// Mirrors `NotificationSettingsViewModel.requestPermissionIfNeeded`: calling this while
    /// `.denied` is a silent no-op that would leave the screen looking identical to the
    /// granted case. Denied users get the banner above instead, which sends them to Settings.
    private func requestPermissionIfNeeded() async {
        guard permission == .notDetermined else { return }
        // AWAIT the answer. Sleeping a fixed 500ms and re-reading was wrong: the prompt is
        // modal and a person takes seconds to read it, so the re-read observed
        // `.notDetermined` and the denial warning never appeared. Caught on the simulator.
        await PushNotificationManager.shared.requestAuthorizationResult()
        await refreshPermission()
    }

    private var createCard: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Alert me when")
                .font(AppTypography.bodyEmphasis)
                .foregroundColor(AppColors.textPrimary)

            Picker("Condition", selection: $viewModel.draftKind) {
                ForEach(PriceAlertKind.allCases, id: \.self) { kind in
                    Text(kind.label).tag(kind)
                }
            }
            .pickerStyle(.segmented)

            HStack(spacing: AppSpacing.sm) {
                Text(viewModel.draftKind.isPercent ? "%" : "$")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textSecondary)

                TextField(
                    viewModel.draftKind.isPercent ? "5" : "250.00",
                    text: $viewModel.draftThreshold
                )
                .keyboardType(.decimalPad)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
            }
            .padding(AppSpacing.md)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    // A field INSIDE a card must use the nested fill: sharing the parent's
                    // fill measures 1.00:1 against it in dark mode and the control vanishes.
                    .fill(AppColors.cardBackgroundNested)
            )

            Picker("Repeat", selection: $viewModel.draftRepeat) {
                ForEach(PriceAlertRepeat.allCases, id: \.self) { mode in
                    Text(mode.label).tag(mode)
                }
            }
            .pickerStyle(.segmented)

            Text(viewModel.draftRepeat.explanation)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Button {
                Task {
                    // Ask ONLY after a rule exists. The prompt is one-shot, so spending it on
                    // a failed create — or before the user has committed to anything — is how
                    // a permanent denial happens.
                    if await viewModel.create() {
                        await requestPermissionIfNeeded()
                    }
                }
            } label: {
                Text(viewModel.isSaving ? "Saving…" : "Add Alert")
                    .font(AppTypography.bodyEmphasis)
                    // On a saturated fill: `textOnAccent`, never `.white`.
                    .foregroundColor(AppColors.textOnAccent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            // ALWAYS the accent fill. Swapping in `textMuted` for the
                            // disabled state put `textOnAccent` at 2.54:1 in dark — the
                            // fill/ink pair has to change TOGETHER or not at all, so the
                            // disabled look comes from opacity on the whole control.
                            .fill(AppColors.primaryFill)
                    )
            }
            .buttonStyle(.plain)
            .disabled(!viewModel.canSave)
            .opacity(viewModel.canSave ? 1 : 0.4)

            // A disabled button with no reason is indistinguishable from a broken one.
            if viewModel.atCap {
                Text("You can have \(viewModel.maxPerTicker) alerts on \(viewModel.ticker). "
                     + "Remove one to add another.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.caution)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .cardBorder(cornerRadius: AppCornerRadius.large)
    }

    // MARK: - Existing

    @ViewBuilder
    private var existingSection: some View {
        let alerts = store.alerts(for: viewModel.ticker)
        switch store.state {
        case .loading:
            ProgressView().tint(AppColors.textSecondary)
                .frame(maxWidth: .infinity)

        case .error(let message):
            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.caution)

        // Inherited from the store rather than reimplemented: a guest tapping the bell used to
        // get `AppError`'s raw string, because this sheet had no auth guard of its own.
        case .signedOut:
            Text("Sign in to set price alerts.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

        case .reconnecting:
            Text("Reconnecting…")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

        case .loaded where alerts.isEmpty:
            Text("No alerts on \(viewModel.ticker) yet.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

        case .loaded:
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Your alerts")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                // Self-carded rows (`ActivityRow`), so no group wrapper — nesting a
                // card in a card measures 1.00:1 in dark and the rows disappear.
                VStack(spacing: AppSpacing.md) {
                    ForEach(alerts) { alert in
                        PriceAlertRuleRow(
                            alert: alert,
                            onToggle: { Task { await viewModel.toggleActive(alert) } },
                            onDelete: { Task { await viewModel.delete(alert) } }
                        )
                    }
                }

                // Answers the question the tester actually asked — "how do users know they
                // are the same?" The bell badge is the implicit signal; this is the stated one.
                Text("These also show in Tracking → Alerts.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
    }
}

#Preview {
    PriceAlertsSheet(ticker: "AAPL")
}
