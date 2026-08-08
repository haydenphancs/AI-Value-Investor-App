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
