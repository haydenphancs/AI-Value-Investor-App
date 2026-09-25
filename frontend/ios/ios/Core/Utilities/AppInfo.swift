//
//  AppInfo.swift
//  ios
//
//  Build and device facts, in ONE place, plus the support address.
//
//  WHY THIS EXISTS
//  ---------------
//  `appVersion` / `buildNumber` were computed in three places — `Bundle.appVersion` in
//  APIClient (the canonical one, used for the `X-App-Version` header) and byte-identical
//  private copies in `ProfileView` and `AppSettingsView` for the "Caydex v1.0 (1)" footer.
//  The feedback report needs the same facts, which would have made a fourth. It also needs
//  the OS version and the device model, which existed nowhere at all — `UIDevice` appeared
//  in zero files.
//
//  A bug report without a build number and an OS version is usually untriageable, so this
//  is the difference between "we'll fix it as soon as we can" being true and being a slogan.
//

import Foundation
import UIKit

extension Bundle {
    /// The marketing version ("1.0"). `nonisolated` because `APIClient` reads it off the actor
    /// to build the `X-App-Version` header.
    nonisolated var appVersion: String {
        infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
    }

    /// `CFBundleVersion` — the build number, which changes on every TestFlight upload while
    /// `appVersion` stays put. It is the only thing that identifies WHICH build a report came
    /// from, so it matters more than the marketing version for triage.
    nonisolated var buildNumber: String {
        infoDictionary?["CFBundleVersion"] as? String ?? "1"
    }
}

enum AppInfo {

    /// The address users write to. Cloudflare Email Routing carries `support@`, `copyright@`
    /// and `privacy@` only — `feedback@` has NO route and silently bounced every message sent
    /// to it, which is why there is one address here and not one per purpose.
    static let supportEmail = "support@caydexinvest.com"

    static var appVersion: String { Bundle.main.appVersion }
    static var buildNumber: String { Bundle.main.buildNumber }

    /// The App Store's NUMERIC app id (the `id` in `apps.apple.com/app/id123456789`) — the
    /// adamId App Store Connect assigned Caydex's record (documents/legal/LAUNCH_CHECKLIST.md
    /// §7; the same value as `IAP_APP_APPLE_ID` on Railway).
    ///
    /// It used to be a deliberate blank until launch, because the listing 404s until App
    /// Review approves it. But this is COMPILED IN, and the 1.0 binary is built before launch,
    /// so a launch-day flip could only have reached users in a 1.0.1. The pre-launch 404 is
    /// now handled at RUNTIME instead: `InstallSourcePolicy` uses the store links only for an
    /// App Store install, which cannot exist before the listing is live.
    ///
    /// This is a NUMBER, not the bundle id. Digits only.
    static let appStoreAppID = "6759525689"

    /// The App Store product page, or nil if `appStoreAppID` is ever blank. A pure URL
    /// builder — whether it is SAFE to use is `InstallSourcePolicy`'s call.
    ///
    /// One digits-only guard for every App Store link in the app — `reviewURL` and
    /// `downloadURL` both build on this rather than repeating it.
    static var appStoreURL: URL? {
        let id = appStoreAppID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !id.isEmpty, id.allSatisfy(\.isNumber) else { return nil }
        return URL(string: "https://apps.apple.com/app/id\(id)")
    }

    /// Deep link to the App Store review sheet, or nil if `appStoreAppID` is ever blank.
    ///
    /// Only ever OPENED for an App Store install — `InstallSourcePolicy.rateAction` decides.
    ///
    /// Why this exists at all: `requestReview()` is rate-limited by iOS to three prompts per
    /// 365 days and is documented as "may not display". As the ONLY behaviour behind a row the
    /// user deliberately tapped, that makes it a dead tap for most people most of the time —
    /// a button that silently does nothing. `SKStoreReviewController` is for moments the APP
    /// chooses; a user asking to leave a review should be taken there.
    static var reviewURL: URL? {
        guard let base = appStoreURL else { return nil }
        return URL(string: base.absoluteString + "?action=write-review")
    }

    /// The marketing site.
    ///
    /// MEASURED 2026-08-28: `https://caydexinvest.com` answers 200, while `/app`,
    /// `/download`, `/get` and `/ios` all 404 — the root is the only path that resolves, so
    /// do not "tidy" this into a prettier download path without checking it first.
    static let websiteURL = URL(string: "https://caydexinvest.com")!

    /// Where a share tells the recipient to go to get the app.
    ///
    /// The App Store page when THIS copy was installed from the App Store, the website
    /// otherwise. The listing 404s until App Review approves it (measured 2026-08-28), so a
    /// TestFlight or review build must not send recipients there — and an App Store install
    /// proves the listing is live. Nothing changes on launch day: the first App Store install
    /// is the switch.
    static var downloadURL: URL {
        InstallSourcePolicy.downloadURL(for: InstallSourceStore.current,
                                        appStoreURL: appStoreURL,
                                        websiteURL: websiteURL)
    }

    static var osVersion: String {
        "iOS \(UIDevice.current.systemVersion)"
    }

    /// The hardware identifier ("iPhone17,1"), not the marketing name.
    ///
    /// `UIDevice.current.model` is useless for this — it returns the literal string "iPhone"
    /// on every iPhone ever made. The identifier comes from `uname`, except on the Simulator,
    /// where `utsname.machine` is the HOST architecture ("arm64") and the real answer is in an
    /// environment variable. Reporting "arm64" would make every simulator report look like the
    /// same mystery device.
    static var deviceModel: String {
        if let simulated = ProcessInfo.processInfo.environment["SIMULATOR_MODEL_IDENTIFIER"] {
            return "\(simulated) (Simulator)"
        }
        var info = utsname()
        uname(&info)
        let identifier = Mirror(reflecting: info.machine).children.reduce(into: "") { out, part in
            guard let byte = part.value as? Int8, byte != 0 else { return }
            out += String(UnicodeScalar(UInt8(byte)))
        }
        return identifier.isEmpty ? UIDevice.current.model : identifier
    }

    /// The block appended to a support report.
    ///
    /// Deliberately plain text and deliberately **visible in the composer** — the user reads it,
    /// and can edit or delete any line, before anything is sent. Nothing here is collected
    /// silently, and app version / device type are already disclosed in the privacy policy's
    /// "Device information" bullet.
    ///
    /// Takes its state as parameters rather than reaching for `AppState`: this is a `Core`
    /// utility, and `.claude/rules/ios-swiftui.md` keeps global state injected, not looked up.
    static func diagnosticsBlock(tier: String, isSignedIn: Bool) -> String {
        """
        ---
        Please keep the details below — they tell us exactly which build to look at.
        App: Caydex \(appVersion) (\(buildNumber))
        Device: \(deviceModel), \(osVersion)
        Account: \(isSignedIn ? "signed in" : "signed out") · \(tier)
        """
    }
}
