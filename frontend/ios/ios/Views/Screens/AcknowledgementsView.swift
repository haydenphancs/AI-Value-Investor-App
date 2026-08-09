//
//  AcknowledgementsView.swift
//  ios
//
//  Screen: open-source acknowledgements / licenses.
//
//  ⚠️ ONE ENTRY PER RESOLVED SWIFT PACKAGE. This list held only sentry-cocoa while the app
//  also links GoogleSignIn-iOS and its seven transitive dependencies — all Apache-2.0, whose
//  §4(a) requires the licence to travel with an object-form redistribution. A shipped binary
//  is exactly that, so the eight missing entries were a licence-compliance gap, not a
//  courtesy one. (§4(d) — the NOTICE file — does not apply: none of these checkouts ships one.)
//
//  "Keep in sync when adding a dependency" is what failed here: a transitive package arrives
//  without anyone editing this file. `backend/tests/test_ios_acknowledgements_parity.py`
//  DERIVES the expected set from `Package.resolved`, so a new dependency fails the build
//  instead of relying on the next person reading this comment.
//

import SwiftUI

struct AcknowledgementItem: Identifiable {
    let id = UUID()
    let name: String
    let license: String
    let url: String
}

struct AcknowledgementsView: View {
    // One entry per pin in `frontend/ios/ios.xcodeproj/.../Package.resolved`, derived and
    // asserted by `test_ios_acknowledgements_parity.py`. The seven Google/OpenID packages are
    // transitive dependencies of GoogleSignIn-iOS; they are linked into the binary, so they
    // are attributed here whether or not this app imports them directly.
    private let items: [AcknowledgementItem] = [
        AcknowledgementItem(
            name: "Sentry (sentry-cocoa)",
            license: "MIT License",
            url: "https://github.com/getsentry/sentry-cocoa"
        ),
        AcknowledgementItem(
            name: "GoogleSignIn-iOS",
            license: "Apache License 2.0",
            url: "https://github.com/google/GoogleSignIn-iOS"
        ),
        AcknowledgementItem(
            name: "AppAuth-iOS",
            license: "Apache License 2.0",
            url: "https://github.com/openid/AppAuth-iOS"
        ),
        AcknowledgementItem(
            name: "GTMAppAuth",
            license: "Apache License 2.0",
            url: "https://github.com/google/GTMAppAuth"
        ),
        AcknowledgementItem(
            name: "gtm-session-fetcher",
            license: "Apache License 2.0",
            url: "https://github.com/google/gtm-session-fetcher"
        ),
        AcknowledgementItem(
            name: "GoogleUtilities",
            license: "Apache License 2.0",
            url: "https://github.com/google/GoogleUtilities"
        ),
        AcknowledgementItem(
            name: "Promises",
            license: "Apache License 2.0",
            url: "https://github.com/google/promises"
        ),
        AcknowledgementItem(
            name: "App Check",
            license: "Apache License 2.0",
            url: "https://github.com/google/app-check"
        ),
        AcknowledgementItem(
            name: "interop-ios-for-google-sdks",
            license: "Apache License 2.0",
            url: "https://github.com/google/interop-ios-for-google-sdks"
        ),
    ]

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    Text("Caydex is built with the help of these open-source projects.")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.md)

                    VStack(spacing: 1) {
                        ForEach(items) { item in
                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                Text(item.name)
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textPrimary)
                                Text(item.license)
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                                Text(item.url)
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(AppSpacing.lg)
                            .background(AppColors.cardBackground)
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
                    // Card on the page background: an edge in light, nothing in dark.
                    .cardBorder(cornerRadius: AppCornerRadius.large)
                    .padding(.horizontal, AppSpacing.lg)

                    Spacer().frame(height: AppSpacing.xxxl)
                }
            }
        }
        .navigationTitle("Acknowledgements")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
    }
}

#Preview {
    NavigationStack { AcknowledgementsView() }
}
