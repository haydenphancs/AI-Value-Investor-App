//
//  PaywallSignInGate.swift
//  ios
//
//  Molecule: replaces the purchase CTA when the caller is signed out.
//
//  THE GATE HAS TO BE IN FRONT OF THE STOREKIT SHEET, NOT BEHIND IT. `verifyPurchase` is
//  `.signInRequired`, so `APIClient` refuses the call — but only AFTER Apple has already
//  charged the card. The subscription survives that (it is restorable, unlike a
//  consumable), but the user is left charged with no entitlement and no explanation, and
//  guests reach this sheet constantly: the Updates chip, the Home signals lock, both whale
//  limits and the narration lock all open it.
//
//  It gates the CTA, NOT the plan list. `GET /billing/plans` is public precisely so the
//  paywall renders for guests, and hiding what a plan includes from the people deciding
//  whether to make an account is both worse selling and worse for the "significant content
//  without a login" posture the rest of the app is built around. `BuyCreditsView` used to do
//  the opposite and hide its packs entirely; it now follows this rule too.
//
//  Takes a closure rather than reaching for `@Environment` so it stays a Molecule.
//

import SwiftUI

struct PaywallSignInGate: View {
    let onSignIn: () -> Void

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            Button(action: onSignIn) {
                Text("Sign in to subscribe")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textOnAccent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.primaryFill)
                    )
            }
            .buttonStyle(PlainButtonStyle())

            Text("Subscriptions are tied to your account, so they follow you across devices.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#Preview {
    PaywallSignInGate(onSignIn: {})
        .padding()
        .background(AppColors.background)
}
