//
//  MonitoringConfig.swift
//  ios
//
//  Error monitoring (Sentry) bootstrap — mirrors the backend's posture in
//  backend/app/main.py: inert unless a DSN is set, environment-tagged, no PII,
//  errors-only (no performance tracing).
//
//  ⚠️ The Sentry-using code below is wrapped in `#if canImport(Sentry)`. Until
//  the Sentry Cocoa SPM package is added to the project, `canImport(Sentry)`
//  is false and this whole file compiles to a NO-OP — the app keeps building
//  unchanged. Once the package is added AND `sentryDSN` is filled in, error
//  capture activates. Add the package in Xcode:
//      File > Add Package Dependencies… > https://github.com/getsentry/sentry-cocoa
//      Dependency Rule: Up to Next Major Version, from 8.0.0
//

import Foundation
#if canImport(Sentry)
import Sentry
#endif

enum MonitoringConfig {

    /// Sentry **client DSN** for the iOS project
    /// (Sentry → Settings → Projects → <ios project> → Client Keys (DSN)).
    ///
    /// Unlike the backend DSN (a Railway secret), a mobile client DSN is
    /// embedded in the shipped binary and is public by design: it can ONLY
    /// submit events, never read them, so it is safe to commit. This is NOT the
    /// `SENTRY_AUTH_TOKEN` used for dSYM upload — that one IS a secret and must
    /// never be committed.
    ///
    /// Leave empty to keep monitoring fully inert (mirrors the backend's
    /// `SENTRY_DSN`-guarded no-op for local dev).
    static let sentryDSN = "https://c60b2c2d9699835e1a13b88b8227e9b8@o4511685157715968.ingest.us.sentry.io/4511702900604928"   // ← paste the iOS project DSN here

    /// Maps the app's build environment to a Sentry `environment` tag so dev
    /// noise never mixes with production issues (parallels backend ENVIRONMENT).
    static var environmentName: String {
        switch AppEnvironment.current {
        case .development: return "development"
        case .staging:     return "staging"
        case .production:  return "production"
        }
    }

    // MARK: - Redaction

