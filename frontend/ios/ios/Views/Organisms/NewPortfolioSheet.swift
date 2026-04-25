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
        NavigationView {
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
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(isSubmitting)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSubmitting ? "Creating…" : "Create") { submit() }
                        .fontWeight(.semibold)
                        .disabled(isSubmitting || trimmedName.isEmpty)
                }
            }
        }
        .presentationDetents([.medium])
        .preferredColorScheme(.dark)
        .onAppear { nameFocused = true }
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
                dismiss()
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
        .preferredColorScheme(.dark)
}
