//
//  InAppBrowser.swift
//  ios
//
//  Presents an external link INSIDE the app (`SafariView`) rather than handing
//  it to Safari with `UIApplication.shared.open`, which ejects the user out of
//  Caydex entirely — returning is an app-switch, not a close button. This is the
//  behaviour Webull / Robinhood ship for article links.
//
//  Shape mirrors `.aiChatCover(isPresented:viewModel:)` in AIChatScreen.swift so
//  a screen opts in with one line.
//
//  Split of responsibilities, because ViewModels cannot present views:
//    ViewModel  →  publishes `browserLink`  (via `openExternal`)
//    Screen     →  `.inAppBrowser(link: $viewModel.browserLink)`
//

import SwiftUI

/// One link to show in the in-app browser.
///
/// A wrapper rather than `extension URL: Identifiable`: retroactive conformance
/// on a Foundation type warns under Swift 6 and would leak app-wide from here.
struct BrowserLink: Identifiable {
    let id = UUID()
    let url: URL

    init(_ url: URL) {
        self.url = url
    }
}

/// Route an external URL to the right place.
///
/// **The one place that decides in-app vs. hand-off.** `SFSafariViewController`
/// accepts http/https only, so `mailto:` (Profile → Send Feedback / Support) and
/// `tel:` must still go to the system — presenting those in a Safari view is a
/// runtime failure. Keeping the branch here means no call site has to remember.
@MainActor
func openExternal(_ url: URL, into link: inout BrowserLink?, action: String = "open that link") {
    if SafariView.canOpen(url) {
        link = BrowserLink(url)
    } else {
        // mailto:, tel:, and anything else with a registered system handler.
        openInSystem(url, action: action)
    }
}

/// Hand a URL to the system, and **report it when nothing can open it**.
///
/// Use this instead of a bare `UIApplication.shared.open(url)` anywhere in the app.
///
/// `open(_:)` without a `completionHandler:` throws its result away, and that silence was a real
/// user-facing bug rather than a tidiness issue: `Profile → Help & Support` and `Help Us Improve`
/// are `mailto:` links, **the iOS Simulator ships no Mail app**, so both rows did nothing at all
/// — no UI, no log — for the entire life of the file. `Manage Subscription` had the same shape.
/// A device with no mail account configured fails identically.
///
/// `.claude/rules/auth.md` §6: a user-initiated action must never fail silently.
///
/// - Parameters:
///   - action: an infinitive phrase completing "Couldn't …", e.g. "open your email app".
///   - onFailure: optional extra handling for callers that can degrade *in place* — e.g. the
///     Support screen reveals a copyable address, which beats a toast when the address is the
///     thing the user wanted. The toast still fires; this is additive.
@MainActor
func openInSystem(
    _ url: URL,
    action: String = "open that link",
    onFailure: (@MainActor () -> Void)? = nil
) {
    UIApplication.shared.open(url, options: [:]) { success in
        guard !success else { return }
        // `open`'s completion is delivered on the main queue, but hop explicitly so this is a
        // guarantee of ours rather than an assumption about UIKit — both callees are MainActor.
        Task { @MainActor in
            AppActions.shared.reportMutationFailure(
                AppError.noAppToOpenURL(what: humanTarget(of: url)),
                action: action
            )
            onFailure?()
        }
    }
}

/// Human phrase for what a URL needs, so the failure message names the missing app rather than
/// echoing a URL at the user ("This device has no app set up to open **email**.").
private func humanTarget(of url: URL) -> String {
    switch url.scheme?.lowercased() {
    case "mailto":              return "email"
    case "tel", "telprompt":    return "phone calls"
    case "sms":                 return "text messages"
    case "itms-apps", "itms-appss": return "the App Store"
    default:
        // App Store deep links are plain https, so the scheme alone cannot identify them.
        if let host = url.host?.lowercased(),
           host == "apps.apple.com" || host.hasSuffix(".apple.com") {
            return "the App Store"
        }
        return "links like that"
    }
}

// MARK: - View modifier

private struct InAppBrowserModifier: ViewModifier {
    @Binding var link: BrowserLink?

    func body(content: Content) -> some View {
        content
            // `item:` rather than `isPresented:` — the URL and the presentation
            // are one piece of state, so they cannot disagree (present with a
            // stale URL, or hold a URL after dismissal).
            .fullScreenCover(item: $link) { target in
                SafariView(url: target.url)
                    .ignoresSafeArea()
            }
    }
}

extension View {
    /// Present external links in an in-app browser. Set `link` (normally via
    /// `openExternal(_:into:)`) to open; the cover clears it on dismiss.
    func inAppBrowser(link: Binding<BrowserLink?>) -> some View {
        modifier(InAppBrowserModifier(link: link))
    }
}
