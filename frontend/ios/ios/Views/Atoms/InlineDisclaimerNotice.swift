//
//  InlineDisclaimerNotice.swift
//  ios
//
//  Atom: a compact "not financial advice" line that opens the full DisclaimersView.
//
//  Why this exists: DisclaimersView held the load-bearing language ("not registered
//  investment advisors", "consult a qualified financial advisor") but was reachable
//  from exactly ONE place — Profile → About & Legal. A user could reach every score,
//  verdict and Buy/Sell label in the app without ever seeing it. This atom puts the
//  short form next to the verdict and makes the full text one tap away.
//
//  Deliberately generic (no domain types) so it qualifies as an Atom.
//

import SwiftUI

struct InlineDisclaimerNotice: View {
    /// Short line shown inline. Keep it to one line where possible.
    ///
    /// Pass `""` for a LINK-ONLY notice (icon + link, no prose). That is for a host
    /// that already prints the disclaimer wording itself — the report's bottom
    /// Disclaimer section — where repeating the sentence would say the same thing
    /// twice and only the tap-through to the full text is still missing.
    let text: String
    /// Trailing tappable affordance that opens the full disclaimers.
    let linkLabel: String

    @State private var showDisclaimers = false

    init(
        text: String = "Educational analysis · not financial advice",
        linkLabel: String = "Details"
    ) {
        self.text = text
        self.linkLabel = linkLabel
    }

    var body: some View {
        Button {
            showDisclaimers = true
        } label: {
            HStack(spacing: AppSpacing.xxs) {
                Image(systemName: "info.circle")
                    .font(AppTypography.iconTiny)
                if !text.isEmpty {
                    Text(text)
                        .font(AppTypography.caption)
                }
                Text(linkLabel)
                    .font(AppTypography.captionEmphasis)
                    .underline()
            }
            .foregroundColor(AppColors.textMuted)
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
        }
        .buttonStyle(.plain)
        // Link-only (empty `text`) would otherwise announce as ". Tap for full
        // disclaimers." — a leading pause and no subject.
        .accessibilityLabel(
            text.isEmpty
                ? "\(linkLabel). Tap for full disclaimers."
                : "\(text). Tap for full disclaimers."
        )
        .sheet(isPresented: $showDisclaimers) {
            // DisclaimersView is push-only (no nav stack, no dismiss of its own),
            // so wrap it and supply a Done button when presenting as a sheet.
            NavigationStack {
                DisclaimersView()
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button("Done") { showDisclaimers = false }
                                .foregroundColor(AppColors.primaryBlue)
                        }
                    }
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            InlineDisclaimerNotice()
            InlineDisclaimerNotice(
                text: "AI-generated · verify before acting",
                linkLabel: "Read more"
            )
        }
        .padding()
    }
}
