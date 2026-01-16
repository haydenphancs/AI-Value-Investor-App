# iOS Architecture Guide

**Version:** 1.0
**Target:** iOS 17+, SwiftUI

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [State Management](#state-management)
3. [Network Layer](#network-layer)
4. [Long-Running Tasks](#long-running-tasks)
5. [Error Handling](#error-handling)
6. [File Organization](#file-organization)
7. [Migration Guide](#migration-guide)

---

## Architecture Overview

### Pattern: MVVM + Repository + Centralized State

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          SwiftUI Views                                   │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│   │  HomeView   │  │ResearchView │  │ TickerView  │  │  LearnView  │   │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│          │                │                │                │           │
│   ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐   │
│   │ HomeVM      │  │ResearchVM   │  │ TickerVM    │  │  LearnVM    │   │
│   │ (local)     │  │ (local)     │  │ (local)     │  │  (local)    │   │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
└──────────┼─────────────────┼────────────────┼────────────────┼──────────┘
           │                 │                │                │
           └─────────────────┴────────────────┴────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Global State (AppState)                             │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│   │  AuthState  │  │  UserState  │  │WatchlistState│ │ResearchState│   │
│   └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘   │
│                                                                          │
│   Shared across ALL views via @Environment(AppState.self)               │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Repositories                                     │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│   │StockRepo    │  │ResearchRepo │  │  NewsRepo   │  │  UserRepo   │   │
│   │ + cache     │  │ + polling   │  │ + cache     │  │             │   │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
└──────────┴─────────────────┴────────────────┴────────────────┴──────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          APIClient                                       │
│   • Type-safe endpoints (APIEndpoint enum)                              │
│   • Auto JSON encoding/decoding                                          │
│   • Auth token injection                                                 │
│   • Retry with backoff                                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                        ┌───────────────────┐
                        │  FastAPI Backend  │
                        └───────────────────┘
```

---

## State Management

### Global vs Local State

| Type | Storage | Example | Access Method |
|------|---------|---------|---------------|
| **Global** | `AppState` | User auth, credits, watchlist | `@Environment(AppState.self)` |
| **Local** | `@StateObject` ViewModel | Search text, selected tab | `@StateObject var viewModel` |
| **UI Only** | `@State` | Sheet visibility, animation | `@State var isPresented` |

### When to Use Global State (AppState)

Use `AppState` when:
- Multiple screens need the same data (user credits, watchlist)
- State must persist across navigation
- Changes should trigger updates everywhere (auth status)

```swift
// In View
struct ResearchView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        if appState.user.canGenerateResearch {
            GenerateButton()
        } else {
            UpgradePrompt(credits: appState.user.remainingCredits)
        }
    }
}
```

### When to Use Local State (ViewModel)

Use `@StateObject` ViewModel when:
- State is specific to one screen
- State doesn't need to persist after leaving screen
- Complex logic that shouldn't be in the View

```swift
// Keep using your existing pattern!
struct TickerDetailView: View {
    @StateObject private var viewModel: TickerDetailViewModel

    init(ticker: String) {
        _viewModel = StateObject(wrappedValue: TickerDetailViewModel(tickerSymbol: ticker))
    }

    var body: some View {
        // Your existing code works as-is
    }
}
```

### Accessing Global State in ViewModels

When a ViewModel needs global state:

```swift
@MainActor
class ResearchViewModel: ObservableObject {
    @Published var searchText = ""
    @Published var isGenerating = false

    // Option 1: Pass AppState reference
    private weak var appState: AppState?

    init(appState: AppState) {
        self.appState = appState
    }

    func generateAnalysis(stockId: String) async {
        // Check credits from global state
        guard appState?.user.canGenerateResearch == true else {
            appState?.handleError(AppError.insufficientCredits(required: 1, available: 0))
            return
        }

        isGenerating = true
        defer { isGenerating = false }

        // ... generate analysis

        // Update global state
        appState?.research.reports.insert(newReport, at: 0)
    }
}
```

---

## Network Layer

### Making API Calls

```swift
// 1. Define endpoint in APIEndpoint.swift
case getStock(ticker: String)

var path: String {
    case .getStock(let ticker):
        return "/api/v1/stocks/\(ticker)"
}

// 2. Call from Repository or ViewModel
let stock = try await apiClient.request(
    endpoint: .getStock(ticker: "AAPL"),
    responseType: StockDetail.self
)
```

### Adding a New Endpoint

1. Add case to `APIEndpoint` enum
2. Implement `path`, `method`, `queryParameters`, `body` as needed
3. Create request/response structs if needed
4. Use in Repository or ViewModel

### Response Models

Response models should match backend JSON with `CodingKeys` for snake_case:

```swift
struct StockDetail: Codable {
    let ticker: String
    let companyName: String  // Maps from company_name
    let marketCap: Double?   // Maps from market_cap

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case marketCap = "market_cap"
    }
}
```

---

## Long-Running Tasks

### Polling for AI Research Generation

The `TaskPollingManager` handles long-running AI tasks:

```swift
class ResearchViewModel: ObservableObject {
    @Published var progress: Int = 0
    @Published var currentStep: String = ""

    private let pollingManager = TaskPollingManager()

    func generateAnalysis(stockId: String, persona: String) {
        Task {
            for try await update in pollingManager.generateAndMonitorResearch(
                stockId: stockId,
                persona: persona
            ) {
                switch update {
                case .started(let reportId):
                    // Show generating UI
                    break

                case .progress(let percent, let step):
                    self.progress = percent
                    self.currentStep = step

                case .completed(let report):
                    // Show completed report
                    break

                case .failed(let error):
                    // Handle error
                    break
                }
            }
        }
    }
}
```

### Flow Diagram

```
┌─────────────────┐
│  User taps      │
│  "Generate"     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     POST /research/generate
│  APIClient      │─────────────────────────────►┌──────────────────┐
│  (initial call) │                              │ Backend creates  │
└────────┬────────┘◄─────────────────────────────│ task, returns ID │
         │           { report_id: "abc123" }     └──────────────────┘
         │
         ▼
┌─────────────────┐
│  Start polling  │
│  every 3 sec    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     GET /research/reports/{id}/status
│  Poll status    │─────────────────────────────►┌──────────────────┐
│                 │◄─────────────────────────────│ { progress: 45,  │
└────────┬────────┘  { status: "processing" }    │   step: "..." }  │
         │                                        └──────────────────┘
         │ (repeat until completed/failed)
         ▼
┌─────────────────┐     GET /research/reports/{id}
│  Fetch full     │─────────────────────────────►┌──────────────────┐
│  report         │◄─────────────────────────────│ Full report JSON │
└─────────────────┘                              └──────────────────┘
```

---

## Error Handling

### Error Types

```swift
enum AppError {
    // Network
    case noConnection
    case timeout
    case serverError(statusCode: Int)

    // Auth
    case unauthorized
    case tokenExpired

    // Business
    case insufficientCredits(required: Int, available: Int)
    case notFound(resource: String)
    case validationFailed(message: String)
}
```

### Handling Errors in ViewModels

```swift
func loadData() {
    Task {
        do {
            let data = try await repository.getData()
            self.items = data
        } catch {
            let appError = AppError.from(error)

            // Show in local UI
            self.errorMessage = appError.message

            // Or report globally (shows toast)
            appState?.handleError(error)
        }
    }
}
```

### Using BaseViewModel (Optional)

For new ViewModels, you can extend `BaseViewModel` for automatic error handling:

```swift
class MyViewModel: BaseViewModel {
    @Published var items: [Item] = []

    override func loadData() {
        performTask {
            self.items = try await self.apiClient.request(...)
        }
        // Automatically handles: isLoading, errorMessage
    }
}
```

---

## File Organization

### Current Structure (Keep)

```
ios/
├── Views/
│   ├── Atoms/          # Small reusable components
│   ├── Molecules/      # Composed components
│   ├── Organisms/      # Complex sections
│   └── Screens/        # Full-screen views
├── ViewModels/         # Screen-specific ViewModels
├── Models/             # Data models
└── Theme/              # Colors, typography, spacing
```

### New Core Structure (Added)

```
ios/
├── Core/
│   ├── State/
│   │   └── AppState.swift       # Global app state
│   ├── Services/
│   │   ├── APIClient.swift      # HTTP client
│   │   ├── APIEndpoint.swift    # Endpoint definitions
│   │   ├── APIConfig.swift      # Configuration
│   │   ├── AuthService.swift    # Auth/token handling
│   │   └── TaskPollingManager.swift  # Long-running tasks
│   ├── Repositories/
│   │   └── StockRepository.swift    # Data access layer
│   ├── ViewModels/
│   │   └── BaseViewModel.swift      # Optional base class
│   └── Utilities/
│       └── AppError.swift           # Error types
└── iosApp.swift                     # Updated entry point
```

---

## Migration Guide

### Your Existing ViewModels Still Work!

The new architecture is **additive** - your existing code works as-is:

```swift
// This still works exactly the same:
struct HomeContentView: View {
    @StateObject private var viewModel = HomeViewModel()

    var body: some View {
        // Your existing code
    }
}
```

### Gradual Migration Path

**Step 1: Access Global State When Needed**

```swift
// Add Environment access to views that need shared state
struct ResearchContentView: View {
    @StateObject private var viewModel = ResearchViewModel()
    @Environment(AppState.self) private var appState  // NEW

    var body: some View {
        // Use appState.user.credits, appState.watchlist, etc.
    }
}
```

**Step 2: Replace Mock Data with API Calls**

```swift
// Before (mock data)
func loadMockData() {
    self.items = MockData.items
}

// After (real API)
func loadData() {
    Task {
        do {
            self.items = try await repository.getItems()
        } catch {
            self.errorMessage = AppError.from(error).message
        }
    }
}
```

**Step 3: Use Repositories for Data Access**

```swift
// Before: Fetch directly in ViewModel
let data = try await apiClient.request(...)

// After: Use Repository (adds caching, abstracts API)
let data = try await stockRepository.getStock(ticker: "AAPL")
```

### Quick Reference: Where to Put Code

| Code Type | Location |
|-----------|----------|
| UI components | `Views/Atoms/`, `Molecules/`, `Organisms/` |
| Full screens | `Views/Screens/` |
| Screen-specific logic | `ViewModels/` |
| Shared data (auth, user) | `Core/State/AppState.swift` |
| API calls | `Core/Services/APIClient.swift` |
| Data fetching + caching | `Core/Repositories/` |
| Error types | `Core/Utilities/AppError.swift` |

---

## Summary

### Key Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Global State | `@Observable` + `@Environment` | Modern, no boilerplate, type-safe |
| Local State | `@StateObject` ViewModels | Your existing pattern, works great |
| Networking | Actor-based `APIClient` | Thread-safe, testable |
| Long Tasks | `AsyncThrowingStream` polling | Native Swift, cancellable |
| Error Handling | `AppError` enum | User-friendly messages, actionable |

### What You Get

1. **No breaking changes** - Existing code works as-is
2. **Shared state** - User credits, auth, watchlist shared everywhere
3. **Real networking** - Type-safe API calls to your FastAPI backend
4. **AI task handling** - Polling manager for long-running research
5. **Error handling** - Consistent, user-friendly error messages
6. **Caching** - Repository layer with built-in caching
