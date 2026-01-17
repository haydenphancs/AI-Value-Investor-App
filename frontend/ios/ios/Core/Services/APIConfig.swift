//
//  APIConfig.swift
//  ios
//
//  API Configuration
//
//  Handles environment-specific configuration:
//  - Base URL (dev, staging, production)
//  - Timeouts
//  - Retry policies
//

import Foundation

// MARK: - App Environment

enum AppEnvironment: Sendable {
    case development
    case staging
    case production

    nonisolated static var current: AppEnvironment {
        #if DEBUG
        return .development
        #else
        // Check for staging flag or default to production
        if ProcessInfo.processInfo.environment["STAGING"] != nil {
            return .staging
        }
        return .production
        #endif
    }
}

// MARK: - API Configuration

enum APIConfig: Sendable {

    // MARK: - Base URLs

    nonisolated static var baseURL: URL {
        switch AppEnvironment.current {
        case .development:
            // Local development server
            return URL(string: "http://localhost:8000")!
        case .staging:
            // Staging server
            return URL(string: "https://staging-api.yourapp.com")!
        case .production:
            // Production server
            return URL(string: "https://api.yourapp.com")!
        }
    }

    // MARK: - Timeouts

    /// Default request timeout (seconds)
    static let defaultTimeout: TimeInterval = 30

    /// Timeout for AI generation requests (seconds)
    static let aiGenerationTimeout: TimeInterval = 120

    /// Timeout for chat messages (seconds)
    static let chatTimeout: TimeInterval = 60

    // MARK: - Retry Policy

    /// Maximum retry attempts for failed requests
    static let maxRetryAttempts = 3

    /// Base delay between retries (seconds)
    static let retryBaseDelay: TimeInterval = 1.0

    /// Maximum delay between retries (seconds)
    static let retryMaxDelay: TimeInterval = 10.0

    // MARK: - Polling Configuration

    /// Interval for polling research status (seconds)
    static let researchPollInterval: TimeInterval = 3.0

    /// Maximum polling duration before timeout (seconds)
    static let researchPollTimeout: TimeInterval = 180.0 // 3 minutes

    // MARK: - Cache Configuration

    /// Default cache TTL (seconds)
    static let defaultCacheTTL: TimeInterval = 300 // 5 minutes

    /// Stock quote cache TTL (seconds)
    static let quoteCacheTTL: TimeInterval = 60 // 1 minute

    /// News feed cache TTL (seconds)
    static let newsCacheTTL: TimeInterval = 300 // 5 minutes

    // MARK: - Pagination

    /// Default page size for list endpoints
    static let defaultPageSize = 20

    /// Maximum page size allowed
    static let maxPageSize = 100
}

// MARK: - Feature Flags

enum FeatureFlags: Sendable {
    /// Enable offline mode with cached data
    nonisolated static var offlineModeEnabled: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    /// Enable debug logging for API calls
    nonisolated static var apiLoggingEnabled: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    /// Enable mock data for development
    nonisolated static var useMockData: Bool {
        #if DEBUG
        return ProcessInfo.processInfo.environment["USE_MOCK"] != nil
        #else
        return false
        #endif
    }
}
