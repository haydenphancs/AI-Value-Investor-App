//
//  DisclaimerAcknowledgementView.swift
//  ios
//
//  Screen: one-time, first-run acknowledgement of the financial / AI disclaimers.
//
//  Why this exists: the full DisclaimersView was reachable from exactly ONE place
//  (Profile → About & Legal), so a user could reach every score, verdict and Buy/Sell
//  label in the app without ever seeing that Caydex is not a registered investment
//  adviser and that AI output can be wrong. This is shown once, requires an explicit
//  tap, and records the acknowledgement.
//
//  Deliberately NOT a hard gate on using the app — Caydex is guest-first and Apple
//  5.1.1(v) wants apps usable without a login. It gates nothing but itself; the tap
//  is the point.
//

import SwiftUI

struct DisclaimerAcknowledgementView: View {
    /// Persisted so this is shown once per install.
    @AppStorage("has_acknowledged_disclaimers") private var hasAcknowledged = false
    @State private var showFullDisclaimers = false

    private struct Point: Identifiable {
        let id = UUID()
        let icon: String
        let title: String
        let body: String
    }

    private let points: [Point] = [
        Point(
            icon: "graduationcap.fill",
            title: "Educational, not advice",
            body: "Caydex is a research and education tool. Nothing in it is financial, "
                + "investment, legal, or tax advice, or a recommendation to buy or sell."
        ),
        Point(
            icon: "person.crop.circle.badge.xmark",
            title: "Not a registered adviser",
            body: "Caydex is not a registered investment adviser, broker-dealer, or "
                + "financial planner, and cannot give advice about your personal situation."
        ),
        Point(
            icon: "brain.head.profile",
            title: "AI output can be wrong",
            body: "Reports, summaries, and chat answers are AI-generated and may be "
                + "inaccurate, incomplete, or out of date. Verify anything you rely on."
        ),
        Point(
            icon: "chart.line.uptrend.xyaxis",
            title: "You decide, you carry the risk",
            body: "All investing involves risk, including loss of principal. Past "
                + "performance doesn't guarantee future results. Consider consulting a "
                + "qualified professional."
        )
    ]

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: AppSpacing.xl) {
                        VStack(alignment: .leading, spacing: AppSpacing.sm) {
                            Image("CaydexLogo")
                                .resizable()
                                .scaledToFit()
                                .frame(width: 56, height: 56)

                            Text("Before you start")
                                .font(AppTypography.titleLarge)
                                .foregroundColor(AppColors.textPrimary)

                            Text("A few things to know about what Caydex is — and isn't.")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textSecondary)
                        }
                        .padding(.top, AppSpacing.xxl)

                        VStack(alignment: .leading, spacing: AppSpacing.lg) {
                            ForEach(points) { point in
                                HStack(alignment: .top, spacing: AppSpacing.md) {
                                    Image(systemName: point.icon)
                                        .font(AppTypography.iconLarge)
                                        .foregroundColor(AppColors.primaryBlue)
                                        .frame(width: 26)

                                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                        Text(point.title)
                                            .font(AppTypography.bodyEmphasis)
                                            .foregroundColor(AppColors.textPrimary)
                                        Text(point.body)
                                            .font(AppTypography.bodySmall)
                                            .foregroundColor(AppColors.textSecondary)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                        }

                        Button {
                            showFullDisclaimers = true
                        } label: {
                            Text("Read the full disclaimers")
                                .font(AppTypography.bodySmallEmphasis)
                                .foregroundColor(AppColors.primaryBlue)
                                .underline()
                        }
                        .buttonStyle(.plain)

                        Spacer(minLength: AppSpacing.xl)
                    }
                    .padding(.horizontal, AppSpacing.xl)
                }

                // Explicit acknowledgement. There is no dismiss affordance anywhere
                // else on this screen — the tap IS the record.
                Button {
                    hasAcknowledged = true
                } label: {
                    Text("I Understand")
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.lg)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                                .fill(AppColors.primaryBlue)
                        )
                }
                .buttonStyle(.plain)
                .padding(.horizontal, AppSpacing.xl)
                .padding(.bottom, AppSpacing.xxl)
            }
        }
        .interactiveDismissDisabled(true)
        .sheet(isPresented: $showFullDisclaimers) {
            NavigationStack {
                DisclaimersView()
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button("Done") { showFullDisclaimers = false }
                                .foregroundColor(AppColors.primaryBlue)
                        }
                    }
            }
            .preferredColorScheme(.dark)
        }
    }
}

#Preview {
    DisclaimerAcknowledgementView()
        .preferredColorScheme(.dark)
}
