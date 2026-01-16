//
//  AppError.swift
//  ios
//
//  Unified Error Handling
//
//  Maps backend error codes and network errors to user-friendly messages.
//  Provides suggested actions for recovery.
//

import Foundation

// MARK: - App Error

/// Unified error type for the application.
/// Maps various error sources to user-actionable messages.
enum AppError: Error, Identifiable, Equatable {
    // Network errors
    case noConnection
    case timeout
    case serverError(statusCode: Int)

    // Auth errors
    case unauthorized
    case tokenExpired
    case forbidden

    // Business errors
    case insufficientCredits(required: Int, available: Int)
    case notFound(resource: String)
    case validationFailed(message: String)
    case rateLimited(retryAfter: Int)

    // API errors (from backend)
    case apiError(code: String, message: String)

    // Generic
    case unknown(message: String)

    var id: String {
        switch self {
        case .noConnection: return "no_connection"
        case .timeout: return "timeout"
        case .serverError(let code): return "server_\(code)"
        case .unauthorized: return "unauthorized"
        case .tokenExpired: return "token_expired"
        case .forbidden: return "forbidden"
        case .insufficientCredits: return "insufficient_credits"
        case .notFound(let r): return "not_found_\(r)"
        case .validationFailed: return "validation"
        case .rateLimited: return "rate_limited"
        case .apiError(let code, _): return "api_\(code)"
        case .unknown: return "unknown"
        }
    }

    // MARK: - User-Friendly Messages

    var title: String {
        switch self {
        case .noConnection:
            return "No Connection"
        case .timeout:
            return "Request Timeout"
        case .serverError:
            return "Server Error"
        case .unauthorized, .tokenExpired:
            return "Session Expired"
        case .forbidden:
            return "Access Denied"
        case .insufficientCredits:
            return "Insufficient Credits"
        case .notFound:
            return "Not Found"
        case .validationFailed:
            return "Invalid Input"
        case .rateLimited:
            return "Too Many Requests"
        case .apiError:
            return "Error"
        case .unknown:
            return "Something Went Wrong"
        }
    }

    var message: String {
        switch self {
        case .noConnection:
            return "Please check your internet connection and try again."
        case .timeout:
            return "The request took too long. Please try again."
        case .serverError:
            return "We're experiencing technical difficulties. Please try again later."
        case .unauthorized, .tokenExpired:
            return "Your session has expired. Please sign in again."
        case .forbidden:
            return "You don't have permission to perform this action."
        case .insufficientCredits(let required, let available):
            return "This action requires \(required) credits, but you only have \(available). Upgrade your plan to get more."
        case .notFound(let resource):
            return "The requested \(resource) could not be found."
        case .validationFailed(let msg):
            return msg
        case .rateLimited(let seconds):
            return "Please wait \(seconds) seconds before trying again."
        case .apiError(_, let msg):
            return msg
        case .unknown(let msg):
            return msg.isEmpty ? "An unexpected error occurred. Please try again." : msg
        }
    }

    // MARK: - Suggested Actions

    var suggestedAction: ErrorAction {
        switch self {
        case .noConnection:
            return .waitForConnection
        case .timeout, .serverError:
            return .retry
        case .unauthorized, .tokenExpired:
            return .signIn
        case .forbidden:
            return .goBack
        case .insufficientCredits:
            return .upgrade
        case .notFound:
            return .goBack
        case .validationFailed:
            return .fixInput
        case .rateLimited:
            return .waitAndRetry
        case .apiError, .unknown:
            return .retry
        }
    }

    var isRetryable: Bool {
        switch self {
        case .timeout, .serverError, .rateLimited:
            return true
        default:
            return false
        }
    }

    // MARK: - Factory

    /// Create AppError from any Error
    static func from(_ error: Error) -> AppError {
        // Already an AppError
        if let appError = error as? AppError {
            return appError
        }

        // URLSession errors
        if let urlError = error as? URLError {
            switch urlError.code {
            case .notConnectedToInternet, .networkConnectionLost:
                return .noConnection
            case .timedOut:
                return .timeout
            case .cannotConnectToHost, .cannotFindHost:
                return .serverError(statusCode: 0)
            default:
                return .unknown(message: urlError.localizedDescription)
            }
        }

        // API response errors
        if let apiError = error as? APIError {
            return mapAPIError(apiError)
        }

        return .unknown(message: error.localizedDescription)
    }

    private static func mapAPIError(_ error: APIError) -> AppError {
        switch error {
        case .unauthorized:
            return .unauthorized
        case .forbidden:
            return .forbidden
        case .notFound:
            return .notFound(resource: "item")
        case .serverError(let code):
            return .serverError(statusCode: code)
        case .rateLimited(let retryAfter):
            return .rateLimited(retryAfter: retryAfter)
        case .businessError(let code, let message):
            // Map backend error codes
            if code.starts(with: "BIZ_2001") || code.starts(with: "BIZ_2002") {
                return .insufficientCredits(required: 1, available: 0)
            }
            return .apiError(code: code, message: message)
        case .decodingError:
            return .unknown(message: "Failed to process server response")
        case .networkError(let underlying):
            return .from(underlying)
        case .unknown(let message):
            return .unknown(message: message)
        }
    }
}

// MARK: - Error Action

enum ErrorAction {
    case retry
    case waitAndRetry
    case waitForConnection
    case signIn
    case goBack
    case fixInput
    case upgrade
    case contactSupport

    var buttonTitle: String {
        switch self {
        case .retry:
            return "Try Again"
        case .waitAndRetry:
            return "Wait"
        case .waitForConnection:
            return "OK"
        case .signIn:
            return "Sign In"
        case .goBack:
            return "Go Back"
        case .fixInput:
            return "OK"
        case .upgrade:
            return "Upgrade"
        case .contactSupport:
            return "Contact Support"
        }
    }
}

// MARK: - API Error (Network Layer)

/// Low-level API errors before mapping to AppError
enum APIError: Error {
    case unauthorized
    case forbidden
    case notFound
    case serverError(statusCode: Int)
    case rateLimited(retryAfter: Int)
    case businessError(code: String, message: String)
    case decodingError(Error)
    case networkError(Error)
    case unknown(message: String)
}
