//
//  NewPortfolioSheet.swift
//  ios
//
//  Organism: small sheet for creating a new named portfolio. On success the
//  newly created portfolio becomes active so the user immediately sees their
//  empty list, ready to add tickers to.
//

import SwiftUI

struct NewPortfolioSheet: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var name: String = ""
    @State private var isSubmitting: Bool = false
    @State private var errorMessage: String?
    @FocusState private var nameFocused: Bool

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                VStack(alignment: .leading, spacing: AppSpacing.lg) {
                    Text("Group your tickers into a named portfolio. Alerts and Insights below will scope to whichever portfolio is active.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)

                    TextField("Portfolio name", text: $name)
                        .textFieldStyle(.roundedBorder)
                        .focused($nameFocused)
                        .submitLabel(.done)
                        .onSubmit { submit() }
                        .disabled(isSubmitting)

                    if let errorMessage {
                        Text(errorMessage)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.bearish)
                    }

                    Spacer()
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.top, AppSpacing.lg)
            }
            .navigationTitle("New Portfolio")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { close() }
                        .disabled(isSubmitting)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSubmitting ? "Creating…" : "Create") { submit() }
                        .fontWeight(.semibold)
                        .disabled(isSubmitting || trimmedName.isEmpty)
                }
            }
        }
        // `.large` is not decoration. `.onAppear` focuses the field, so the keyboard
        // comes up during the presentation animation and forces the sheet taller than
        // its only detent — the reporter's screenshot shows it at FULL height with the
        // toolbar at the top of the screen, not at the .medium position where this code
        // puts it. A sheet driven past its only legal detent leaves the presentation
        // controller resolving a height it was never given, and taps stop landing where
        // the chrome is drawn. Giving it somewhere legal to go is the fix; `.medium`
        // stays first so the sheet still opens compact.
        .presentationDetents([.medium, .large])
        .onAppear { nameFocused = true }
    }

    /// The ONE way this sheet closes.
    ///
    /// Resigning focus first collapses the keyboard so the sheet is back at a legal
    /// detent before it animates away. Clearing the view-model flag is what actually
    /// dismisses it — `showNewPortfolioSheet` is only ever set to `true`
    /// (`TrackingViewModel:1057`), so until now closing relied entirely on SwiftUI
    /// writing `false` back through the `isPresented` binding via
    /// `@Environment(\.dismiss)`. That is one indirection too many for a control the
    /// user is reporting as dead; `AddAssetSheet` and `SortOptionsSheet` in
    /// `TrackingView.swift` already clear their own flags for the same reason.
    /// `dismiss()` stays as the belt-and-braces half — calling both is idempotent.
    private func close() {
        nameFocused = false
        viewModel.showNewPortfolioSheet = false
        dismiss()
    }

    private var trimmedName: String {
        name.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func submit() {
        let candidate = trimmedName
        guard !candidate.isEmpty, !isSubmitting else { return }

        // Frontend duplicate check — server enforces too, but catching it
        // here means no round-trip for the obvious case.
        let lower = candidate.lowercased()
        if viewModel.portfolioStore.portfolios.contains(where: { $0.name.lowercased() == lower }) {
            errorMessage = "A portfolio with that name already exists."
            return
        }

        isSubmitting = true
        errorMessage = nil

        Task { @MainActor in
            do {
                _ = try await viewModel.createPortfolio(named: candidate)
                isSubmitting = false
                close()
            } catch {
                isSubmitting = false
                errorMessage = "Couldn't create the portfolio. Try again."
                print("[NewPortfolioSheet] ❌ \(error)")
            }
        }
    }
}

#Preview {
    NewPortfolioSheet(viewModel: TrackingViewModel())
}
