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
//  The bell existed in `TickerDetailHeader` from the start and every one of the five
//  detail screens passed it `nil`, so it never rendered. This is what it was waiting for.
//

import SwiftUI

struct PriceAlertsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: PriceAlertsViewModel

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
            .task { await viewModel.load() }
        }
    }

    // MARK: - Create

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
                Task { await viewModel.create() }
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
        switch viewModel.state {
        case .loading:
            ProgressView().tint(AppColors.textSecondary)
                .frame(maxWidth: .infinity)

        case .error(let message):
            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.caution)

        case .loaded where viewModel.alerts.isEmpty:
            Text("No alerts on \(viewModel.ticker) yet.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

        case .loaded:
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Your alerts")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                VStack(spacing: 1) {
                    ForEach(viewModel.alerts) { alert in
                        alertRow(alert)
                    }
                }
                .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
                .cardBorder(cornerRadius: AppCornerRadius.large)
            }
        }
    }

    private func alertRow(_ alert: PriceAlertDTO) -> some View {
        HStack(spacing: AppSpacing.md) {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(alert.summary)
                    .font(AppTypography.body)
                    .foregroundColor(alert.isActive ? AppColors.textPrimary : AppColors.textMuted)

                // Surfaces `armed == false`. Without it, a rule that is active but latched
                // after firing looks simply broken.
                if let reason = alert.quietReason {
                    Text(reason)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                } else {
                    Text(alert.repeatRule.label)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer()

            Toggle("", isOn: Binding(
                get: { alert.isActive },
                set: { _ in Task { await viewModel.toggleActive(alert) } }
            ))
            .labelsHidden()
            .tint(AppColors.primaryBlue)

            Button {
                Task { await viewModel.delete(alert) }
            } label: {
                Image(systemName: "trash")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.loss)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Delete alert \(alert.summary)")
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
    }
}

#Preview {
    PriceAlertsSheet(ticker: "AAPL")
}
