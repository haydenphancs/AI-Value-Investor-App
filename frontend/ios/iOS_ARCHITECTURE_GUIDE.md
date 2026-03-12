# iOS Architecture Guide — AI Value Investor

**Version:** 2.0
**Last Updated:** March 2026
**Target:** iOS 17+, SwiftUI, Swift 5.9+
**Dependencies:** Zero third-party (Apple frameworks only)

---

## Table of Contents

1. [App Overview](#app-overview)
2. [Architecture Overview](#architecture-overview)
3. [Project Structure](#project-structure)
4. [Navigation & Routing](#navigation--routing)
5. [State Management](#state-management)
6. [Network Layer](#network-layer)
7. [Authentication & Token Management](#authentication--token-management)
8. [Long-Running Tasks](#long-running-tasks)
9. [Error Handling](#error-handling)
10. [Data Models](#data-models)
11. [ViewModels](#viewmodels)
12. [UI Component System (Atomic Design)](#ui-component-system-atomic-design)
13. [Theme & Styling](#theme--styling)
14. [Services](#services)
15. [API Endpoints Reference](#api-endpoints-reference)
16. [Backend Context](#backend-context)
17. [Data Persistence](#data-persistence)
18. [Swift Language Patterns](#swift-language-patterns)
19. [Developer Guide](#developer-guide)
20. [Migration Guide](#migration-guide)

---

## App Overview

AI Value Investor is a sophisticated investment research and portfolio tracking app that enables users to:

- **Generate AI-powered stock analysis** in the styles of famous investors (Buffett, Lynch, etc.)
- **Track portfolios** with live price data and diversification metrics
- **Read live financial news** with sentiment analysis
- **Chat with an AI investment advisor**
- **Learn investing** through gamified education with audio content
- **Monitor whale activity** (institutional investor positions)
- **Analyze stocks deeply** (fundamentals, earnings, growth, health, technicals)

### Key Stats

| Metric | Count |
|--------|-------|
| Total Swift files | ~535 |
| View Atoms | 128 |
| View Molecules | 183 |
| View Organisms | 102 |
| Screens | 37 |
| ViewModels | 19 |
| Data Models | 38 |
| Core Infrastructure | 11 |

---

## Architecture Overview

### Pattern: MVVM + Repository + Centralized State

```
┌─────────────────────────────────────────────────────────────────────┐
│                       iOS App (SwiftUI)                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Views (Atomic Design)          ViewModels (MVVM)                  │
│  ├─ Atoms (128)                ├─ Screen VMs (19)                  │
│  ├─ Molecules (183)            ├─ BaseViewModel                    │
│  ├─ Organisms (102)            └─ Local @Published state           │
│  └─ Screens (37)                                                   │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  AppState (Global @Observable)                                     │
│  ├─ AuthState      ├─ UserState                                    │
│  ├─ WatchlistState └─ ResearchState                                │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Services Layer                                                    │
│  ├─ APIClient (Actor)          ├─ AuthService + Keychain           │
│  ├─ TaskPollingManager         ├─ AudioManager                     │
│  ├─ AIVoiceManager             └─ StockRepository (caching)        │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Networking (URLSession) → HTTP/REST → FastAPI Backend             │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
          ┌────────────────────────────────────┐
          │  FastAPI Backend (Python)          │
          │  • Supabase (PostgreSQL)           │
          │  • Google Gemini API (AI)          │
          │  • FMP API (Financial Data)        │
          └────────────────────────────────────┘
```

### Data Flow (Stock Detail Example)

```
TickerDetailView
    ↓ @StateObject
TickerDetailViewModel
    ↓ async/await
APIClient.request(.getStock(ticker:))
    ↓ URLSession
FastAPI /api/v1/stocks/{ticker}
    ↓ JSON
Decode → TickerDetail model
    ↓ @Published
View reactively updates
```

---

## Project Structure

### Complete Directory Layout

```
frontend/ios/
├── ios/                                      # Main app code
│   ├── iosApp.swift                          # App entry point
│   │
│   ├── Core/                                 # Core infrastructure
│   │   ├── State/
│   │   │   └── AppState.swift                # Global app state (@Observable)
│   │   ├── Services/
│   │   │   ├── APIClient.swift               # HTTP client (actor-based)
│   │   │   ├── APIEndpoint.swift             # Type-safe endpoint definitions
│   │   │   ├── APIConfig.swift               # Base URL & configuration
│   │   │   ├── AuthService.swift             # Auth + Keychain storage
│   │   │   └── TaskPollingManager.swift       # Long-running task polling
│   │   ├── Repositories/
│   │   │   └── StockRepository.swift         # Data access + caching
│   │   ├── ViewModels/
│   │   │   └── BaseViewModel.swift           # Base class with helpers
│   │   └── Utilities/
│   │       └── AppError.swift                # Unified error types
│   │
│   ├── Views/                                # UI (Atomic Design)
│   │   ├── Atoms/                            # Smallest reusable UI pieces
│   │   │   ├── AIBadge.swift
│   │   │   ├── CreditsBadge.swift
│   │   │   ├── DonutChartView.swift
│   │   │   ├── SparklineView.swift
│   │   │   ├── PriceChangeLabel.swift
│   │   │   ├── SentimentBadge.swift
│   │   │   └── ... (128 files)
│   │   ├── Molecules/                        # Composed components
│   │   │   ├── TickerCard.swift
│   │   │   ├── ChatInputBar.swift
│   │   │   ├── ReportCard.swift
│   │   │   ├── CreditsBalanceCard.swift
│   │   │   ├── Chart/                        # Chart components
│   │   │   │   ├── EarningsChartView.swift
│   │   │   │   └── SmartMoneyFlowChart.swift
│   │   │   └── ... (183 files)
│   │   ├── Organisms/                        # Full sections
│   │   │   ├── CustomTabBar.swift            # 5-tab navigation bar
│   │   │   ├── ChatHistoryList.swift
│   │   │   ├── LiveNewsTimeline.swift
│   │   │   ├── TickerFinancialsContent.swift
│   │   │   └── ... (102 files)
│   │   └── Screens/                          # Full-page views
│   │       ├── HomeView.swift
│   │       ├── UpdatesView.swift
│   │       ├── ResearchView.swift
│   │       ├── TrackingView.swift
│   │       ├── LearnView.swift
│   │       ├── TickerDetailView.swift
│   │       ├── ChatTabView.swift
│   │       ├── SearchView.swift
│   │       ├── ContentView.swift             # Tab container
│   │       ├── RootContainerView.swift       # Root layout
│   │       └── ... (37 files)
│   │
│   ├── ViewModels/                           # Screen-specific ViewModels
│   │   ├── HomeViewModel.swift
│   │   ├── UpdatesViewModel.swift
│   │   ├── ResearchViewModel.swift
│   │   ├── TrackingViewModel.swift
│   │   ├── LearnViewModel.swift
│   │   ├── TickerDetailViewModel.swift
│   │   ├── TickerReportViewModel.swift
│   │   ├── SearchViewModel.swift
│   │   ├── ChatViewModel.swift
│   │   ├── WhaleProfileViewModel.swift
│   │   ├── NewsDetailViewModel.swift
│   │   ├── CryptoDetailViewModel.swift
│   │   ├── ETFDetailViewModel.swift
│   │   ├── IndexDetailViewModel.swift
│   │   ├── CommodityDetailViewModel.swift
│   │   ├── InvestorPathViewModel.swift
│   │   ├── PriceActionViewModel.swift
│   │   └── TradeGroupDetailViewModel.swift
│   │
│   ├── Models/                               # Data models & DTOs (38 files)
│   │   ├── TickerDetailModels.swift          # Stock detail, key stats
│   │   ├── TickerReportModels.swift          # AI report structure
│   │   ├── UpdatesModels.swift               # News feed, sentiment
│   │   ├── ResearchModels.swift              # Analysis, personas, credits
│   │   ├── TrackingModels.swift              # Watchlist, portfolio
│   │   ├── ChatModels.swift                  # Chat messages, AI responses
│   │   ├── LearnModels.swift                 # Lessons, books, journey
│   │   ├── SearchModels.swift                # Search results
│   │   ├── CryptoDetailModels.swift
│   │   ├── ETFDetailModels.swift
│   │   ├── IndexDetailModels.swift
│   │   ├── CommodityDetailModels.swift
│   │   ├── GrowthModels.swift
│   │   ├── HealthCheckModels.swift
│   │   ├── HoldersModels.swift
│   │   ├── ProfitPowerModels.swift
│   │   ├── RevenueBreakdownModels.swift
│   │   ├── VitalRulesEngine.swift            # Financial vital signs rules
│   │   ├── WhaleProfileModels.swift
│   │   ├── WhaleDTOs.swift
│   │   ├── PortfolioHoldingModels.swift
│   │   └── ...
│   │
│   ├── Services/                             # Shared services
│   │   ├── AudioManager.swift                # Global audio playback
│   │   └── AIVoiceManager.swift              # AI voice interaction
│   │
│   ├── Theme/
│   │   └── AppTheme.swift                    # Colors, typography, spacing
│   │
│   ├── Assets.xcassets                       # Images, app icon, colors
│   └── Info.plist                            # App configuration
│
├── ios.xcodeproj/                            # Xcode project
└── iOS_ARCHITECTURE_GUIDE.md                 # This file
```

---

## Navigation & Routing

### Root Navigation Flow

```
iosApp (entry point)
    ↓
RootView (checks auth state)
    ↓
RootContainerView (audio player overlay)
    ↓
ContentView (5-tab CustomTabBar)
    ├─ Home       → HomeView
    ├─ Updates    → UpdatesView
    ├─ Research   → ResearchView
    ├─ Tracking   → TrackingView
    └─ Wiser      → LearnView
```

### Main Tabs (5)

| Tab | Screen | Purpose |
|-----|--------|---------|
| Home | `HomeView` | Market tickers, AI insights, daily briefings, recent research |
| Updates | `UpdatesView` | News timeline with sentiment analysis filters |
| Research | `ResearchView` | Generate AI stock analysis in investor personas |
| Tracking | `TrackingView` | Watchlist, portfolio, diversification metrics |
| Wiser | `LearnView` | Investment education, books, audio, gamified path |

### Secondary Screens (30+)

- **TickerDetailView** — Stock analysis (fundamentals, earnings, growth, health, holders, technicals)
- **TickerReportView** — Full AI-generated analysis report
- **CryptoDetailView** — Cryptocurrency overview & stats
- **ETFDetailView** — ETF overview, dividends, stats
- **IndexDetailView** — Market index overview
- **CommodityDetailView** — Commodity data
- **NewsDetailView** — Full article view
- **ChatTabView** — AI investment advisor conversation
- **SearchView** — Global search (stocks, news, books)
- **WhaleProfileView** — Institutional investor tracking
- **ProfileView** — User profile & settings
- **AppSettingsView** — App configuration
- **InvestorPathView** — Gamified learning path
- **MoneyMovesDetailView** — Financial article detail

---

## State Management

### Global vs Local State

| Type | Storage | Example | Access Method |
|------|---------|---------|---------------|
| **Global** | `AppState` | User auth, credits, watchlist | `@Environment(AppState.self)` |
| **Local** | `@StateObject` ViewModel | Search text, selected tab | `@StateObject var viewModel` |
| **UI Only** | `@State` | Sheet visibility, animation | `@State var isPresented` |

### AppState Structure

```swift
@Observable @MainActor final class AppState {
    // Sub-states
    var auth = AuthState()           // Auth status, access token
    var user = UserState()           // Profile, credits, tier (free/pro/premium)
    var watchlist = WatchlistState() // Tracked stocks with prices
    var research = ResearchState()   // Reports, generating status, persona

    // Global UI state
    var isOnline: Bool = true
    var isLoading: Bool = false
    var currentError: AppError?
    var toastMessage: ToastMessage?

    // Services
    var apiClient: APIClient!
    var authService: AuthService!
}
```

### When to Use Global State (AppState)

Use `AppState` when:
- Multiple screens need the same data (user credits, watchlist)
- State must persist across navigation
- Changes should trigger updates everywhere (auth status)

```swift
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

```swift
@MainActor
class ResearchViewModel: ObservableObject {
    @Published var searchText = ""
    @Published var isGenerating = false

    private weak var appState: AppState?

    init(appState: AppState) {
        self.appState = appState
    }

    func generateAnalysis(stockId: String) async {
        guard appState?.user.canGenerateResearch == true else {
            appState?.handleError(AppError.insufficientCredits(required: 1, available: 0))
            return
        }

        isGenerating = true
        defer { isGenerating = false }

        // ... generate analysis
        appState?.research.reports.insert(newReport, at: 0)
    }
}
```

---

## Network Layer

### APIClient (Actor-Based)

**File:** `Core/Services/APIClient.swift`

The APIClient is an `actor` ensuring thread-safe HTTP communication:

- **JSON encoding:** `.convertToSnakeCase` for request bodies
- **JSON decoding:** Uses explicit `CodingKeys` (avoids double-conversion)
- **Date handling:** ISO8601
- **Auth:** Bearer token injection from stored access token
- **Retry:** Exponential backoff for server errors (500+)
- **Logging:** Debug-only request/response logging

```swift
// Making a request
let stock = try await apiClient.request(
    endpoint: .getStock(ticker: "AAPL"),
    responseType: StockDetail.self
)
```

### Adding a New Endpoint

1. Add case to `APIEndpoint` enum in `Core/Services/APIEndpoint.swift`
2. Implement `path`, `method`, `queryParameters`, `body` as needed
3. Create request/response structs with `CodingKeys` if needed
4. Call from Repository or ViewModel

```swift
// 1. Define endpoint
case getStock(ticker: String)

var path: String {
    case .getStock(let ticker):
        return "/api/v1/stocks/\(ticker)"
}

// 2. Call it
let stock = try await apiClient.request(
    endpoint: .getStock(ticker: "AAPL"),
    responseType: StockDetail.self
)
```

### Response Models

All models use explicit `CodingKeys` to map snake_case JSON to camelCase Swift:

```swift
struct StockDetail: Codable {
    let ticker: String
    let companyName: String
    let marketCap: Double?

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case marketCap = "market_cap"
    }
}
```

---

## Authentication & Token Management

### AuthService + KeychainService

**File:** `Core/Services/AuthService.swift`

**Responsibilities:**
- Sign in / sign up with email & password
- Token refresh
- Sign out & token cleanup
- Keychain storage coordination

### Keychain Storage

```swift
final class KeychainService: @unchecked Sendable {
    func set(_ value: String, forKey key: String)   // Store
    func get(_ key: String) -> String?               // Retrieve
    func delete(_ key: String)                       // Remove
    func deleteAll()                                 // Clear all
}
```

- Uses iOS Keychain (`kSecClassGenericPassword`)
- Accessible after first unlock (device only)
- Stores `access_token` and `refresh_token` separately

### Auth Flow

```
App Launch
    ↓
Check Keychain for stored token
    ↓ (token found)
Validate with GET /api/v1/auth/me
    ↓ (valid)
AppState.auth.status = .authenticated
    ↓ (invalid)
Clear token → AppState.auth.status = .unauthenticated
```

On any 401 response, the APIClient automatically clears the token and signs out.

---

## Long-Running Tasks

### TaskPollingManager

**File:** `Core/Services/TaskPollingManager.swift`

Handles AI research generation with polling via `AsyncThrowingStream`:

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
                    break
                case .progress(let percent, let step):
                    self.progress = percent
                    self.currentStep = step
                case .completed(let report):
                    break
                case .failed(let error):
                    break
                }
            }
        }
    }
}
```

### Progress Events

```swift
enum TaskProgress<T> {
    case started(taskId: String)
    case progress(percent: Int, step: String)
    case completed(T)
    case failed(AppError)
}
```

### Flow Diagram

```
User taps "Generate"
    ↓
POST /research/generate → { report_id: "abc123" }
    ↓
Start polling every 3 sec
    ↓
GET /research/reports/{id}/status → { progress: 45, step: "..." }
    ↓ (repeat until completed/failed)
GET /research/reports/{id} → Full report JSON
```

---

## Error Handling

### AppError Enum

**File:** `Core/Utilities/AppError.swift`

```swift
enum AppError {
    // Network
    case noConnection
    case timeout
    case serverError(statusCode: Int)

    // Auth
    case unauthorized
    case tokenExpired
    case forbidden

    // Business
    case insufficientCredits(required: Int, available: Int)
    case notFound(resource: String)
    case validationFailed(message: String)
    case rateLimited

    // API
    case apiError(code: String, message: String)

    // Generic
    case unknown(message: String)
}
```

Each error provides:
- Unique `id` for identification
- User-friendly `title` and `message`
- `suggestedAction` for recovery (retry, upgrade, fixInput, etc.)

### Handling Errors in ViewModels

```swift
func loadData() {
    Task {
        do {
            let data = try await repository.getData()
            self.items = data
        } catch {
            let appError = AppError.from(error)
            self.errorMessage = appError.message       // Local UI
            appState?.handleError(error)               // Global toast
        }
    }
}
```

### Using BaseViewModel

**File:** `Core/ViewModels/BaseViewModel.swift`

Provides automatic loading state, error handling, AppState access, and task cancellation:

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

## Data Models

**Location:** `Models/` (38 files)

### Major Model Categories

| File | Contents |
|------|----------|
| `TickerDetailModels.swift` | Stock detail, key stats, company profile |
| `TickerReportModels.swift` | Full AI report (executive summary, vitals, fundamentals, revenue, moat, insider activity, forecast, risk, price movement) |
| `UpdatesModels.swift` | News feed, filters, sentiment analysis |
| `ResearchModels.swift` | Analysis reports, personas, credits, trending |
| `TrackingModels.swift` | Watchlist, portfolio, diversification |
| `ChatModels.swift` | Chat messages, AI responses |
| `LearnModels.swift` | Lessons, topics, journey, books, schedule |
| `SearchModels.swift` | Search results, recent searches |
| `GrowthModels.swift` | Growth analysis metrics |
| `HealthCheckModels.swift` | Financial health diagnostics |
| `HoldersModels.swift` | Institutional/insider holdings |
| `ProfitPowerModels.swift` | Profitability analysis |
| `RevenueBreakdownModels.swift` | Revenue segmentation |
| `VitalRulesEngine.swift` | Financial vital signs rules engine |
| `WhaleProfileModels.swift` | Institutional investor profiles |
| `PortfolioHoldingModels.swift` | Portfolio position tracking |
| `CryptoDetailModels.swift` | Cryptocurrency data |
| `ETFDetailModels.swift` | ETF data |
| `IndexDetailModels.swift` | Market index data |
| `CommodityDetailModels.swift` | Commodity data |

### Coding Convention

All models use explicit `CodingKeys` to map snake_case JSON to camelCase Swift properties.

---

## ViewModels

**Location:** `ViewModels/` (19 files)

| ViewModel | Screen | Key Responsibilities |
|-----------|--------|---------------------|
| `HomeViewModel` | HomeView | Market tickers, insights, briefings, recent research |
| `UpdatesViewModel` | UpdatesView | News feed with category filters |
| `ResearchViewModel` | ResearchView | Ticker search, persona selection, report generation |
| `TrackingViewModel` | TrackingView | Watchlist management, portfolio tracking |
| `LearnViewModel` | LearnView | Lessons, books, learning journey |
| `TickerDetailViewModel` | TickerDetailView | Stock detail data loading |
| `TickerReportViewModel` | TickerReportView | Full AI report display |
| `SearchViewModel` | SearchView | Global search |
| `ChatViewModel` | ChatTabView | AI chat interface |
| `WhaleProfileViewModel` | WhaleProfileView | Institutional investor data |
| `NewsDetailViewModel` | NewsDetailView | Full article content |
| `CryptoDetailViewModel` | CryptoDetailView | Crypto data |
| `ETFDetailViewModel` | ETFDetailView | ETF data |
| `IndexDetailViewModel` | IndexDetailView | Index data |
| `CommodityDetailViewModel` | CommodityDetailView | Commodity data |
| `InvestorPathViewModel` | InvestorPathView | Gamified learning path |
| `PriceActionViewModel` | Price/technical views | Price & technical data |
| `TradeGroupDetailViewModel` | TradeGroupDetailView | Trade group details |

---

## UI Component System (Atomic Design)

The app follows **Atomic Design** methodology for UI composition:

### Atoms (128 files) — Smallest reusable units

Badges, labels, indicators, tiny charts:
- `AIBadge`, `CreditsBadge`, `SentimentBadge`, `AnalystActionBadge`
- `DonutChartView`, `SparklineView`, `GradientProgressBar`
- `PriceChangeLabel`, toggle buttons, progress indicators

### Molecules (183 files) — Composed of atoms

Cards, bars, rows, headers:
- `TickerCard`, `ReportCard`, `CreditsBalanceCard`
- `ChatInputBar`, `ArticleCalloutBox`, `DailyBriefingRow`
- `CommunityInsightRow`, `AnalystActionCard`
- Chart components: `EarningsChartView`, `SmartMoneyFlowChart`

### Organisms (102 files) — Full sections

Complex, self-contained sections:
- `CustomTabBar` — 5-tab navigation
- `ChatHistoryList`, `ChatMessagesList`
- `LiveNewsTimeline`, `TickerFinancialsContent`
- `SmartMoneySection`, `EarningsSectionCard`
- `HealthCheckSectionCard`, `GrowthSectionCard`

### Screens (37 files) — Full-page views

Complete screens with their own ViewModel, listed in [Navigation & Routing](#navigation--routing).

---

## Theme & Styling

**File:** `Theme/AppTheme.swift`

### Color System (Dark Theme)

| Token | Hex | Usage |
|-------|-----|-------|
| Background | `#171B26` | Main background (dark navy) |
| Card Background | `#1E2330` | Card surfaces |
| Primary Blue | `#3B82F6` | Primary actions |
| Accent Cyan | `#06B6D4` | Secondary accent |
| Bullish Green | `#22C55E` | Positive values |
| Bearish Red | `#EF4444` | Negative values |
| Neutral Amber | `#F59E0B` | Neutral/warning |
| Text Primary | `#FFFFFF` | Main text |
| Text Secondary | `#9CA3AF` | Muted text |

### Typography

System font with sizes ranging from 11px to 28px, weights: regular, medium, semibold, bold.

### Spacing Scale

| Token | Value |
|-------|-------|
| xxs | 2px |
| xs | 4px |
| sm | 8px |
| md | 12px |
| lg | 16px |
| xl | 20px |
| xxl | 24px |
| xxxl | 32px |

### Corner Radius

| Token | Value |
|-------|-------|
| small | 6px |
| medium | 8px |
| large | 12px |
| pill | 20px |

---

## Services

### AudioManager

**File:** `Services/AudioManager.swift`

Global singleton for audio playback (educational content, audiobooks):

- AVFoundation-based playback
- Playback speed control & sleep timer
- Queue management for episodes
- Mini player + full-screen player modes
- Scroll-based hiding & compact mode for chat keyboard
- History tracking

```swift
// Key published state
@Published var currentEpisode: AudioEpisode?
@Published var playbackState: PlaybackState   // idle, playing, paused, stopped
@Published var currentTime: TimeInterval
@Published var duration: TimeInterval
@Published var queue: [AudioQueueItem]
@Published var isMiniPlayerExpanded: Bool
@Published var showFullScreenPlayer: Bool
```

### AIVoiceManager

**File:** `Services/AIVoiceManager.swift`

Manages AI voice interaction for conversational features.

---

## API Endpoints Reference

**File:** `Core/Services/APIEndpoint.swift`

### Auth
| Endpoint | Method | Path |
|----------|--------|------|
| `signIn` | POST | `/api/v1/auth/signin` |
| `signUp` | POST | `/api/v1/auth/signup` |
| `refreshToken` | POST | `/api/v1/auth/refresh` |
| `signOut` | POST | `/api/v1/auth/signout` |

### User
| Endpoint | Method | Path |
|----------|--------|------|
| `getCurrentUser` | GET | `/api/v1/auth/me` |
| `getUserCredits` | GET | `/api/v1/users/credits` |
| `updateProfile` | PUT | `/api/v1/users/profile` |

### Stocks
| Endpoint | Method | Path |
|----------|--------|------|
| `searchStocks` | GET | `/api/v1/stocks/search` |
| `getStock` | GET | `/api/v1/stocks/{ticker}` |
| `getStockQuote` | GET | `/api/v1/stocks/{ticker}/quote` |
| `getStockFundamentals` | GET | `/api/v1/stocks/{ticker}/fundamentals` |
| `getStockNews` | GET | `/api/v1/stocks/{ticker}/news` |
| `getStockChart` | GET | `/api/v1/stocks/{ticker}/chart` |
| `getAnalystAnalysis` | GET | `/api/v1/stocks/{ticker}/analyst` |
| `getSentimentAnalysis` | GET | `/api/v1/stocks/{ticker}/sentiment` |
| `getTickerReport` | GET | `/api/v1/stocks/{ticker}/report` |

### Asset Details
| Endpoint | Method | Path |
|----------|--------|------|
| `getIndexDetail` | GET | `/api/v1/indices/{symbol}` |
| `getCryptoDetail` | GET | `/api/v1/crypto/{symbol}` |
| `getETFDetail` | GET | `/api/v1/etfs/{symbol}` |
| `getCommodityDetail` | GET | `/api/v1/commodities/{symbol}` |

### Watchlist & Portfolio
| Endpoint | Method | Path |
|----------|--------|------|
| `getWatchlist` | GET | `/api/v1/watchlist` |
| `addToWatchlist` | POST | `/api/v1/watchlist` |
| `removeFromWatchlist` | DELETE | `/api/v1/watchlist/{id}` |
| `getTrackingAssets` | GET | `/api/v1/tracking/assets` |
| `getHoldings` | GET | `/api/v1/tracking/holdings` |
| `addHolding` | POST | `/api/v1/tracking/holdings` |
| `updateHolding` | PUT | `/api/v1/tracking/holdings/{id}` |
| `deleteHolding` | DELETE | `/api/v1/tracking/holdings/{id}` |

### Research
| Endpoint | Method | Path |
|----------|--------|------|
| `generateAnalysis` | POST | `/api/v1/research/generate` |
| `getResearchStatus` | GET | `/api/v1/research/reports/{id}/status` |
| `getResearchReport` | GET | `/api/v1/research/reports/{id}` |
| `rateResearchReport` | POST | `/api/v1/research/reports/{id}/rate` |

### Chat
| Endpoint | Method | Path |
|----------|--------|------|
| `createChatSession` | POST | `/api/v1/chat/sessions` |
| `sendChatMessage` | POST | `/api/v1/chat/sessions/{id}/messages` |
| `getChatHistory` | GET | `/api/v1/chat/sessions/{id}/messages` |

### News
| Endpoint | Method | Path |
|----------|--------|------|
| `getNewsFeed` | GET | `/api/v1/news` |
| `getNewsArticle` | GET | `/api/v1/news/{id}` |

---

## Backend Context

### Technology Stack

| Component | Technology |
|-----------|-----------|
| Framework | FastAPI (Python) |
| Server | Uvicorn |
| Database | Supabase (PostgreSQL) |
| AI | Google Gemini API |
| Auth | JWT (python-jose) |
| Financial Data | Financial Modeling Prep API |
| Technical Analysis | pandas, TA-Lib |

### API Convention

- RESTful endpoints prefixed with `/api/v1/`
- JSON request/response bodies
- Bearer token authentication (`Authorization: Bearer <token>`)
- Standard HTTP status codes
- snake_case field names

---

## Data Persistence

| Data | Storage | Notes |
|------|---------|-------|
| Auth tokens | iOS Keychain | Secure, persists across app launches |
| App data | Server-only | No local database (CoreData/SQLite/Realm) |
| In-memory cache | ViewModel/Repository | Cleared on navigation away |
| UI preferences | UserDefaults | Theme, settings |

---

## Swift Language Patterns

Modern Swift patterns used throughout the codebase:

| Pattern | Usage |
|---------|-------|
| `@Observable` (iOS 17+) | Global state — no `@EnvironmentObject` boilerplate |
| `async/await` | All networking and async operations |
| `actor` | Thread-safe `APIClient` |
| `Sendable` | Compile-time data race safety |
| `@MainActor` | UI operations on main thread |
| `@Published` | Observable object property binding |
| `@StateObject` | ViewModel lifecycle management |
| `@Environment` | Dependency injection |
| `AsyncThrowingStream` | Long-running task progress |
| Custom `ViewModifier` | Reusable view behaviors |

---

## Developer Guide

### Adding a New Screen

1. Create model in `Models/` with `CodingKeys`
2. Add API endpoint case to `APIEndpoint` enum
3. Create ViewModel in `ViewModels/` (extend `BaseViewModel` or `ObservableObject`)
4. Create screen in `Views/Screens/`
5. Wire navigation from parent view

### Adding New Global State

1. Extend `AppState` or add a new `@Observable` sub-state
2. Access via `@Environment(AppState.self)` — no `@Published` needed

### Where to Put Code

| Code Type | Location |
|-----------|----------|
| UI components | `Views/Atoms/`, `Molecules/`, `Organisms/` |
| Full screens | `Views/Screens/` |
| Screen-specific logic | `ViewModels/` |
| Shared data (auth, user) | `Core/State/AppState.swift` |
| API calls | `Core/Services/APIClient.swift` |
| Endpoint definitions | `Core/Services/APIEndpoint.swift` |
| Data fetching + caching | `Core/Repositories/` |
| Error types | `Core/Utilities/AppError.swift` |
| Audio/voice features | `Services/` |
| Colors, fonts, spacing | `Theme/AppTheme.swift` |

---

## Migration Guide

### Existing ViewModels Still Work

The architecture is **additive** — existing code works as-is:

```swift
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

---

## Key Decisions Summary

| Decision | Choice | Why |
|----------|--------|-----|
| Global State | `@Observable` + `@Environment` | Modern, no boilerplate, type-safe |
| Local State | `@StateObject` ViewModels | Existing pattern, works great |
| Networking | Actor-based `APIClient` | Thread-safe, testable |
| Long Tasks | `AsyncThrowingStream` polling | Native Swift, cancellable |
| Error Handling | `AppError` enum | User-friendly messages, actionable |
| Dependencies | Zero third-party | Reduces complexity, Apple-only |
| UI System | Atomic Design | Scalable component reuse |
| Persistence | Server + Keychain | No local DB complexity |
| Theme | Dark-only with token system | Consistent, maintainable |
| Backend | FastAPI + Supabase + Gemini | Python AI ecosystem |
