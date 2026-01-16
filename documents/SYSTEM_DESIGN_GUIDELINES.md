# AI Value Investor - System Design Guidelines

**Version:** 1.0
**Author:** Principal Architect
**Date:** January 2026
**Status:** DRAFT - For Review

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Data Flow Architecture](#3-data-flow-architecture)
4. [State Management Strategy (iOS)](#4-state-management-strategy-ios)
5. [Agent Orchestration Pattern](#5-agent-orchestration-pattern)
6. [Error Handling Strategy](#6-error-handling-strategy)
7. [Caching & Performance](#7-caching--performance)
8. [API Contract Standards](#8-api-contract-standards)
9. [Security Architecture](#9-security-architecture)
10. [Recommendations & Critique](#10-recommendations--critique)

---

## 1. Executive Summary

### Vision
Build a "Bloomberg Terminal for Novice Investors" - a system that makes professional-grade financial analysis accessible through AI-powered personas.

### Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Backend Pattern | Clean Architecture (Layered) | Separation of concerns, testability |
| iOS Pattern | MVVM + Repository | SwiftUI native, reactive state |
| AI Orchestration | Task Queue + Polling | Long-running tasks without blocking |
| State Management | Centralized App State | Consistent UX across screens |
| Error Strategy | Domain-Specific Errors | User-friendly, actionable messages |

### Architecture Principles

1. **Offline-First Mindset**: Cache aggressively, degrade gracefully
2. **Optimistic UI**: Show expected results, reconcile on confirmation
3. **Fail Fast, Fail Informatively**: Errors should guide users to solutions
4. **Progressive Disclosure**: Load essential data first, details on demand

---

## 2. Architecture Overview

### High-Level System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           iOS APPLICATION                                    │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        PRESENTATION LAYER                             │   │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │   │
│  │   │     Views       │  │    ViewModels   │  │    Coordinators │      │   │
│  │   │ (Atomic Design) │◄─│   (per-screen)  │◄─│   (navigation)  │      │   │
│  │   └─────────────────┘  └────────┬────────┘  └─────────────────┘      │   │
│  └──────────────────────────────────┼───────────────────────────────────┘   │
│                                     │                                        │
│  ┌──────────────────────────────────▼───────────────────────────────────┐   │
│  │                         DOMAIN LAYER                                  │   │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │   │
│  │   │   App State     │  │   Repositories  │  │   Use Cases     │      │   │
│  │   │  (Observable)   │◄─│  (Protocols)    │◄─│  (Business)     │      │   │
│  │   └─────────────────┘  └────────┬────────┘  └─────────────────┘      │   │
│  └──────────────────────────────────┼───────────────────────────────────┘   │
│                                     │                                        │
│  ┌──────────────────────────────────▼───────────────────────────────────┐   │
│  │                          DATA LAYER                                   │   │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │   │
│  │   │  API Service    │  │  Cache Manager  │  │  Persistence    │      │   │
│  │   │  (URLSession)   │  │  (Memory+Disk)  │  │  (Core Data)    │      │   │
│  │   └─────────────────┘  └─────────────────┘  └─────────────────┘      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │ HTTPS/JSON
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FASTAPI BACKEND                                    │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         API LAYER (v1)                                │   │
│  │   ┌────────┐ ┌────────┐ ┌──────────┐ ┌────────┐ ┌────────────┐       │   │
│  │   │  auth  │ │ stocks │ │ research │ │  news  │ │   chat     │       │   │
│  │   └───┬────┘ └───┬────┘ └────┬─────┘ └───┬────┘ └─────┬──────┘       │   │
│  └───────┼──────────┼───────────┼───────────┼────────────┼──────────────┘   │
│          │          │           │           │            │                   │
│  ┌───────▼──────────▼───────────▼───────────▼────────────▼──────────────┐   │
│  │                       SERVICE LAYER                                   │   │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │   │
│  │   │  UserService    │  │ ResearchService │  │   NewsService   │      │   │
│  │   └────────┬────────┘  └────────┬────────┘  └────────┬────────┘      │   │
│  └────────────┼─────────────────────┼────────────────────┼──────────────┘   │
│               │                     │                    │                   │
│  ┌────────────▼─────────────────────▼────────────────────▼──────────────┐   │
│  │                         AGENT LAYER                                   │   │
│  │   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │   │
│  │   │ ResearchAgent   │  │EducationAgent   │  │ NewsSummarizer  │      │   │
│  │   │ (Persona-Based) │  │ (RAG-Based)     │  │ (Sentiment)     │      │   │
│  │   └────────┬────────┘  └────────┬────────┘  └────────┬────────┘      │   │
│  └────────────┼─────────────────────┼────────────────────┼──────────────┘   │
│               │                     │                    │                   │
│  ┌────────────▼─────────────────────▼────────────────────▼──────────────┐   │
│  │                      INTEGRATION LAYER                                │   │
│  │   ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐     │   │
│  │   │   Gemini   │  │    FMP     │  │  NewsAPI   │  │  Supabase  │     │   │
│  │   └────────────┘  └────────────┘  └────────────┘  └────────────┘     │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         ▼                            ▼                            ▼
┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
│    Supabase     │        │   Google Gemini │        │      FMP        │
│   (Postgres +   │        │   1.5 Pro+      │        │ (Financial Data)│
│    Auth + RLS)  │        │                 │        │                 │
└─────────────────┘        └─────────────────┘        └─────────────────┘
```

---

## 3. Data Flow Architecture

### 3.1 Standard Request Flow (Synchronous)

**Example: Fetching Stock Details**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              iOS CLIENT                                      │
│                                                                              │
│  1. User taps stock → TickerDetailViewModel.loadStock(ticker)               │
│                               │                                              │
│  2. ViewModel calls           ▼                                              │
│     StockRepository.fetchStock(ticker)                                       │
│                               │                                              │
│  3. Repository checks         ▼                                              │
│     ┌─────────────────────────────────────────┐                             │
│     │ Cache.get(key: "stock_\(ticker)")       │                             │
│     │   ├── HIT → Return cached, trigger      │                             │
│     │   │         background refresh if stale │                             │
│     │   └── MISS → Continue to API ──────────►│                             │
│     └─────────────────────────────────────────┘                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼ GET /api/v1/stocks/{ticker}
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BACKEND                                         │
│                                                                              │
│  4. API Endpoint receives request                                            │
│     stocks.py: get_stock_detail()                                            │
│                               │                                              │
│  5. Validates auth            ▼                                              │
│     dependencies.py: get_current_user()                                      │
│                               │                                              │
│  6. Service layer             ▼                                              │
│     ┌─────────────────────────────────────────┐                             │
│     │ Check Redis cache                        │                             │
│     │   ├── HIT → Return cached response      │                             │
│     │   └── MISS → Query Supabase ───────────►│                             │
│     └─────────────────────────────────────────┘                             │
│                               │                                              │
│  7. Data aggregation          ▼                                              │
│     ┌─────────────────────────────────────────┐                             │
│     │ Parallel fetch:                          │                             │
│     │   - supabase.table("stocks").select()   │                             │
│     │   - fmp.get_company_profile()           │                             │
│     │   - fmp.get_quote()                     │                             │
│     └─────────────────────────────────────────┘                             │
│                               │                                              │
│  8. Transform to schema       ▼                                              │
│     schemas/stock.py: StockDetailResponse                                    │
│                               │                                              │
│  9. Cache result              ▼                                              │
│     cache.set(key, response, ttl=300)                                        │
│                               │                                              │
│  10. Return JSON              ▼                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼ JSON Response
┌─────────────────────────────────────────────────────────────────────────────┐
│                              iOS CLIENT                                      │
│                                                                              │
│  11. APIService.decode()      │                                              │
│      → StockDetail model      ▼                                              │
│                                                                              │
│  12. Repository caches        │                                              │
│      locally & returns        ▼                                              │
│                                                                              │
│  13. ViewModel updates        │                                              │
│      @Published stock         ▼                                              │
│                                                                              │
│  14. SwiftUI re-renders       │                                              │
│      TickerDetailView         ▼                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Repository Pattern Implementation (iOS)

```swift
// MARK: - Protocol Definition
protocol StockRepositoryProtocol {
    func fetchStock(_ ticker: String) async throws -> Stock
    func fetchFundamentals(_ ticker: String) async throws -> [Fundamental]
    func searchStocks(_ query: String) async throws -> [StockSearchResult]
    func addToWatchlist(_ stockId: String) async throws
}

// MARK: - Implementation
final class StockRepository: StockRepositoryProtocol {
    private let apiService: APIServiceProtocol
    private let cacheManager: CacheManagerProtocol
    private let persistenceManager: PersistenceManagerProtocol

    init(
        apiService: APIServiceProtocol = APIService.shared,
        cacheManager: CacheManagerProtocol = CacheManager.shared,
        persistenceManager: PersistenceManagerProtocol = PersistenceManager.shared
    ) {
        self.apiService = apiService
        self.cacheManager = cacheManager
        self.persistenceManager = persistenceManager
    }

    func fetchStock(_ ticker: String) async throws -> Stock {
        let cacheKey = "stock_\(ticker)"

        // 1. Check memory cache (instant)
        if let cached: Stock = cacheManager.get(cacheKey), !cached.isStale {
            return cached
        }

        // 2. Check disk cache (fast)
        if let persisted: Stock = try? await persistenceManager.fetch(cacheKey) {
            // Trigger background refresh if stale
            if persisted.isStale {
                Task { try? await refreshStock(ticker) }
            }
            return persisted
        }

        // 3. Fetch from API
        let stock = try await apiService.request(
            endpoint: .stockDetail(ticker),
            responseType: Stock.self
        )

        // 4. Cache the result
        cacheManager.set(cacheKey, value: stock, ttl: .minutes(5))
        try? await persistenceManager.save(stock, key: cacheKey)

        return stock
    }
}
```

### 3.3 Backend Service Layer Pattern

```python
# services/stock_service.py

class StockService:
    """
    Service layer for stock-related operations.
    Handles caching, data aggregation, and business logic.
    """

    def __init__(
        self,
        supabase: Client,
        fmp_client: FMPClient,
        cache_manager: CacheManager
    ):
        self.supabase = supabase
        self.fmp = fmp_client
        self.cache = cache_manager

    async def get_stock_detail(self, ticker: str) -> StockDetail:
        """
        Get comprehensive stock details with caching.

        Data Flow:
        1. Check Redis cache
        2. Parallel fetch from Supabase + FMP
        3. Aggregate and transform
        4. Cache result
        """
        cache_key = f"stock:{ticker}"

        # Check cache
        cached = await self.cache.get(cache_key)
        if cached:
            return StockDetail(**cached)

        # Parallel fetch
        db_stock, profile, quote = await asyncio.gather(
            self._get_from_db(ticker),
            self.fmp.get_company_profile(ticker),
            self.fmp.get_quote(ticker),
            return_exceptions=True
        )

        # Handle partial failures gracefully
        stock = self._merge_stock_data(db_stock, profile, quote)

        # Cache for 5 minutes
        await self.cache.set(cache_key, stock.dict(), ttl=300)

        return stock
```

---

## 4. State Management Strategy (iOS)

### 4.1 Centralized App State Architecture

**Problem with Current Approach:**
Your current ViewModels each manage their own state independently, leading to:
- Duplicate data across screens (e.g., user credits)
- Inconsistent state after mutations
- No shared state between related screens

**Recommended Architecture:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          APP STATE (Single Source of Truth)                  │
│                                                                              │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│   │ AuthState   │  │ UserState   │  │ StockState  │  │ ResearchState│       │
│   │ ─────────── │  │ ─────────── │  │ ─────────── │  │ ───────────  │       │
│   │ isLoggedIn  │  │ profile     │  │ watchlist   │  │ reports      │       │
│   │ token       │  │ credits     │  │ recentViews │  │ generating   │       │
│   │ refreshToken│  │ tier        │  │ searchCache │  │ personas     │       │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │
│          │                │                │                │               │
│          └────────────────┴────────────────┴────────────────┘               │
│                                   │                                          │
│                                   ▼                                          │
│                        ┌──────────────────┐                                 │
│                        │   AppState       │                                 │
│                        │   @Observable    │                                 │
│                        └────────┬─────────┘                                 │
│                                 │                                            │
└─────────────────────────────────┼────────────────────────────────────────────┘
                                  │
           ┌──────────────────────┼──────────────────────┐
           │                      │                      │
           ▼                      ▼                      ▼
    ┌─────────────┐        ┌─────────────┐        ┌─────────────┐
    │HomeViewModel│        │ResearchVM   │        │TickerVM     │
    │ @Bindable   │        │ @Bindable   │        │ @Bindable   │
    │ appState    │        │ appState    │        │ appState    │
    └─────────────┘        └─────────────┘        └─────────────┘
```

### 4.2 Implementation

```swift
// MARK: - App State Container (iOS 17+ Observation)
@Observable
final class AppState {
    // Sub-states
    var auth = AuthState()
    var user = UserState()
    var stocks = StockState()
    var research = ResearchState()
    var news = NewsState()

    // Global UI state
    var isOnline: Bool = true
    var globalError: AppError?

    // Dependencies
    private let authRepository: AuthRepositoryProtocol
    private let userRepository: UserRepositoryProtocol

    init(
        authRepository: AuthRepositoryProtocol = AuthRepository(),
        userRepository: UserRepositoryProtocol = UserRepository()
    ) {
        self.authRepository = authRepository
        self.userRepository = userRepository
    }
}

// MARK: - Sub-State: User
@Observable
final class UserState {
    var profile: UserProfile?
    var credits: CreditBalance?
    var tier: UserTier = .free
    var isLoading: Bool = false

    var canGenerateResearch: Bool {
        guard let credits = credits else { return false }
        return credits.remaining > 0
    }
}

// MARK: - Sub-State: Research
@Observable
final class ResearchState {
    var reports: [ResearchReport] = []
    var generatingReports: Set<String> = []  // Report IDs being generated
    var selectedPersona: InvestorPersona = .buffett

    func isGenerating(_ reportId: String) -> Bool {
        generatingReports.contains(reportId)
    }
}

// MARK: - ViewModel Using Shared State
@MainActor
final class ResearchViewModel {
    // Shared state (read/write)
    @Bindable var appState: AppState

    // Local state (screen-specific)
    var searchText: String = ""
    var selectedTab: ResearchTab = .research
    var isSearching: Bool = false

    private let researchRepository: ResearchRepositoryProtocol

    init(
        appState: AppState,
        researchRepository: ResearchRepositoryProtocol = ResearchRepository()
    ) {
        self.appState = appState
        self.researchRepository = researchRepository
    }

    func generateAnalysis(stockId: String) async {
        // Check shared state for credits
        guard appState.user.canGenerateResearch else {
            appState.globalError = .insufficientCredits
            return
        }

        do {
            // Optimistic UI update
            let tempId = UUID().uuidString
            appState.research.generatingReports.insert(tempId)

            // Call API
            let report = try await researchRepository.generate(
                stockId: stockId,
                persona: appState.research.selectedPersona
            )

            // Update shared state
            appState.research.generatingReports.remove(tempId)
            appState.research.reports.insert(report, at: 0)
            appState.user.credits?.used += 1

        } catch {
            appState.globalError = .fromError(error)
        }
    }
}
```

### 4.3 State Flow Diagram

```
User Action: "Generate Analysis"
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│                    ResearchViewModel                              │
│                                                                   │
│  1. Validate: appState.user.canGenerateResearch                  │
│       │                                                           │
│       ▼ (false) → Set appState.globalError = .insufficientCredits│
│       │                                                           │
│       ▼ (true)                                                    │
│  2. Optimistic: appState.research.generatingReports.insert(id)   │
│       │                                                           │
│       ▼                                                           │
│  3. API Call: researchRepository.generate()                       │
│       │                                                           │
│       ├── Success:                                                │
│       │     - appState.research.reports.insert(report)           │
│       │     - appState.user.credits.used += 1                    │
│       │                                                           │
│       └── Failure:                                                │
│             - appState.research.generatingReports.remove(id)     │
│             - appState.globalError = .fromError(error)           │
└──────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│              SwiftUI Automatic Re-render                          │
│                                                                   │
│  - ResearchView: Shows generating indicator                       │
│  - HomeView: Credits badge updates                                │
│  - ProfileView: Usage stats update                                │
│                                                                   │
│  (All views observe the same AppState)                            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. Agent Orchestration Pattern

### 5.1 The Challenge

Deep Research reports take ~30 seconds to generate. HTTP requests shouldn't block for this long because:
- Mobile connections are unreliable
- Users expect responsive UI
- iOS may terminate long-running requests

### 5.2 Recommended Pattern: Task Queue + Polling

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ASYNC TASK PATTERN                                   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        iOS CLIENT                                     │   │
│  │                                                                        │   │
│  │  1. POST /research/generate → Returns immediately with report_id      │   │
│  │                                                                        │   │
│  │  2. Poll GET /research/reports/{id}/status every 3s                   │   │
│  │     └── Response: { status: "processing", progress: 45 }              │   │
│  │                                                                        │   │
│  │  3. When status == "completed"                                         │   │
│  │     └── GET /research/reports/{id} → Full report                      │   │
│  │                                                                        │   │
│  │  Alternative: WebSocket for real-time updates (optional)              │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        BACKEND                                         │   │
│  │                                                                        │   │
│  │  POST /research/generate:                                              │   │
│  │    1. Validate credits                                                 │   │
│  │    2. Create report record (status: "pending")                         │   │
│  │    3. Enqueue background task                                          │   │
│  │    4. Return { report_id, status: "pending" } ← IMMEDIATE              │   │
│  │                                                                        │   │
│  │  Background Worker:                                                    │   │
│  │    1. Update status: "processing"                                      │   │
│  │    2. Gather financial data (parallel FMP calls)                       │   │
│  │    3. Generate AI analysis (Gemini)                                    │   │
│  │    4. Update status: "completed" + store results                       │   │
│  │    5. Decrement credits (only on success)                              │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.3 Backend Implementation

```python
# endpoints/research.py

@router.post("/generate")
async def generate_research_report(
    request: ResearchRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
) -> ResearchJobResponse:
    """
    Initiate research report generation.
    Returns immediately with job ID for polling.
    """
    # 1. Pre-check credits (fail fast)
    user_service = UserService(supabase)
    if not await user_service.check_credits(user["id"]):
        raise HTTPException(403, "Insufficient credits")

    # 2. Create pending report
    report = supabase.table("deep_research_reports").insert({
        "user_id": user["id"],
        "stock_id": request.stock_id,
        "investor_persona": request.investor_persona,
        "status": "pending",
        "progress": 0
    }).execute()

    report_id = report.data[0]["id"]

    # 3. Enqueue background task
    background_tasks.add_task(
        execute_research_generation,
        report_id=report_id,
        stock_id=request.stock_id,
        persona=request.investor_persona,
        user_id=user["id"]
    )

    # 4. Return immediately
    return ResearchJobResponse(
        report_id=report_id,
        status="pending",
        estimated_seconds=30,
        poll_url=f"/api/v1/research/reports/{report_id}/status"
    )


@router.get("/reports/{report_id}/status")
async def get_report_status(
    report_id: str,
    user: dict = Depends(get_current_user),
    supabase: Client = Depends(get_supabase)
) -> ReportStatusResponse:
    """
    Get current status of report generation.
    Designed for polling (lightweight response).
    """
    result = supabase.table("deep_research_reports").select(
        "status, progress, error_message, completed_at"
    ).eq("id", report_id).eq("user_id", user["id"]).single().execute()

    if not result.data:
        raise HTTPException(404, "Report not found")

    return ReportStatusResponse(**result.data)


# Background task with progress updates
async def execute_research_generation(
    report_id: str,
    stock_id: str,
    persona: str,
    user_id: str
):
    """
    Background worker for research generation.
    Updates progress throughout for polling.
    """
    supabase = get_supabase()

    def update_progress(progress: int, step: str):
        supabase.table("deep_research_reports").update({
            "progress": progress,
            "current_step": step,
            "status": "processing"
        }).eq("id", report_id).execute()

    try:
        update_progress(10, "Fetching company data")

        # Step 1: Gather financial data
        financial_data = await gather_financial_data(stock_id)
        update_progress(30, "Analyzing fundamentals")

        # Step 2: Generate AI analysis
        agent = ResearchAgent()
        analysis = await agent.generate_research_report(
            ticker=financial_data["ticker"],
            persona=persona,
            financial_data=financial_data
        )
        update_progress(80, "Formatting report")

        # Step 3: Store results
        supabase.table("deep_research_reports").update({
            "status": "completed",
            "progress": 100,
            **analysis,
            "completed_at": datetime.utcnow().isoformat()
        }).eq("id", report_id).execute()

        # Step 4: Decrement credits (only on success)
        await UserService(supabase).decrement_credits(user_id, 1)

    except Exception as e:
        logger.error(f"Research generation failed: {e}")
        supabase.table("deep_research_reports").update({
            "status": "failed",
            "error_message": str(e),
            "progress": 0
        }).eq("id", report_id).execute()
        # NOTE: No credit decrement on failure
```

### 5.4 iOS Polling Implementation

```swift
// MARK: - Research Generation with Polling
final class ResearchRepository: ResearchRepositoryProtocol {

    func generateAndAwaitReport(
        stockId: String,
        persona: InvestorPersona
    ) -> AsyncThrowingStream<ReportProgress, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    // 1. Initiate generation
                    let job = try await apiService.request(
                        endpoint: .generateResearch(stockId: stockId, persona: persona),
                        responseType: ResearchJobResponse.self
                    )

                    continuation.yield(.started(reportId: job.reportId))

                    // 2. Poll for status
                    var attempts = 0
                    let maxAttempts = 60  // 3 minutes max (3s * 60)

                    while attempts < maxAttempts {
                        try await Task.sleep(nanoseconds: 3_000_000_000)  // 3 seconds

                        let status = try await apiService.request(
                            endpoint: .reportStatus(job.reportId),
                            responseType: ReportStatusResponse.self
                        )

                        switch status.status {
                        case "processing":
                            continuation.yield(.progress(
                                reportId: job.reportId,
                                percent: status.progress,
                                step: status.currentStep
                            ))

                        case "completed":
                            let report = try await fetchReport(job.reportId)
                            continuation.yield(.completed(report))
                            continuation.finish()
                            return

                        case "failed":
                            throw ResearchError.generationFailed(status.errorMessage)

                        default:
                            break
                        }

                        attempts += 1
                    }

                    throw ResearchError.timeout

                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
}

// MARK: - ViewModel Usage
@MainActor
final class ResearchViewModel {

    func generateAnalysis(stockId: String) {
        generationTask = Task {
            do {
                for try await progress in researchRepository.generateAndAwaitReport(
                    stockId: stockId,
                    persona: appState.research.selectedPersona
                ) {
                    switch progress {
                    case .started(let reportId):
                        currentReportId = reportId
                        generationProgress = 0

                    case .progress(_, let percent, let step):
                        generationProgress = percent
                        generationStep = step

                    case .completed(let report):
                        appState.research.reports.insert(report, at: 0)
                        generationProgress = 100
                        showCompletionAnimation()
                    }
                }
            } catch {
                appState.globalError = .fromError(error)
            }
        }
    }
}
```

---

## 6. Error Handling Strategy

### 6.1 Error Classification

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ERROR TAXONOMY                                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    NETWORK ERRORS                                    │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │    │
│  │  │  Offline    │  │  Timeout    │  │  Server     │                  │    │
│  │  │  (no conn)  │  │  (slow)     │  │  (5xx)      │                  │    │
│  │  │             │  │             │  │             │                  │    │
│  │  │ Retry: No   │  │ Retry: Yes  │  │ Retry: Yes  │                  │    │
│  │  │ Action: Wait│  │ Action: Auto│  │ Action: Auto│                  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    BUSINESS ERRORS                                   │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │    │
│  │  │ Auth Failed │  │ No Credits  │  │ Not Found   │                  │    │
│  │  │  (401)      │  │  (403)      │  │  (404)      │                  │    │
│  │  │             │  │             │  │             │                  │    │
│  │  │ Action:     │  │ Action:     │  │ Action:     │                  │    │
│  │  │ Re-login    │  │ Upgrade     │  │ Go back     │                  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    VALIDATION ERRORS                                 │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │    │
│  │  │ Invalid     │  │ Missing     │  │ Rate        │                  │    │
│  │  │ Input (422) │  │ Field       │  │ Limited(429)│                  │    │
│  │  │             │  │             │  │             │                  │    │
│  │  │ Action:     │  │ Action:     │  │ Action:     │                  │    │
│  │  │ Show inline │  │ Highlight   │  │ Wait + retry│                  │    │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Backend Error Response Standard

```python
# schemas/common.py

class ErrorCode(str, Enum):
    """Standardized error codes for client handling."""

    # Authentication (1xxx)
    AUTH_TOKEN_EXPIRED = "AUTH_1001"
    AUTH_INVALID_TOKEN = "AUTH_1002"
    AUTH_UNAUTHORIZED = "AUTH_1003"

    # Business Logic (2xxx)
    CREDITS_INSUFFICIENT = "BIZ_2001"
    CREDITS_LIMIT_REACHED = "BIZ_2002"
    REPORT_GENERATION_FAILED = "BIZ_2003"
    STOCK_NOT_FOUND = "BIZ_2004"

    # Validation (3xxx)
    VALIDATION_FAILED = "VAL_3001"
    INVALID_TICKER = "VAL_3002"
    INVALID_PERSONA = "VAL_3003"

    # External Services (4xxx)
    GEMINI_ERROR = "EXT_4001"
    FMP_ERROR = "EXT_4002"
    DATABASE_ERROR = "EXT_4003"


class APIError(BaseModel):
    """Standardized error response."""
    error_code: ErrorCode
    message: str
    user_message: str  # User-friendly, actionable
    details: Optional[Dict[str, Any]] = None
    retry_after: Optional[int] = None  # Seconds (for rate limiting)
    action: Optional[str] = None  # Suggested action (upgrade, retry, etc.)

    class Config:
        json_schema_extra = {
            "example": {
                "error_code": "BIZ_2001",
                "message": "User has insufficient credits for deep research",
                "user_message": "You've used all your research credits this month.",
                "details": {"current_credits": 0, "required": 1},
                "action": "upgrade"
            }
        }


# Custom exception handler
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content=APIError(
            error_code=exc.error_code,
            message=exc.message,
            user_message=exc.user_message,
            details=exc.details,
            action=exc.action
        ).dict()
    )
```

### 6.3 iOS Error Handling

```swift
// MARK: - Domain Errors
enum AppError: Error, Identifiable {
    case network(NetworkError)
    case auth(AuthError)
    case business(BusinessError)
    case validation(ValidationError)

    var id: String { localizedDescription }

    var userMessage: String {
        switch self {
        case .network(.offline):
            return "No internet connection. Please check your network."
        case .network(.timeout):
            return "Request timed out. Please try again."
        case .auth(.tokenExpired):
            return "Your session has expired. Please sign in again."
        case .business(.insufficientCredits):
            return "You've used all your research credits this month."
        case .validation(let error):
            return error.message
        default:
            return "Something went wrong. Please try again."
        }
    }

    var suggestedAction: ErrorAction? {
        switch self {
        case .auth(.tokenExpired), .auth(.invalidToken):
            return .reAuthenticate
        case .business(.insufficientCredits):
            return .showUpgrade
        case .network(.offline):
            return .waitForConnection
        case .network(.serverError), .network(.timeout):
            return .retry
        default:
            return nil
        }
    }

    var isRetryable: Bool {
        switch self {
        case .network(.timeout), .network(.serverError):
            return true
        default:
            return false
        }
    }
}

// MARK: - Error Presentation
struct ErrorBanner: View {
    let error: AppError
    let onDismiss: () -> Void
    let onAction: (() -> Void)?

    var body: some View {
        HStack {
            Image(systemName: error.icon)
            Text(error.userMessage)
            Spacer()

            if let action = error.suggestedAction {
                Button(action.title) {
                    onAction?()
                }
            }
        }
        .padding()
        .background(error.backgroundColor)
    }
}

// MARK: - Global Error Handler
extension AppState {
    func handleError(_ error: Error) {
        let appError = AppError.from(error)

        // Log for debugging
        Logger.error("App error: \(appError)")

        // Handle auth errors immediately
        if case .auth(.tokenExpired) = appError {
            Task { await forceLogout() }
            return
        }

        // Set for UI display
        self.globalError = appError
    }
}
```

### 6.4 Retry Strategy

```swift
// MARK: - Exponential Backoff Retry
struct RetryPolicy {
    let maxAttempts: Int
    let initialDelay: TimeInterval
    let maxDelay: TimeInterval
    let multiplier: Double

    static let standard = RetryPolicy(
        maxAttempts: 3,
        initialDelay: 1.0,
        maxDelay: 10.0,
        multiplier: 2.0
    )

    static let aggressive = RetryPolicy(
        maxAttempts: 5,
        initialDelay: 0.5,
        maxDelay: 30.0,
        multiplier: 2.0
    )
}

extension APIService {
    func requestWithRetry<T: Decodable>(
        endpoint: Endpoint,
        responseType: T.Type,
        policy: RetryPolicy = .standard
    ) async throws -> T {
        var lastError: Error?
        var delay = policy.initialDelay

        for attempt in 1...policy.maxAttempts {
            do {
                return try await request(endpoint: endpoint, responseType: responseType)
            } catch let error as AppError where error.isRetryable {
                lastError = error
                Logger.warning("Request failed (attempt \(attempt)/\(policy.maxAttempts)): \(error)")

                if attempt < policy.maxAttempts {
                    try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    delay = min(delay * policy.multiplier, policy.maxDelay)
                }
            } catch {
                throw error  // Non-retryable, throw immediately
            }
        }

        throw lastError ?? AppError.network(.unknown)
    }
}
```

---

## 7. Caching & Performance

### 7.1 Multi-Layer Cache Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CACHING LAYERS                                       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     iOS CLIENT                                       │    │
│  │                                                                       │    │
│  │  L1: In-Memory (NSCache)                                             │    │
│  │      ├── TTL: 5 minutes                                               │    │
│  │      ├── Size: 50MB max                                               │    │
│  │      └── Eviction: LRU                                                │    │
│  │                                                                       │    │
│  │  L2: Disk Cache (FileManager)                                         │    │
│  │      ├── TTL: 24 hours (configurable per resource)                   │    │
│  │      ├── Size: 200MB max                                              │    │
│  │      └── Location: /Caches (can be purged by OS)                     │    │
│  │                                                                       │    │
│  │  L3: Persistent Storage (Core Data / SwiftData)                      │    │
│  │      ├── User data (watchlist, settings, generated reports)          │    │
│  │      └── Location: /Documents (backed up, never purged)              │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     BACKEND                                          │    │
│  │                                                                       │    │
│  │  L1: Redis Cache                                                      │    │
│  │      ├── Stock quotes: TTL 1 min                                     │    │
│  │      ├── Company profiles: TTL 24 hours                              │    │
│  │      ├── News feed: TTL 5 min                                        │    │
│  │      └── User sessions: TTL 7 days                                   │    │
│  │                                                                       │    │
│  │  L2: Supabase (PostgreSQL)                                           │    │
│  │      └── Persistent data store                                        │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Cache Invalidation Strategy

```swift
// MARK: - Cache Policy
enum CachePolicy {
    case cacheFirst        // Return cache, refresh in background
    case networkFirst      // Try network, fallback to cache
    case cacheOnly         // Only cache, no network
    case networkOnly       // Only network, no cache
    case staleWhileRevalidate(maxStale: TimeInterval)
}

// MARK: - Cache Keys
enum CacheKey {
    case stock(ticker: String)
    case stockFundamentals(ticker: String)
    case newsFeed(page: Int)
    case newsArticle(id: String)
    case researchReport(id: String)
    case userProfile
    case watchlist

    var key: String {
        switch self {
        case .stock(let ticker): return "stock_\(ticker)"
        case .stockFundamentals(let ticker): return "fundamentals_\(ticker)"
        case .newsFeed(let page): return "news_feed_\(page)"
        case .newsArticle(let id): return "news_\(id)"
        case .researchReport(let id): return "report_\(id)"
        case .userProfile: return "user_profile"
        case .watchlist: return "watchlist"
        }
    }

    var defaultTTL: TimeInterval {
        switch self {
        case .stock: return 60            // 1 minute (prices change)
        case .stockFundamentals: return 86400  // 24 hours (quarterly data)
        case .newsFeed: return 300        // 5 minutes
        case .newsArticle: return 3600    // 1 hour
        case .researchReport: return 86400 * 7  // 7 days (static once generated)
        case .userProfile: return 600     // 10 minutes
        case .watchlist: return 300       // 5 minutes
        }
    }
}
```

### 7.3 Backend Caching Decorators

```python
# cache_decorators.py

from functools import wraps
from typing import Callable, Optional
import hashlib
import json

def cached(
    ttl: int = 300,
    key_prefix: str = "",
    vary_on: Optional[list] = None
):
    """
    Caching decorator for service methods.

    Args:
        ttl: Time-to-live in seconds
        key_prefix: Prefix for cache key
        vary_on: Parameters to include in cache key
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Build cache key
            key_parts = [key_prefix, func.__name__]

            if vary_on:
                for param in vary_on:
                    if param in kwargs:
                        key_parts.append(f"{param}:{kwargs[param]}")

            cache_key = ":".join(key_parts)

            # Check cache
            cached_value = await cache_manager.get(cache_key)
            if cached_value is not None:
                return cached_value

            # Execute function
            result = await func(*args, **kwargs)

            # Store in cache
            await cache_manager.set(cache_key, result, ttl=ttl)

            return result
        return wrapper
    return decorator


# Usage in service
class StockService:

    @cached(ttl=60, key_prefix="stock", vary_on=["ticker"])
    async def get_quote(self, ticker: str) -> dict:
        return await self.fmp.get_quote(ticker)

    @cached(ttl=86400, key_prefix="profile", vary_on=["ticker"])
    async def get_company_profile(self, ticker: str) -> dict:
        return await self.fmp.get_company_profile(ticker)
```

---

## 8. API Contract Standards

### 8.1 Response Envelope

All API responses should follow a consistent structure:

```json
// Success Response
{
  "success": true,
  "data": { /* payload */ },
  "meta": {
    "request_id": "uuid",
    "timestamp": "ISO-8601",
    "cache_hit": false,
    "version": "1.0"
  }
}

// Error Response
{
  "success": false,
  "error": {
    "code": "BIZ_2001",
    "message": "Insufficient credits",
    "user_message": "You've used all your research credits.",
    "action": "upgrade"
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "ISO-8601"
  }
}

// Paginated Response
{
  "success": true,
  "data": [ /* items */ ],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total_items": 150,
    "total_pages": 8,
    "has_next": true,
    "has_prev": false
  },
  "meta": { /* ... */ }
}
```

### 8.2 Versioning Strategy

```
/api/v1/stocks/{ticker}    ← Current version
/api/v2/stocks/{ticker}    ← Future breaking changes

Headers:
  Accept-Version: 1.0      ← Optional version override
  X-API-Version: 1.0       ← Response version indicator
```

### 8.3 Rate Limiting Headers

```
X-RateLimit-Limit: 100          # Max requests per window
X-RateLimit-Remaining: 95       # Requests remaining
X-RateLimit-Reset: 1704067200   # Unix timestamp when limit resets
Retry-After: 60                 # Seconds to wait (on 429)
```

---

## 9. Security Architecture

### 9.1 Authentication Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     AUTHENTICATION FLOW                                      │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        iOS CLIENT                                     │   │
│  │                                                                        │   │
│  │  1. User signs in with Apple/Google via Supabase Auth                 │   │
│  │     └── Returns: supabase_access_token                                │   │
│  │                                                                        │   │
│  │  2. Exchange for app token:                                            │   │
│  │     POST /api/v1/auth/token                                           │   │
│  │     Body: { supabase_token: "..." }                                   │   │
│  │     └── Returns: { access_token, refresh_token, expires_in }         │   │
│  │                                                                        │   │
│  │  3. Store tokens securely:                                             │   │
│  │     └── Keychain (access_token, refresh_token)                        │   │
│  │                                                                        │   │
│  │  4. Include in all requests:                                           │   │
│  │     └── Header: Authorization: Bearer {access_token}                  │   │
│  │                                                                        │   │
│  │  5. On 401 error:                                                      │   │
│  │     └── POST /api/v1/auth/refresh { refresh_token }                   │   │
│  │     └── Update stored tokens                                          │   │
│  │     └── Retry original request                                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        BACKEND                                         │   │
│  │                                                                        │   │
│  │  Token Structure (JWT):                                                │   │
│  │  {                                                                     │   │
│  │    "sub": "user-uuid",                                                │   │
│  │    "email": "user@example.com",                                       │   │
│  │    "tier": "pro",                                                     │   │
│  │    "iat": 1704067200,                                                 │   │
│  │    "exp": 1704153600  // 24 hours                                     │   │
│  │  }                                                                     │   │
│  │                                                                        │   │
│  │  Validation:                                                           │   │
│  │    1. Verify JWT signature                                            │   │
│  │    2. Check expiration                                                │   │
│  │    3. Validate user exists in Supabase                                │   │
│  │    4. Row Level Security (RLS) enforces data access                   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Data Protection

| Data Type | iOS Storage | Backend Storage | Encryption |
|-----------|-------------|-----------------|------------|
| Auth Tokens | Keychain | N/A | AES-256 (Keychain) |
| User Profile | Core Data | Supabase (RLS) | At-rest (Supabase) |
| Research Reports | Core Data + Cache | Supabase (RLS) | At-rest |
| API Keys | N/A | Environment vars | N/A (never in code) |

---

## 10. Recommendations & Critique

### 10.1 Current Architecture Strengths

1. **Clean Separation of Concerns**: Backend layers (API → Service → Agent → Integration) are well-defined
2. **Atomic Design for iOS UI**: Good reusability with Atoms/Molecules/Organisms pattern
3. **Background Tasks**: Using FastAPI's BackgroundTasks for long-running operations
4. **Investor Personas**: Well-structured prompts with clear differentiation

### 10.2 Areas for Improvement

#### Backend

| Issue | Current State | Recommendation |
|-------|---------------|----------------|
| **Background Tasks** | Using FastAPI BackgroundTasks | Consider Celery/Redis Queue for production scale. BackgroundTasks don't survive server restarts. |
| **No Status Updates** | Report status stored but no real-time push | Add WebSocket endpoint or SSE for live progress updates |
| **Circular Imports** | `generate_report_task` has inline imports | Move task functions to separate `tasks/` module |
| **Missing Middleware** | No request ID propagation | Add correlation ID middleware for distributed tracing |
| **Error Granularity** | Generic error messages | Implement structured error codes (see Section 6.2) |

#### Frontend (iOS)

| Issue | Current State | Recommendation |
|-------|---------------|----------------|
| **No Networking Layer** | ViewModels use mock data | Implement Repository pattern with URLSession |
| **Isolated State** | Each ViewModel manages own state | Centralized AppState (see Section 4) |
| **No Offline Support** | Assumed always online | Add Core Data + offline-first caching |
| **No Retry Logic** | Single request attempts | Implement exponential backoff (see Section 6.4) |
| **Hardcoded Personas** | Some mismatch with backend | Sync personas from backend config |

### 10.3 Architecture Evolution Roadmap

```
Phase 1 (Current): Foundation
├── ✅ Basic MVVM structure
├── ✅ Atomic Design components
├── ✅ Backend layered architecture
└── 🔲 Repository pattern (iOS)

Phase 2: Networking & State
├── 🔲 Centralized AppState
├── 🔲 API Service layer (iOS)
├── 🔲 Multi-layer caching
└── 🔲 Error handling framework

Phase 3: Real-time & Offline
├── 🔲 WebSocket for live updates
├── 🔲 Core Data persistence
├── 🔲 Offline-first sync
└── 🔲 Background refresh (iOS)

Phase 4: Scale & Observability
├── 🔲 Celery task queue (Backend)
├── 🔲 Distributed tracing
├── 🔲 Performance monitoring
└── 🔲 A/B testing infrastructure
```

### 10.4 Immediate Action Items

1. **High Priority**
   - [ ] Create `Services/` folder in iOS with `APIService` and `CacheManager`
   - [ ] Implement `AppState` observable container
   - [ ] Add structured error handling to backend endpoints
   - [ ] Create polling mechanism for report generation status

2. **Medium Priority**
   - [ ] Add Redis caching decorators to frequently-called endpoints
   - [ ] Implement token refresh interceptor in iOS
   - [ ] Create Core Data models for offline persistence
   - [ ] Add request/response logging middleware

3. **Nice to Have**
   - [ ] WebSocket endpoint for real-time progress
   - [ ] Push notifications for completed reports
   - [ ] Background app refresh for watchlist updates

---

## Appendix A: File Structure (Recommended)

### iOS

```
ios/
├── App/
│   ├── AIValueInvestorApp.swift
│   └── AppDelegate.swift
├── Core/
│   ├── State/
│   │   ├── AppState.swift
│   │   ├── AuthState.swift
│   │   ├── UserState.swift
│   │   └── ...
│   ├── Services/
│   │   ├── APIService.swift
│   │   ├── CacheManager.swift
│   │   └── PersistenceManager.swift
│   ├── Repositories/
│   │   ├── StockRepository.swift
│   │   ├── ResearchRepository.swift
│   │   └── ...
│   └── Utilities/
│       ├── Logger.swift
│       └── Extensions/
├── Features/
│   ├── Home/
│   │   ├── HomeView.swift
│   │   └── HomeViewModel.swift
│   ├── Research/
│   │   ├── ResearchView.swift
│   │   └── ResearchViewModel.swift
│   └── ...
├── SharedUI/
│   ├── Atoms/
│   ├── Molecules/
│   └── Organisms/
├── Models/
│   ├── Domain/
│   │   ├── Stock.swift
│   │   └── ResearchReport.swift
│   └── DTO/
│       ├── StockResponse.swift
│       └── ResearchResponse.swift
└── Resources/
    └── Assets.xcassets
```

### Backend

```
backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/
│   │       └── dependencies.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   └── middleware.py
│   ├── services/
│   │   └── *.py
│   ├── agents/
│   │   └── *.py
│   ├── integrations/
│   │   └── *.py
│   ├── schemas/
│   │   └── *.py
│   ├── models/           # SQLAlchemy models (if needed)
│   ├── tasks/            # Background task definitions (NEW)
│   │   ├── research_tasks.py
│   │   └── news_tasks.py
│   └── main.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── requirements.txt
```

---

## Appendix B: Decision Log

| Date | Decision | Context | Alternatives Considered |
|------|----------|---------|------------------------|
| Jan 2026 | Use polling over WebSocket for v1 | Simpler implementation, works offline | WebSocket, SSE, Push Notifications |
| Jan 2026 | Centralized AppState over distributed | Consistency, simpler debugging | Multiple @Observable objects, Redux-like |
| Jan 2026 | Repository pattern | Testability, abstraction | Direct API calls in ViewModels |
| Jan 2026 | FastAPI BackgroundTasks for v1 | Quick implementation | Celery, Redis Queue, Dramatiq |

---

**Document End**

*This document should be reviewed quarterly and updated as the architecture evolves.*