    /// Patterns with no diagnostic value that must never leave the device.
    /// Mirrors `backend/app/log_redaction.py`; keep the two in sync.
    ///
    /// Account UUIDs are deliberately NOT redacted — they are pseudonymous and are the
    /// primary handle for correlating a crash with a backend trace. They are disclosed
    /// in the privacy policy instead.
    private static let redactionRules: [(NSRegularExpression, String)] = {
        let patterns: [(String, String)] = [
            // Secret query params: ?apikey=… &token=… &password=…
            (#"(?i)([?&](?:api[_-]?key|token|access[_-]?token|secret|password|key)=)[^&\s'"]+"#, "$1***"),
            // Bearer tokens
            (#"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{20,}"#, "$1***"),
            // Bare JWTs
            (#"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"#, "***"),
            // Email addresses
            (#"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b"#, "***@***"),
        ]
        return patterns.compactMap { pattern, template in
            guard let re = try? NSRegularExpression(pattern: pattern) else { return nil }
            return (re, template)
        }
    }()

    /// Apply every redaction rule to a free-text string.
    static func redact(_ text: String) -> String {
        var out = text
        for (re, template) in redactionRules {
            out = re.stringByReplacingMatches(
                in: out,
                range: NSRange(out.startIndex..., in: out),
                withTemplate: template
            )
        }
        return out
    }
}

/// Starts error monitoring as early as possible in the app lifecycle. Safe to
/// call unconditionally: it is a no-op when the Sentry package is absent OR the
/// DSN is empty, so it never affects local dev or an un-provisioned build.
/// True only in a Debug configuration. Deliberately a runtime constant rather than an
/// `#if DEBUG … #else … #endif` wrapped around the whole function body.
///
/// The `#else` form works, but it excludes the ENTIRE Sentry integration from Debug
/// compilation — and `sentry-cocoa` is a real linked SPM dependency, so `SentrySDK.start`,
/// the `beforeSend` closure and `event.exceptions` would stop being type-checked on every
/// dev build. A sentry-cocoa API change would then first surface at archive/TestFlight time,
/// which is the worst possible moment to discover it, and nothing in CI compiles Release
/// Swift. A `Bool` the optimizer folds keeps the code compiled in both configurations while
/// producing the same Release codegen.
#if DEBUG
private let isDebugBuild = true
#else
private let isDebugBuild = false
#endif

func startErrorMonitoring() {
    // ⛔️ NEVER report from a Debug build.
    //
    // The DSN points at the PRODUCTION `caydex-apple-ios` project, and the Discord alert is
    // "A new issue is created" on ALL environments — so every simulator run pinged Discord and
    // filed an issue. An `environment=development` TAG was not enough: the events still land in
    // the production project, still fire the alert, still burn quota, and still have to be
    // triaged by a human who does not yet know they are noise.
    //
    // Measured 2026-08-07: 23 unresolved issues in 24h, essentially all of them local. Two
    // examples of why Debug capture can only ever be noise:
    //
    //   * "Fatal App Hang" whose main thread is `_swift_getGenericMetadata` under
    //     `LockingConcurrentMap` inside `ViewLayoutEngine.explicitAlignment` — Debug builds do
    //     not pre-specialize generics, so the first SwiftUI layout pass instantiates metadata
    //     at runtime. On a simulator that alone exceeds the 2s watchdog. It cannot happen in
    //     an optimized Release build on real hardware.
    //   * `ThemeContrastAudit.swift` "Fatal error: Theme contrast regression" — that audit is
    //     `#if DEBUG` ONLY and calls `assertionFailure`, which traps under `-Onone` and is
    //     compiled out at `-O`, so it is doubly unreachable for a user. It filed 5 production
    //     issues. (The runtime prints "Fatal error:" for a trapped `assertionFailure`; the
    //     source really does say `assertionFailure`, at `ThemeContrastAudit.swift:172`.)
    //
    // In Debug you already have Xcode, the console and the debugger; Sentry adds nothing.
    // Gating on the build CONFIGURATION (rather than the backend's `ENVIRONMENT ==
    // "production"`) keeps BOTH TestFlight and App Store builds reporting, since those are
    // Release-configured — which is where real users are, and the only place a hang means
    // anything.
    guard !isDebugBuild else {
        #if DEBUG
        print("🟡 [Sentry] Debug build — error monitoring intentionally disabled (see MonitoringConfig)")
        #endif
        return
    }

    #if canImport(Sentry)
    guard !MonitoringConfig.sentryDSN.isEmpty else {
        return
    }

    SentrySDK.start { options in
        options.dsn = MonitoringConfig.sentryDSN
        options.environment = MonitoringConfig.environmentName

        // Fintech app: never ship user PII (mirrors backend send_default_pii=False).
        options.sendDefaultPii = false

        // Errors only — no performance tracing (mirrors backend
        // SENTRY_TRACES_SAMPLE_RATE = 0.0; keeps event volume and cost low).
        options.tracesSampleRate = 0.0

        // Attach a stack trace to captured messages/errors so crashes symbolicate
        // (needs dSYMs uploaded for Release/TestFlight builds — see the dSYM step).
        options.attachStacktrace = true

        // Redact on the way out. This previously scrubbed ONLY the Authorization
        // header, which left the real exposure open: breadcrumbs, exception messages
        // and log messages are free text, so a crash inside chat or auth code could
        // carry the user's typed message or their email address off-device. Mirrors
        // backend/app/log_redaction.py.
        options.beforeSend = { event in
            if event.request?.headers?["Authorization"] != nil {
                event.request?.headers?["Authorization"] = "[redacted]"
            }
            if let url = event.request?.url {
                event.request?.url = MonitoringConfig.redact(url)
            }

            // `SentryMessage.formatted` is get-only, so swap the whole object.
            if let formatted = event.message?.formatted {
                event.message = SentryMessage(formatted: MonitoringConfig.redact(formatted))
            }

            for exception in event.exceptions ?? [] {
                if let value = exception.value {
                    exception.value = MonitoringConfig.redact(value)
                }
            }

            for crumb in event.breadcrumbs ?? [] {
                if let message = crumb.message {
                    crumb.message = MonitoringConfig.redact(message)
                }
                // Breadcrumb `data` is an arbitrary dictionary — most commonly a network
                // breadcrumb's url/body. Redact string values; drop anything else, since
                // we can't inspect it safely.
                if let data = crumb.data {
                    crumb.data = data.mapValues { value -> Any in
                        (value as? String).map(MonitoringConfig.redact) ?? value
                    }
                }
            }

            return event
        }
    }

    #else
    // Sentry package not yet linked — nothing to start. Kept as a no-op so callers never
    // need a compile guard. (`sentry-cocoa` IS linked today, so this branch is not taken;
    // it exists so removing the package cannot break the build.)
    #endif
}
