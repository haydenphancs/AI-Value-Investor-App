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
func startErrorMonitoring() {
    #if canImport(Sentry)
    guard !MonitoringConfig.sentryDSN.isEmpty else {
        #if DEBUG
        print("🟡 [Sentry] DSN empty — error monitoring inert")
        #endif
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

    #if DEBUG
    print("🟢 [Sentry] error monitoring enabled (environment=\(MonitoringConfig.environmentName))")
    #endif
    #else
    // Sentry package not yet linked — nothing to start (expected until the SPM
    // package is added). Kept as a no-op so callers never need a compile guard.
    #if DEBUG
    print("🟡 [Sentry] package not linked — error monitoring unavailable")
    #endif
    #endif
}
