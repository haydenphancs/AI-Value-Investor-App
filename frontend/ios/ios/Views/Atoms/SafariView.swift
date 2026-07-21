//
//  SafariView.swift
//  ios
//
//  Atom: `SFSafariViewController` wrapped for SwiftUI.
//
//  Used for opening a publisher's article from the news feed. The alternative,
//  `UIApplication.shared.open(url)`, EJECTS the user into Safari — they lose the
//  app, and coming back is an app-switch rather than a Done button. This keeps
//  them in Caydex.
//
//  What this does NOT do is get past a publisher's paywall. Most financial
//  publishers (MarketWatch, WSJ, Barron's, Bloomberg) gate their articles, so
//  "Read full story" lands on a register/subscribe wall. That is the
//  publisher's access control and is theirs to enforce — the app links to the
//  original and nothing more. Safari's own Reader button stays available to the
//  user for pages that do serve their text; we do not force it on.
//

import SwiftUI
import SafariServices

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    /// Whether `SFSafariViewController` can present this URL at all.
    ///
    /// It handles **http and https only**. Handing it a `mailto:` or `tel:` URL
    /// is a runtime failure, and the app has real `mailto:` links (Profile →
    /// Send Feedback / Support). This is the single guard for that: everything
    /// routes through `openExternal(_:into:)`, which calls this and falls back
    /// to `UIApplication.shared.open` for anything else.
    static func canOpen(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return false }
        return scheme == "http" || scheme == "https"
    }

    func makeUIViewController(context: Context) -> SFSafariViewController {
        let config = SFSafariViewController.Configuration()
        // The bar-collapsing behaviour reads as broken inside a cover — the
        // close button scrolls away and the user is stuck.
        config.barCollapsingEnabled = false

        let controller = SFSafariViewController(url: url, configuration: config)
        // Brand accent on the controls only. `preferredBarTintColor` is
        // deliberately NOT set: forcing the app's dark background onto the bars
        // fights a light article page, and the reference apps (Webull,
        // Robinhood) let Safari adapt its chrome to the page instead.
        controller.preferredControlTintColor = UIColor(AppColors.primaryBlue)
        controller.dismissButtonStyle = .close
        return controller
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}
