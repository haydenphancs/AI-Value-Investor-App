//
//  BaseViewModel.swift
//  ios
//
//  Base ViewModel Protocol and Helpers
//
//  This provides common patterns for ViewModels:
//  - Loading state management
//  - Error handling
//  - AppState access
//  - Async task management
//
//  Two options for adoption:
//  1. Protocol conformance (recommended for existing code)
//  2. Class inheritance (for new ViewModels)
//

import Foundation
import Combine
import SwiftUI

// MARK: - ViewModel Protocol

/// Protocol that all ViewModels should conform to for consistency.
/// Provides a standard interface for loading, error, and refresh handling.
@MainActor
protocol ViewModelProtocol: ObservableObject {
    /// Whether the ViewModel is currently loading data
    var isLoading: Bool { get set }

    /// Current error message (nil if no error)
    var errorMessage: String? { get set }

    /// Load initial data
    func loadData()

    /// Refresh data (for pull-to-refresh)
    func refresh() async
}

// MARK: - Base ViewModel Class (Optional Inheritance)

/// Base class for ViewModels that need AppState access.
///
/// Usage:
/// ```swift
/// class MyViewModel: BaseViewModel {
///     @Published var items: [Item] = []
///
///     override func loadData() {
///         performTask {
///             self.items = try await self.apiClient.request(...)
///         }
///     }
/// }
/// ```
@MainActor
class BaseViewModel: ObservableObject {

    // MARK: - Published Properties

    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published private(set) var loadingTasks: Set<String> = []

    // MARK: - Dependencies

    /// Reference to global app state
    weak var appState: AppState?

    /// API client for network requests
    var apiClient: APIClient {
        appState?.apiClient ?? APIClient.shared
    }

    // MARK: - Task Management

    /// Currently running tasks (for cancellation)
    private var tasks: [String: Task<Void, Never>] = [:]

    // MARK: - Initialization

    init(appState: AppState? = nil) {
        self.appState = appState
    }

    // MARK: - Loading State

    /// Check if any task is currently loading
    var hasActiveTask: Bool {
        !loadingTasks.isEmpty
    }

    /// Start a named loading task
    func startLoading(_ taskName: String = "default") {
        loadingTasks.insert(taskName)
        isLoading = true
    }

    /// Complete a named loading task
    func stopLoading(_ taskName: String = "default") {
        loadingTasks.remove(taskName)
        isLoading = !loadingTasks.isEmpty
    }

    // MARK: - Error Handling

    /// Set error message and optionally report to AppState
    func setError(_ error: Error, reportGlobally: Bool = false) {
        let appError = AppError.from(error)
        errorMessage = appError.message

        if reportGlobally {
            appState?.handleError(error)
        }
    }

    /// Clear error message
    func clearError() {
        errorMessage = nil
    }

    // MARK: - Async Task Helpers

    /// Perform an async task with automatic loading state and error handling.
    ///
    /// - Parameters:
    ///   - taskName: Unique name for this task (for tracking multiple concurrent tasks)
    ///   - showLoading: Whether to show loading indicator
    ///   - reportError: Whether to set errorMessage on failure
    ///   - operation: The async operation to perform
    func performTask(
        _ taskName: String = "default",
        showLoading: Bool = true,
        reportError: Bool = true,
        operation: @escaping () async throws -> Void
    ) {
        // Cancel existing task with same name
        tasks[taskName]?.cancel()

        tasks[taskName] = Task {
            if showLoading {
                startLoading(taskName)
            }

            clearError()

            do {
                try await operation()
            } catch is CancellationError {
                // Task was cancelled, ignore
            } catch {
                if reportError {
                    setError(error)
                }
            }

            if showLoading {
                stopLoading(taskName)
            }

            tasks.removeValue(forKey: taskName)
        }
    }

    /// Perform a task that returns a value
    func performTask<T>(
        _ taskName: String = "default",
        showLoading: Bool = true,
        reportError: Bool = true,
        operation: @escaping () async throws -> T
    ) async -> T? {
        if showLoading {
            startLoading(taskName)
        }

        clearError()

        defer {
            if showLoading {
                stopLoading(taskName)
            }
        }

        do {
            return try await operation()
        } catch is CancellationError {
            return nil
        } catch {
            if reportError {
                setError(error)
            }
            return nil
        }
    }

    /// Cancel a specific task
    func cancelTask(_ taskName: String) {
        tasks[taskName]?.cancel()
        tasks.removeValue(forKey: taskName)
        stopLoading(taskName)
    }

    /// Cancel all tasks
    func cancelAllTasks() {
        for (name, task) in tasks {
            task.cancel()
            stopLoading(name)
        }
        tasks.removeAll()
    }

    // MARK: - Override Points

    /// Override to load initial data
    func loadData() {
        // Override in subclass
    }

    /// Override for pull-to-refresh
    func refresh() async {
        // Default implementation reloads data
        loadData()
    }

    // MARK: - Cleanup

    deinit {
        // Cancel all tasks when ViewModel is deallocated
        for task in tasks.values {
            task.cancel()
        }
    }
}

// MARK: - View Extensions

/// View modifier for attaching a ViewModel to a View
struct ViewModelModifier<VM: ObservableObject>: ViewModifier {
    @StateObject var viewModel: VM

    func body(content: Content) -> some View {
        content
            .environmentObject(viewModel)
    }
}

extension View {
    /// Attach a ViewModel to a View
    func viewModel<VM: ObservableObject>(_ viewModel: @autoclosure @escaping () -> VM) -> some View {
        modifier(ViewModelModifier(viewModel: viewModel()))
    }
}

// MARK: - Loading State Helpers

/// Represents a loadable resource with loading/success/error states
enum LoadingState<T> {
    case idle
    case loading
    case success(T)
    case failure(AppError)

    var isLoading: Bool {
        if case .loading = self { return true }
        return false
    }

    var value: T? {
        if case .success(let value) = self { return value }
        return nil
    }

    var error: AppError? {
        if case .failure(let error) = self { return error }
        return nil
    }
}

// MARK: - Refreshable Protocol

/// Protocol for ViewModels that support refresh
@MainActor
protocol Refreshable {
    func refresh() async
}
