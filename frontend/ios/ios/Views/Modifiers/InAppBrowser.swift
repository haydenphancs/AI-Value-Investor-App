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
func openExternal(_ url: URL, into link: inout BrowserLink?) {
    if SafariView.canOpen(url) {
        link = BrowserLink(url)
    } else {
        // mailto:, tel:, and anything else with a registered system handler.
        UIApplication.shared.open(url)
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
