//
//  InstallSourcePolicy.swift
//  ios
//
//  Where THIS copy of the app came from — the App Store, or a pre-release channel
//  (TestFlight / App Review / Xcode) — and what that means for the two things that link to
//  the App Store: the "Rate the App" row and the download link every share carries.
//
//  ⚠️ FOUNDATION ONLY — no `import StoreKit`, `SwiftUI` or `UIKit`. There is no XCTest target,
//  so the only way to EXECUTE this logic is to pipe this file into `xcrun swift -` from pytest
//  (`backend/tests/test_ios_install_source_rate_the_app.py`, the mechanism
//  `WeeklyQuotePicker` uses). The StoreKit half — reading `AppTransaction` and the receipt —
//  lives in `InstallSourceStore`, which hands this file plain values.
//
//  WHY THIS EXISTS (TestFlight 1.0 (3) and 1.0 (6)): "Rate the App" did nothing on tap. The
//  App Store id was a deliberate blank until launch, so the row fell back to
//  `requestReview()`, which iOS never displays for a TestFlight install. The plan was to fill
//  the id in on launch day — but the id is COMPILED IN, and the 1.0 binary App Review approves
//  is built before launch, so that flip could only reach users in a 1.0.1. Every 1.0 App Store
//  user would have had the silent row, and every share would have linked the website.
//
//  So the id is set, and the decision moved to RUNTIME: an App Store install can only exist
//  once the listing is live, which makes "installed from the App Store" the exact condition
//  for the store links to resolve. Pre-release installs (the listing may 404, and
//  `requestReview()` is inert there) get the website and an honest explanation instead.
//

import Foundation

/// Where this copy of the app was installed from. `nil` wherever it appears means UNKNOWN —
/// neither StoreKit nor the receipt could say.
enum InstallSource: Equatable {
    /// Installed from the live App Store listing.
    case appStore
    /// TestFlight or App Review. StoreKit calls both "sandbox" and does not tell them apart.
    case preRelease
    /// Run from Xcode.
    case development
}

/// `AppStore.Environment`, restated without StoreKit so this file stays runnable standalone.
/// `unrecognised` is an environment StoreKit reported that this build does not know — it
/// carries no information, so the receipt decides.
enum StoreEnvironment: Equatable {
    case production
    case sandbox
    case xcode
    case unrecognised
}

/// What tapping "Rate the App" does.
enum RateAction: Equatable {
    /// Deep link to the App Store's write-review sheet.
    case openReview(URL)
    /// Say why rating is not possible from this build, and offer feedback instead.
    case explainPreRelease
    /// `requestReview()` — reached only if the App Store id is blank again.
    case systemPrompt
}

enum InstallSourcePolicy {

    /// The receipt file names StoreKit writes. Compared EXACTLY: the file name is a fixed
    /// system string, and a fuzzy match would turn an unexpected value into a confident answer.
    static let sandboxReceiptName = "sandboxReceipt"
    static let productionReceiptName = "receipt"

    /// Where this copy came from, or nil when nothing can say.
    ///
    /// A recognised StoreKit environment wins: `AppTransaction` is signed by the App Store,
    /// the receipt path is just a file name. The receipt is the FALLBACK because
    /// `AppTransaction.shared` has been seen to throw on TestFlight installs (and needs the
    /// network on first use), and a TestFlight install that falls through to "unknown" would
    /// be sent to a store page that does not exist yet.
    static func classify(store: StoreEnvironment?, receiptFileName: String?) -> InstallSource? {
        switch store {
        case .production?: return .appStore
        case .sandbox?:    return .preRelease
        case .xcode?:      return .development
        case .unrecognised?, nil: break
        }
        switch receiptFileName {
        case sandboxReceiptName?:    return .preRelease
        case productionReceiptName?: return .appStore
        default:                     return nil
        }
    }

    /// The link a share gives its recipient.
    ///
    /// The App Store page ONLY for a known App Store install — the one case where the page is
    /// certain to exist. Everything else, UNKNOWN included, gets the website: the recipient is
    /// a third party, a dead link is the worst thing we can send them, and the site always
    /// resolves.
    static func downloadURL(for source: InstallSource?, appStoreURL: URL?, websiteURL: URL) -> URL {
        guard source == .appStore, let appStoreURL else { return websiteURL }
        return appStoreURL
    }

    /// What the "Rate the App" row does.
    ///
    /// UNKNOWN takes the review link, unlike `downloadURL`: the person tapping IS the user, an
    /// App Store error page is at least a visible answer, and telling a real App Store
    /// customer "this is a pre-release build" would be false. With the receipt fallback,
    /// unknown is rare.
    static func rateAction(for source: InstallSource?, reviewURL: URL?) -> RateAction {
        switch source {
        case .preRelease?, .development?:
            return .explainPreRelease
        case .appStore?, nil:
            guard let reviewURL else { return .systemPrompt }
            return .openReview(reviewURL)
        }
    }

    /// Parses the DEBUG-only `CAYDEX_INSTALL_SOURCE` override. The outer optional is "was the
    /// value understood"; the inner is the source, where `"unknown"` is a real answer (nil).
    static func parseOverride(_ raw: String) -> InstallSource?? {
        switch raw {
        case "appStore":    return .some(.appStore)
        case "preRelease":  return .some(.preRelease)
        case "development": return .some(.development)
        case "unknown":     return .some(nil)
        default:            return nil
        }
    }
}
