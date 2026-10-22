//
//  ETFDetailViewModel.swift
//  ios
//
//  ViewModel for the ETF Detail screen.
//  Fetches real data from GET /api/v1/etfs/{symbol}?range=3M
//  and maps the response DTO to display models.
//  Range/interval taps and the 30 s refresh use the light GET /api/v1/etfs/{symbol}/quote;
//  older bars come from GET /api/v1/etfs/{symbol}/chart/history (via ChartHistoryLoader).
//  Until the full detail lands, a 3M+ first paint and a range tap also ask the bars-only
//  GET /api/v1/etfs/{symbol}/chart (see `fetchBarsOnlyChart`).
//

import Foundation
import SwiftUI
import Combine
import os

@MainActor
class ETFDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var etfData: ETFDetailData?
    /// Fast-core first paint. Populated by `/etfs/{symbol}/core`, which answers in
    /// ~0.3s from the cheap cached sections, and rendered by the header/chart gate as
    /// `etfData ?? coreData`.
    ///
    /// Written ONLY while `etfData` is still nil (see `loadCore`), so a slow core
    /// response can never overwrite the full model. Mirrors `TickerDetailViewModel`.
    @Published var coreData: ETFCoreData?

    /// What the header + chart render: the full model when it has landed, the fast-core
    /// slice until then.
    ///
    /// Written as an `if let` rather than `etfData ?? coreData` on purpose — the two
    /// sides are different concrete types and only share the protocol, so the coalescing
    /// form depends on contextual coercion that is easy to break by accident.
    var headerData: (any ETFHeaderRenderable)? {
        if let etfData { return etfData }
        return coreData
    }
    @Published var newsArticles: [TickerNewsArticle] = []
    @Published var isNewsLoading: Bool = false
    @Published var hasMoreNews: Bool = false
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?
    @Published var selectedTab: ETFDetailTab = .overview
    @Published var selectedChartRange: ChartTimeRange = .threeMonths
    @Published var isFavorite: Bool = false
    @Published var aiInputText: String = ""
    @Published var pendingAIQuery: String?
    /// Where a tap wants to navigate, WITH the asset type.
    ///
    /// This was a bare `String?` and the screen hardcoded its own asset type for every
    /// value, so a related-ticker chip on a NEWS card — which carries US-listed EQUITY
    /// symbols, put there by the shared enrichment prompt — opened the wrong screen
    /// entirely: tapping "NVDA" on the BTC screen pushed CryptoDetailView("NVDA").
    /// Carrying the type with the symbol makes that unrepresentable.
    @Published var pendingTickerNavigation: SearchSelection?

    /// External link to show in the in-app browser. Set via `openExternal(_:into:)`
    /// and presented by the Screen's `.inAppBrowser(link:)` — a ViewModel cannot
    /// present a view itself.
    @Published var browserLink: BrowserLink?
    @Published var chartSettings = ChartSettings()
    /// The ONE place this screen names its asset class: the screen passes it to
    /// `TickerChartView`, and init resolves the remembered range/interval against it.
    let chartAssetContext: ChartAssetContext = .etf
    /// Bumped ONLY when the bars in hand belong to a different (range, interval) than the
    /// previous write — a user range or interval change — or when a write brings bars after
    /// one that had none (see `ChartBarsStamp.hasBars`). Never on a timer tick, the
    /// core→full swap or pull-to-refresh: the chart keeps the user's pan across those and
    /// resets to the range's default window only on a bump. It used to bump on EVERY write
    /// (and on every 30 s slice that carried bars), so a panned chart snapped back.
    @Published var chartDataVersion: Int = 0
    /// The range / interval the bars in hand were REQUESTED for — captured before the
    /// await, never the pill at landing time. The pill flips on the tap and the bars arrive
    /// later; the chart maps the bars by these, and veils itself while they differ.
    @Published var chartDataRange: ChartTimeRange?
    @Published var chartDataInterval: ChartInterval?
    /// The user's last range/interval change failed, so the chart holds no bars (never the
    /// previous range's bars under the new pill) and says so. Cleared by the next write.
    @Published var chartLoadFailed: Bool = false
    /// A user range/interval fetch is in flight — with no bars in hand the chart shows its
    /// loading block instead of "No chart data".
    @Published private(set) var isChartLoading: Bool = false
    /// Older bars for the chart, paged in as the user pans left. Fed every write of the
    /// chart bars (`didWriteChartBars`) so its store stays contiguous with them.
    let chartHistory: ChartHistoryLoader

    // MARK: - Live Price
    private var chartRefreshTask: Task<Void, Never>?

    // MARK: - Private Properties

    let etfSymbol: String
    private let repository: StockRepository = .shared
    private var cancellables = Set<AnyCancellable>()

    // MARK: - News (shared cache + deferred enrichment)
    /// Full set from the shared news cache; `newsArticles` is the paged slice.
    private var allNewsArticles: [TickerNewsArticle] = []
    private var newsDisplayCount: Int = 10
    private let newsPageSize: Int = 10
    /// Serialises enrichment (News-tab-appear vs fetch/load-more completion).
    private var isEnrichingNews = false
    /// Re-entrancy guard for `loadMoreNews` — the zero-height load-more sentinel
    /// re-fires the instant more rows render, so without this it cascades every
    /// remaining page in one tick (TickerDetail has the same guard).
    private var isLoadingMoreNews = false
    /// Monotonic token for the fetches that write the chart. fetchETFDetail writes the
    /// whole etfData snapshot and fetchChartForRange merges the light slice's bars into it;
    /// each captures the token before awaiting and applies its result only if still
    /// current, so a slow earlier range can't clobber the chart the user has since switched to.
    private var chartRequestToken = 0
    /// Monotonic token for `fetchBarsOnlyChart`, in the same style as `chartRequestToken` but
    /// its OWN counter: the bars-only answer is an accelerator BESIDE the full fetch, so it must
    /// never supersede (or be superseded by) the full fetch's token — the full detail still owns
    /// the chart, `isChartLoading` and the error path. Each bars-only ask bumps it, so an older
    /// answer landing after a newer tap's ask drops itself.
    private var barsOnlyChartToken = 0
    /// True while the range sink assigns the range's default interval, so the
    /// interval sink doesn't ALSO reload (one range change would otherwise fire two
    /// identical fetches when the range crosses an interval boundary).
    private var suppressIntervalReload = false

    /// What the last write of the chart bars was fetched for. The ETF routes serve the
    /// regular session only, so no extended-hours flag. `chartDataVersion` moves only when
    /// it changes.
    ///
    /// `hasBars` is part of it on purpose (as on the commodity / crypto / index VMs): a cold
    /// daily core carries NO bars, and the chart commits its window for an empty series as
    /// `.zero`. Without the flag the full payload lands under the SAME stamp, the chart keeps
    /// that window, and its clamp opens 3M on the last 10 bars. Empty → bars is never a pan
    /// the user made, so it re-commits the range's default window.
    private struct ChartBarsStamp: Equatable {
        let range: ChartTimeRange
        let interval: ChartInterval
        let hasBars: Bool
    }
    private var lastChartStamp: ChartBarsStamp?
    private let log = Logger(subsystem: "com.caydex.ios", category: "ETFDetailViewModel")

    // MARK: - Initialization

    init(etfSymbol: String) {
        self.etfSymbol = etfSymbol
        // `repository` is a fixed `.shared`, not injected, so the loader takes the same
        // default.
        self.chartHistory = ChartHistoryLoader(asset: .etfs)

        // Seed the range AND interval as a resolved pair BEFORE the sinks are wired — the
        // user's remembered range and that range's interval, resolved for an ETF (a 2Y
        // picked on crypto opens 1Y; the ETF routes 400 "2Y"), else 3M + its default.
        //
        // The interval must never be left at `ChartSettings.selectedInterval`'s `.fiveMin`:
        // every cold open used to request `?range=3M&interval=5min` — a pair the picker cannot
        // produce (`ChartTimeRange.allowedIntervals` excludes it), so the backend silently
        // fell back to `daily` and cached the response under a key no later request would
        // ever reuse. It also made `selectedInterval.isIntraday` true on a daily chart, which
        // un-gated the 30-second refresh timer.
        //
        // Assigning here fires nothing: both sinks are registered below and drop their first
        // value at subscribe time. Read-only — a fallback is never stored over the preference.
        let restored = ChartSelectionMemory.restoredSelection(in: chartAssetContext, screenDefault: selectedChartRange)
        selectedChartRange = restored.range
        chartSettings.selectedInterval = restored.interval

        // Observe chart range changes: auto-set the interval and manage timer
        $selectedChartRange
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] newRange in
                guard let self = self else { return }
                // Setting the interval fires the interval sink SYNCHRONOUSLY; suppress
                // its reload so a range change drives exactly one fetch (not two when
                // the new range crosses an interval boundary).
                self.suppressIntervalReload = true
                self.chartSettings.selectedInterval = ChartSelectionMemory.rememberedInterval(for: newRange, in: self.chartAssetContext)
                self.suppressIntervalReload = false

                // Restart or stop chart refresh timer based on new range
                // The timer runs on EVERY range — same correction as IndexDetailViewModel
                // (2026-09-12). Stopping it on a non-intraday range permanently killed the
                // 30-second LEVEL refresh the moment the user tapped 3M, with nothing to
                // restart it, while this timer's own body already decides per tick whether
                // to include chart bars and says so: "The level header refreshes either
                // way — the old `isIntraday` guard froze it entirely on a daily chart."
                self.startChartRefreshTimer()

                Task { await self.fetchChartForRange(newRange) }
            }
            .store(in: &cancellables)

        // Observe interval changes and re-fetch chart data (manual interval picker)
        chartSettings.$selectedInterval
            .dropFirst()
            .removeDuplicates()
            .sink { [weak self] _ in
                guard let self = self else { return }
                // Skip the reload the range sink already owns (see suppress flag).
                guard !self.suppressIntervalReload else { return }
                Task { await self.fetchChartForRange(self.selectedChartRange) }
            }
            .store(in: &cancellables)

        // The screen reads `chartHistory.state` through this view model, so the loader's
        // changes must re-publish here — an older page landing has to redraw the chart.
        chartHistory.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
    }

    // MARK: - Data Loading

    func loadETFData() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }
            // Fast core, in parallel with the full detail: whichever lands first paints.
            async let coreTask: () = self.loadCore()
            async let detailTask: () = self.fetchETFDetail()
            async let newsTask: () = self.fetchETFNews()
            async let watchlistTask: () = self.checkWatchlistStatus()
            _ = await (coreTask, detailTask, newsTask, watchlistTask)
        }
    }

    func refresh() async {
        // Drop this asset's CLIENT-side cache first — otherwise the gesture does no
        // network work for anything served by StockRepository (news 60s, analyst /
        // sentiment / technical 30 min, ETF profile + holdings-risk + dividends 24h
        // against a process-lifetime singleton). Backend caches still absorb the
        // upstream cost; this only bypasses the on-device copy.
        StockRepository.shared.invalidate(symbol: etfSymbol)
        await fetchETFDetail()
        await fetchETFNews()
    }

    /// Fetches ETF detail from the backend and maps to display models.
    private func fetchETFDetail() async {
        await fetchETFDetail(range: selectedChartRange, interval: chartSettings.selectedInterval)
    }

    /// The full detail for `range` / `interval`, captured by the CALLER before anything
    /// awaits — the bars are stamped with what was requested, never the pill at landing time.
    private func fetchETFDetail(range: ChartTimeRange, interval: ChartInterval) async {
        let startTime = CFAbsoluteTimeGetCurrent()
        chartRequestToken += 1
        let token = chartRequestToken

        do {
            log.debug("ETF detail fetch for \(self.etfSymbol, privacy: .public) range \(range.rawValue, privacy: .public) interval \(interval.rawValue, privacy: .public)")

            let response = try await repository.getETFDetail(
                symbol: etfSymbol,
                range: range.rawValue,
                interval: interval.rawValue
            )

            let elapsed = CFAbsoluteTimeGetCurrent() - startTime
            log.debug("ETF detail loaded in \(String(format: "%.2f", elapsed), privacy: .public)s — \(response.symbol, privacy: .public), \(response.chartData.count) bars")

            // Apply only if still the latest request — a newer range change may have
            // already painted fresher data that this slower response must not clobber.
            // The SAME token gates errorMessage + streaming so a stale success can't
            // clear a newer request's error banner or start streaming on stale data.
            if token == self.chartRequestToken {
                self.errorMessage = nil
                let model = response.toDisplayModel()
                self.etfData = model
                self.isChartLoading = false
                // Core→full and pull-to-refresh carry the same stamp, so this keeps the
                // user's window; it bumps when the full payload is for a new range, or brings
                // the first bars after a bar-less core (there was no window to keep).
                self.didWriteChartBars(model.chartPricePoints, range: range, interval: interval)
                // News now comes from the SHARED cache via `fetchETFNews()`, not
                // this detail payload — so ETF news matches the Updates feed and
                // gets AI enrichment. (The payload still carries `news_articles`;
                // we just don't use it here.)

                // Start live price streaming + chart refresh if market is active.
                self.maybeStartStreaming()
            }
            self.isLoading = false

        } catch {
            let elapsed = CFAbsoluteTimeGetCurrent() - startTime
            log.error("ETF detail failed for \(self.etfSymbol, privacy: .public) range \(range.rawValue, privacy: .public) after \(String(format: "%.2f", elapsed), privacy: .public)s — \(String(describing: error), privacy: .public)")

            self.isLoading = false

            // Load fallback data so the screen isn't empty — but only if this is
            // still the latest request, so a STALE fetch's failure can't clobber
            // data a newer range fetch already painted.
            if token == self.chartRequestToken {
                self.isChartLoading = false
                self.errorMessage = "Unable to load ETF data. Pull to refresh."
                // This was a range change made before the full model landed (or a refresh
                // that superseded one): the fast-core bars on screen belong to the old range.
                self.clearChartAfterFailedRequest(range: range, interval: interval)
                loadFallbackData()
            }
        }
    }

    /// Reload chart data when user changes the time range.
    func updateChartRange(_ range: ChartTimeRange) {
        selectedChartRange = range
    }

    /// Called by the Combine observers when the range or interval changes.
    ///
    /// The light `/quote` slice carries the bars for any range, so a tap re-requests only
    /// those — it used to re-download the whole ETF detail (holdings, dividends, profile,
    /// strategy, performance) and replace the model wholesale on every pill tap.
    private func fetchChartForRange(_ range: ChartTimeRange) async {
        let interval = chartSettings.selectedInterval
        log.debug("ETF chart range → \(range.rawValue, privacy: .public) \(interval.rawValue, privacy: .public)")
        // A new ask: the previous answer's failure note gives way to the loading block.
        chartLoadFailed = false
        isChartLoading = true

        // A range/interval tap while the FULL detail is still in flight (the fast-core
        // chart is interactive before it lands): there is no model to merge a slice into,
        // so re-request the full payload for the new range, as before. Its token bump
        // supersedes the first fetch, so this is now the screen's load.
        //
        // The full payload is 2-5 s cold, so the bars-only route runs BESIDE it and paints
        // the new range's chart on the core model as soon as it answers (~0.1-0.7 s). The
        // full fetch still owns the chart once it lands, `isChartLoading` and the error UI;
        // the bars-only answer drops itself if the full model beat it, the pills moved on,
        // or a newer tap asked again (see `fetchBarsOnlyChart`). Awaited, never left to the
        // implicit cancel at scope exit: if the full fetch FAILS first, the bars can still
        // land and replace its "Couldn't load this range" note with this range's chart.
        guard etfData != nil else {
            async let barsOnly: () = self.fetchBarsOnlyChart(range: range, interval: interval)
            await fetchETFDetail(range: range, interval: interval)
            await barsOnly
            return
        }

        chartRequestToken += 1
        let token = chartRequestToken

        do {
            let light = try await repository.getETFQuote(
                symbol: self.etfSymbol,
                range: range.rawValue,
                interval: interval.rawValue
            )

            // Drop a stale response so a slow earlier range can't overwrite the chart
            // the user has since switched to (last-write-wins).
            guard token == self.chartRequestToken, let current = self.etfData else { return }
            var model = light.merged(into: current, includeChart: true)
            // A USER range change shows ITS answer, even an empty one: `merged` keeps the
            // previous bars on an empty slice (right for a timer tick), but those belong to
            // the PREVIOUS range and would sit under the new pill.
            if light.chartData.isEmpty {
                model.chartPricePoints = []
            }
            self.etfData = model
            self.isChartLoading = false
            self.didWriteChartBars(model.chartPricePoints, range: range, interval: interval)
            log.debug("ETF chart range \(range.rawValue, privacy: .public) updated — \(model.chartPricePoints.count) bars")
            // News is loaded once via `fetchETFNews()` (shared cache), not
            // re-pulled on every chart-range change. No re-arm here: a model already
            // existed, so the path that painted it armed the 30-second refresh, and the
            // range sink re-arms it on every tap anyway.

        } catch {
            guard token == self.chartRequestToken else { return }
            log.error("ETF chart range \(range.rawValue, privacy: .public) \(interval.rawValue, privacy: .public) failed for \(self.etfSymbol, privacy: .public) — \(String(describing: error), privacy: .public)")
            self.isChartLoading = false
            self.clearChartAfterFailedRequest(range: range, interval: interval)
        }
    }

    // MARK: - Chart bars bookkeeping

    /// Call at EVERY write of the chart bars, with the range / interval those bars were
    /// REQUESTED for. Feeds the history loader (so its older pages stay contiguous with the
    /// bars in hand), stamps the bars, and bumps `chartDataVersion` only on a new stamp.
    private func didWriteChartBars(
        _ bars: [StockPricePoint],
        range: ChartTimeRange,
        interval: ChartInterval,
        failed: Bool = false
    ) {
        // Written only on a change: `@Published` re-publishes an equal value, and this runs
        // on every 30 s tick that carries bars.
        if chartDataRange != range { chartDataRange = range }
        if chartDataInterval != interval { chartDataInterval = interval }
        if chartLoadFailed != failed { chartLoadFailed = failed }
        // The ETF routes are regular-session only: never extended hours here.
        chartHistory.absorb(bars, symbol: etfSymbol, range: range, interval: interval, extendedHours: false)
        let stamp = ChartBarsStamp(range: range, interval: interval, hasBars: !bars.isEmpty)
        if stamp != lastChartStamp {
            lastChartStamp = stamp
            chartDataVersion += 1
        }
    }

    /// A chart request for `range` / `interval` failed. When the bars on screen belong to
    /// ANOTHER selection they must not stay under this pill: clear them and stamp the
    /// request, so the chart shows "Couldn't load this range". When they already ARE this
    /// selection's bars (the user tapped away and back, a refresh failed) they stay — and
    /// if there are none, the note still says the load failed.
    private func clearChartAfterFailedRequest(range: ChartTimeRange, interval: ChartInterval) {
        if let shown = lastChartStamp, shown.range == range, shown.interval == interval {
            if !shown.hasBars { chartLoadFailed = true }
            return
        }
        // The header + chart render `etfData ?? coreData`: clear the one on screen.
        if etfData != nil {
            etfData?.chartPricePoints = []
        } else {
            coreData?.chartPricePoints = []
        }
        didWriteChartBars([], range: range, interval: interval, failed: true)
    }

    /// Light refresh: merge the volatile slice into `etfData` IN PLACE.
    ///
    /// Replaces a `fetchChartForRange` call that, despite its name, re-requested the entire
    /// detail payload and then did `self.etfData = response.toDisplayModel()` — a wholesale
    /// replacement. Two consequences, both fixed here:
    ///
    ///  1. Every WebSocket tick that had landed since the last refresh was ERASED, on a
    ///     30-second sawtooth, on a screen whose whole point is a live price. The
    ///     live-patched last candle went with it.
    ///  2. The poll re-fetched holdings, dividends, profile, strategy and performance —
    ///     none of which can change in 30 seconds.
    ///
    /// The socket WINS over the REST snapshot: a tick is now, a snapshot is up to 45s old.
    private func refreshLiveSlice(includeChart: Bool) async {
        // OBSERVE the token, never advance it. This runs from the 30-second timer only;
        // advancing it here discarded an in-flight USER range fetch (cold range = 0.5–5 s
        // of FMP history) whenever a tick landed mid-flight — and since the tick passes
        // `includeChart: false` on a daily range it fetched no bars either, so the chart
        // stayed on the OLD range under the NEW pill with nothing left to retry. Same
        // discipline as `CryptoDetailViewModel.refreshChartOnly`. A tick that lands after
        // a newer user fetch still drops itself (the token moved).
        let token = chartRequestToken
        // What this tick asks for, captured before the await: its bars are stamped with it.
        let range = selectedChartRange
        let interval = chartSettings.selectedInterval
        do {
            let light = try await repository.getETFQuote(
                symbol: etfSymbol,
                range: includeChart ? range.rawValue : nil,
                interval: includeChart ? interval.rawValue : nil
            )
            // Drop a stale response so rapid range switching can't clobber a newer range.
            guard token == self.chartRequestToken, let current = self.etfData else { return }

            let model = light.merged(into: current, includeChart: includeChart)
            self.etfData = model
            if includeChart, !light.chartData.isEmpty {
                // A tail merge with the same stamp: the chart keeps the user's window.
                self.didWriteChartBars(model.chartPricePoints, range: range, interval: interval)
            }
        } catch {
            log.error("ETF live slice refresh failed for \(self.etfSymbol, privacy: .public) — \(String(describing: error), privacy: .public)")
        }
    }

    /// Arm the 30-second live-slice refresh. Idempotent — any path that paints etfData
    /// (initial fetch OR a range change that supersedes it) can call it, and
    /// `startChartRefreshTimer` cancels before it re-arms.
    ///
    /// UNCONDITIONAL, on purpose. The `!livePriceManager.isConnected` guard that opened
    /// this is gone with the socket, and the `shouldStreamLivePrice(for:
    /// etfData.marketStatus)` guard that replaced it was exactly the latch the note below
    /// warned against: `marketStatus` is the value captured at the LAST fetch, not the
    /// clock. Open SPY at 20:30 ET (`.closed`), background overnight, foreground at 09:40
    /// — the model still said `.closed`, so nothing armed, and the header kept last
    /// night's price under a "Market Closed" badge for the whole live session until a
    /// pull-to-refresh. The timer body already gates every tick on
    /// `MarketHoursUtil.isMarketActive()` (the wall clock), so arming while closed costs
    /// one `continue` per 30 s, and the first live tick rewrites `marketStatus` through
    /// `merged(into:)`, healing the badge. Same shape as `IndexDetailViewModel` at load
    /// and `CommodityDetailView` on foreground. Nothing that can latch may go back here —
    /// this timer is the only thing refreshing the price.
    private func maybeStartStreaming() {
        startChartRefreshTimer()
    }


    /// Fetch the fast-core slice in parallel with the full detail and paint it the moment
    /// it lands.
    ///
    /// Why: the whole screen used to sit behind ONE aggregated response — `^GSPC`
    /// measured 5.63s cold against 0.14s warm — which is the "very slow at first time
    /// open it" TestFlight report this fixes. The stock screen never had that problem
    /// despite the slowest full build of the lot, because it paints a core slice first.
    ///
    /// `try?` on purpose: core is an accelerator, so a core failure must be invisible —
    /// the full response is already in flight and owns the error path. It also
    /// deliberately does NOT touch `errorMessage` or bump any request token, both of
    /// which belong to the full fetch. (The bars-only follow-up below is NOT a `try?`: it
    /// is just as invisible to the user, but logs its failure.)
    private func loadCore() async {
        // What this request asks for, captured before the await: its bars are stamped with it.
        let range = selectedChartRange
        let interval = chartSettings.selectedInterval
        guard let core = try? await StockRepository.shared.getETFCore(
            symbol: etfSymbol,
            range: range.rawValue,
            interval: interval.rawValue
        ) else { return }
        // The race guard. A core response landing AFTER the full one must be dropped, or
        // the screen would visibly step backwards from the complete model to the
        // header-only one.
        guard etfData == nil else { return }
        var model = core.toCoreData()
        guard range == selectedChartRange, interval == chartSettings.selectedInterval else {
            // The user picked another range/interval while core was in flight, and that
            // fetch owns the chart now: paint the header only. Core's bars belong to the OLD
            // selection and would sit under the new pill (or over its failure note).
            model.chartPricePoints = []
            coreData = model
            // That tap started its own bars-only ask, but if it answered BEFORE core there
            // was no model to paint on and it dropped itself. Ask again for the selection
            // the pills hold now: a repository cache hit when that answer already landed
            // (the client cache keeps it), one extra request in the rarer case it is still
            // in flight — whose own answer then drops itself on the newer token.
            await fetchBarsOnlyChart(range: selectedChartRange, interval: chartSettings.selectedInterval)
            return
        }
        coreData = model
        didWriteChartBars(model.chartPricePoints, range: range, interval: interval)
        // `/core` carries bars for 1D/1W only, so a screen opening on a remembered 3M/6M/1Y/
        // 5Y/ALL would sit on the chart placeholder until the FULL detail landed (2-5 s
        // cold). Ask the bars-only route for exactly this selection now. Keyed on "core
        // brought no bars", not on the range, so it costs nothing when core does carry them.
        if model.chartPricePoints.isEmpty {
            await fetchBarsOnlyChart(range: range, interval: interval)
        }
    }

    /// Paint one selection's bars from the bars-only `GET /etfs/{symbol}/chart` onto the
    /// fast-core model while the FULL detail is still in flight.
    ///
    /// Why: the first paint (`/core`) carries no 3M+ bars, and a pill tapped before the full
    /// detail lands re-requests the whole detail — both left the chart on its placeholder
    /// for the 2-5 s a cold full build takes. This route answers in ~0.1-0.7 s with the SAME
    /// builder and bars as the detail's `chart_data`, so the full model's later write carries
    /// the same stamp and keeps the user's window.
    ///
    /// Applied only if, when it lands, ALL of these still hold — else dropped:
    ///  - it is still the newest bars-only ask (`barsOnlyChartToken`): a newer tap wins;
    ///  - the full model has NOT landed (`etfData == nil`): from then on the full detail owns
    ///    the chart, and a range tap uses the light `/quote` slice;
    ///  - the pills still ask for `range` / `interval`: never another selection's bars under
    ///    the current pill;
    ///  - a core model is on screen to paint on (stamping bars that nothing draws would make
    ///    the stamp lie about what is shown);
    ///  - the answer carries bars. An empty one is left to the full detail, which owns the
    ///    "No chart data" / "Couldn't load this range" outcome.
    ///
    /// The write goes through `didWriteChartBars`, like every other chart write. A failure is
    /// silent to the USER — the full detail is still coming and owns the error banner — but
    /// always logged: a route failing on every open would otherwise look exactly like "the
    /// chart is just slow", the report this fixes.
    private func fetchBarsOnlyChart(range: ChartTimeRange, interval: ChartInterval) async {
        barsOnlyChartToken += 1
        let token = barsOnlyChartToken
        let startTime = CFAbsoluteTimeGetCurrent()
        let response: StockChartResponse
        do {
            response = try await repository.getAssetChart(
                asset: .etfs,
                symbol: etfSymbol,
                range: range.rawValue,
                interval: interval.rawValue
            )
        } catch {
            // APIClient wraps a cancellation into APIError.networkError, so `catch is
            // CancellationError` never matches — a cancelled ask is not a failure to report.
            guard !Task.isCancelled else { return }
            log.warning("ETF bars-only chart failed for \(self.etfSymbol, privacy: .public) range \(range.rawValue, privacy: .public) interval \(interval.rawValue, privacy: .public) — \(String(describing: type(of: error)), privacy: .public): \(String(describing: error), privacy: .public)")
            return
        }
        let elapsed = CFAbsoluteTimeGetCurrent() - startTime
        guard token == barsOnlyChartToken,
              etfData == nil,
              coreData != nil,
              range == selectedChartRange,
              interval == chartSettings.selectedInterval else {
            log.debug("ETF bars-only chart for \(self.etfSymbol, privacy: .public) \(range.rawValue, privacy: .public) \(interval.rawValue, privacy: .public) dropped after \(String(format: "%.2f", elapsed), privacy: .public)s — superseded, full model landed, or no core on screen")
            return
        }
        guard !response.prices.isEmpty else {
            log.debug("ETF bars-only chart for \(self.etfSymbol, privacy: .public) \(range.rawValue, privacy: .public) \(interval.rawValue, privacy: .public) answered no bars — left to the full detail")
            return
        }
        coreData?.chartPricePoints = response.prices
        didWriteChartBars(response.prices, range: range, interval: interval)
        log.debug("ETF bars-only chart painted for \(self.etfSymbol, privacy: .public) \(range.rawValue, privacy: .public) \(interval.rawValue, privacy: .public) — \(response.prices.count) bars in \(String(format: "%.2f", elapsed), privacy: .public)s")
    }

    // MARK: - Fallback Data

    private func loadFallbackData() {
        // Only seed the SPY sample into an EMPTY screen (first-load failure) — never
        // clobber already-loaded real data. Mirrors IndexDetailViewModel.loadFallbackData.
        // A failed pull-to-refresh must keep the real ETF on screen (just show the
        // error banner), not flip the whole card to hard-coded SPY figures.
        // Never paint another fund's hard-coded figures (sampleSPY) onto a failed
        // load: showing SPY's price / expense ratio / dividend yield / holdings under
        // a *different* ETF (e.g. QQQ) is financial misinformation and leaks into the
        // Cay AI context. Stay in the honest empty/skeleton state — errorMessage drives
        // the banner + pull-to-refresh. Mirrors the Crypto/Commodity VMs.
        guard etfData == nil else { return }
        log.debug("ETF load failed for \(self.etfSymbol, privacy: .public) — honest empty state (no sample seed)")
    }

    // MARK: - Older chart history

    /// The chart is at (or panning toward) its left floor; `userInitiated` = the user tapped
    /// Retry on the "Couldn't load older prices" marker.
    func loadOlderChartBars(userInitiated: Bool) {
        chartHistory.requestOlder(force: userInitiated)
    }

    // MARK: - Live Price

    /// Was `connectLivePrice()`. The 30s live-slice timer is the whole mechanism now.
    func startLivePriceUpdates() {
        startChartRefreshTimer()
    }

    func stopLivePriceUpdates() {
        stopChartRefreshTimer()
    }

    // MARK: - Chart Refresh Timer

    private func startChartRefreshTimer() {
        chartRefreshTask?.cancel()
        chartRefreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000) // 30 seconds
                guard !Task.isCancelled else { break }
                guard let self = self else { break }

                guard MarketHoursUtil.isMarketActive() else { continue }

                // The LIGHT slice, not the whole detail payload. Bars only when the chart
                // is intraday: on a daily chart a 30-second refresh cannot move a candle,
                // so asking for them is pure payload. The price header refreshes either
                // way — the old `isIntraday` guard froze it entirely on a daily chart.
                await self.refreshLiveSlice(
                    includeChart: self.chartSettings.selectedInterval.isIntraday
                )
            }
        }
    }

    private func stopChartRefreshTimer() {
        chartRefreshTask?.cancel()
        chartRefreshTask = nil
    }

    // MARK: - User Actions

    /// Bumped on every star tap. `checkWatchlistStatus()` captures it before its GET and
    /// re-checks it after, so a snapshot that was already in flight when the user tapped
    /// is discarded instead of reverting them. TickerDetailViewModel had this guard; all
    /// four sibling screens shipped without it, so a slow watchlist GET landing after the
    /// tap silently un-starred the asset with no error and no trace.
    ///
    /// A generation counter, NOT a sticky Bool: `checkWatchlistStatus()` re-runs from each
    /// screen's error-state Retry, and a sticky flag would pin the local value forever and
    /// ignore a genuine change made on another device.
    private var favoriteToggleGeneration: Int = 0

    func toggleFavorite() {
        // The user's intent now outranks any watchlist snapshot already in flight.
        favoriteToggleGeneration &+= 1
        let wasInWatchlist = isFavorite
        isFavorite.toggle()

        Task { @MainActor in
            do {
                if wasInWatchlist {
                    try await APIClient.shared.request(
                        endpoint: .removeFromWatchlist(stockId: etfSymbol)
                    )
                    print("✅ [ETFDetailVM] Removed \(etfSymbol) from watchlist")
                } else {
                    try await APIClient.shared.request(
                        endpoint: .addToWatchlist(stockId: etfSymbol, assetType: "etf")
                    )
                    print("✅ [ETFDetailVM] Added \(etfSymbol) to watchlist")
                }

                // The server holds the change now — tell Tracking, Home and Updates.
                // Posted here, AFTER the request returned, and never on the optimistic flip
                // above: the receivers refetch, and a refetch keyed to the flip can overtake
                // the write and read the pre-toggle state (see
                // `PortfolioStore.watchlistDidChangeNotification`).
                PortfolioStore.announceWatchlistChange(
                    ticker: etfSymbol, assetType: "etf",
                    added: !wasInWatchlist, source: .detailStar
                )
            } catch {
                // Revert AND tell the user. The revert was always right; the silence was the
                // bug — in a release build a star that fills in and empties again is
                // indistinguishable from the app deciding the tap never happened.
                isFavorite = wasInWatchlist
                AppActions.shared.reportMutationFailure(
                    error,
                    action: wasInWatchlist
                        ? "remove \(self.etfSymbol) from your watchlist"
                        : "add \(self.etfSymbol) to your watchlist",
                    signInFeature: "save this ETF"
                )
            }
        }
    }

    private func checkWatchlistStatus() async {
        let generation = favoriteToggleGeneration
        do {
            let watchlist: [WatchlistItemDTO] = try await APIClient.shared.request(
                endpoint: .getWatchlist,
                responseType: [WatchlistItemDTO].self
            )
            // Discard a snapshot that raced with a tap: it may predate the user's write
            // and would revert their star with no error shown.
            guard generation == self.favoriteToggleGeneration else {
                print("⏭️ [ETFDetailVM] Watchlist snapshot discarded — user toggled during the fetch")
                return
            }
            self.isFavorite = watchlist.contains { $0.ticker.uppercased() == etfSymbol.uppercased() }
        } catch {
            print("⚠️ [ETFDetailVM] Watchlist check failed: \(error)")
        }
    }

    private struct WatchlistItemDTO: Codable {
        let ticker: String
    }

    func handleWebsiteTap() {
        guard let website = etfData?.etfProfile.website,
              !website.isEmpty,
              let url = URL(string: "https://\(website)") else { return }
        openExternal(url, into: &browserLink)
    }

    func handleRelatedETFTap(_ ticker: RelatedTicker) {
        pendingTickerNavigation = SearchSelection(symbol: ticker.symbol, type: "etf")
    }

    func handleNewsArticleTap(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        openExternal(url, into: &browserLink)
    }

    func handleNewsExternalLink(_ article: TickerNewsArticle) {
        guard let url = article.articleURL else { return }
        openExternal(url, into: &browserLink)
    }

    func handleNewsTickerTap(_ ticker: String) {
        // A news chip is an EQUITY symbol, not another fund.
        pendingTickerNavigation = SearchSelection(symbol: ticker, type: "stock")
    }

    // MARK: - News fetch + enrichment (shared cache; mirrors the other detail VMs)

    private func fetchETFNews() async {
        self.isNewsLoading = true
        do {
            let response = try await repository.getETFNews(symbol: etfSymbol, limit: 50)
            let apiNews = response.articles
            print("📰 [ETFDetail] Got \(apiNews.count) news articles for \(etfSymbol) (cached: \(response.cached ?? false))")

            // Drop unrenderable rows (no parseable date) instead of stamping
            // them "now" — parity with the Updates screen for the same feed.
            self.allNewsArticles = apiNews.compactMap { mapApiToUiArticle($0) }
            self.newsDisplayCount = newsPageSize
            self.hasMoreNews = allNewsArticles.count > newsDisplayCount
            self.newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
            self.isNewsLoading = false

            // Enrich ONLY if the News tab is being viewed — the expensive `flash`
            // model must not fire on every ETF open. `newsTabAppeared()` covers
            // the case where news lands before the user switches to the tab.
            if selectedTab == .news {
                await enrichVisibleArticles()
            }
        } catch {
            print("⚠️ [ETFDetail] Failed to fetch news for \(etfSymbol): \(error)")
        }
        self.isNewsLoading = false
    }

    func loadMoreNews() {
        guard !isLoadingMoreNews, hasMoreNews else { return }
        isLoadingMoreNews = true
        newsDisplayCount += newsPageSize
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
        hasMoreNews = allNewsArticles.count > newsDisplayCount
        // Reset AFTER enrichment (not synchronously) so the load-more sentinel
        // can't cascade every remaining page in a single tick.
        Task {
            await enrichVisibleArticles()
            isLoadingMoreNews = false
        }
    }

    /// Called when the News tab becomes visible — defers AI enrichment to when
    /// news is actually read. See TickerDetailViewModel.newsTabAppeared.
    func newsTabAppeared() {
        Task { await enrichVisibleArticles() }
    }

    private func enrichVisibleArticles() async {
        guard !isEnrichingNews else { return }
        let unenriched = newsArticles.filter { !$0.aiProcessed }
        guard !unenriched.isEmpty else { return }
        let ids = unenriched.map { $0.apiId }
            .filter { !$0.isEmpty && !$0.hasPrefix("temp_") && !$0.hasPrefix("raw_") }
        guard !ids.isEmpty else { return }

        isEnrichingNews = true
        defer { isEnrichingNews = false }

        await attemptEnrichment(articleIds: ids)
        newsArticles = Array(allNewsArticles.prefix(newsDisplayCount))
    }

    private func attemptEnrichment(articleIds: [String], maxAttempts: Int = 2) async {
        for attempt in 1...maxAttempts {
            do {
                let enrichResponse = try await repository.enrichETFNews(
                    symbol: etfSymbol, articleIds: articleIds
                )
                mergeEnrichment(enrichResponse.articles)
                let enrichedCount = allNewsArticles.prefix(newsDisplayCount)
                    .filter { $0.aiProcessed }.count
                if enrichedCount > 0 {
                    print("✅ [ETFDetail] Attempt \(attempt) enriched \(enrichedCount) articles")
                    return
                } else if attempt < maxAttempts {
                    try await Task.sleep(nanoseconds: 3_000_000_000)
                }
            } catch {
                if attempt < maxAttempts {
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                } else {
                    print("⚠️ [ETFDetail] Enrichment failed after \(maxAttempts) attempts: \(error)")
                }
            }
        }
    }

    private func mergeEnrichment(_ enrichedArticles: [StockNewsArticle]) {
        let enrichedById = Dictionary(
            enrichedArticles.map { ($0.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        for i in allNewsArticles.indices {
            guard let enriched = enrichedById[allNewsArticles[i].apiId] else { continue }
            let wasProcessed = enriched.aiProcessed ?? false
            let hasBullets = enriched.summaryBullets?.isEmpty == false
            guard wasProcessed || hasBullets else { continue }
            // Only real AI bullets — no raw-summary pseudo-bullet (parity with Updates).
            allNewsArticles[i].summaryBullets = enriched.summaryBullets ?? []
            allNewsArticles[i].sentiment = mapSentiment(enriched.sentiment)
            allNewsArticles[i].aiProcessed = true
        }
    }

    private func mapApiToUiArticle(_ article: StockNewsArticle) -> TickerNewsArticle? {
        // Drop rows with an unparseable/absent date instead of stamping "now" —
        // parity with the Updates screen.
        guard let published = article.publishedAt.flatMap({ parseNewsDate($0) }) else {
            return nil
        }
        // Drop a title-less row, exactly as the Updates feed does — the same
        // shared cache row must not render as a blank card here while Updates
        // omits it.
        guard !article.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return nil }
        return TickerNewsArticle(
            apiId: article.id,
            headline: article.title,
            source: NewsSource(
                name: article.source ?? "Unknown",
                iconName: nil,
                logoURL: article.sourceLogoUrl.flatMap { URL(string: $0) }
            ),
            sentiment: mapSentiment(article.sentiment),
            publishedAt: published,
            thumbnailName: nil,
            imageURL: article.imageUrl.flatMap { URL(string: $0) },
            relatedTickers: article.relatedTickers ?? [],
            // Only real AI bullets — no raw-summary pseudo-bullet (parity with Updates).
            summaryBullets: article.summaryBullets ?? [],
            articleURL: article.url.flatMap { URL(string: $0) },
            aiProcessed: article.aiProcessed ?? false
        )
    }

    /// nil ⇒ no badge until AI-enriched. Matches `NewsSentiment(backend:)` on the
    /// Updates side so the same row renders identically.
    private func mapSentiment(_ sentiment: String?) -> NewsSentiment? {
        NewsSentiment(backend: sentiment)
    }

    private func parseNewsDate(_ dateString: String) -> Date? {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: dateString) { return d }
        iso.formatOptions = [.withInternetDateTime]
        if let d = iso.date(from: dateString) { return d }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        if let d = f.date(from: dateString) { return d }
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: dateString)
    }

    func handleSuggestionTap(_ suggestion: ETFAISuggestion) {
        aiInputText = suggestion.text
        handleAISend()
    }

    func handleAISend() {
        guard !aiInputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        let query = aiInputText
        aiInputText = ""
        print("[ETFDetailVM] AI Query for \(etfSymbol): \(query)")
        pendingAIQuery = query
    }

    // MARK: - Computed Properties

    var formattedPrice: String {
        etfData?.formattedPrice ?? "--"
    }

    var formattedChange: String {
        etfData?.formattedChange ?? "--"
    }

    var formattedChangePercent: String {
        etfData?.formattedChangePercent ?? "--"
    }

    var isPositive: Bool {
        etfData?.isPositive ?? true
    }

    var chartData: [Double] {
        etfData?.chartData ?? []
    }

    var chartPricePoints: [StockPricePoint] {
        etfData?.chartPricePoints ?? []
    }

    var aiSuggestions: [ETFAISuggestion] {
        ETFAISuggestion.defaultSuggestions
    }

    // MARK: - AI Context Builders

    /// Core ETF facts — always included regardless of tab
    private var baseETFContext: String? {
        guard let data = etfData else { return nil }
        var lines: [String] = []
        lines.append("ETF: \(data.symbol) (\(data.name))")
        lines.append("Price: \(data.formattedPrice) \(data.formattedChange) \(data.formattedChangePercent)")

        if !data.keyStatisticsGroups.isEmpty {
            lines.append("Key Statistics:")
            for group in data.keyStatisticsGroups {
                let groupStr = group.statistics.map { "\($0.label): \($0.value)" }.joined(separator: " | ")
                lines.append("  \(groupStr)")
            }
        }

        if !data.performancePeriods.isEmpty {
            let perfStr = data.performancePeriods.map { p in
                let sign = p.changePercent >= 0 ? "+" : ""
                return "\(p.label): \(sign)\(String(format: "%.1f", p.changePercent))%"
            }.joined(separator: ", ")
            lines.append("Performance: \(perfStr)")
        }

        return lines.joined(separator: "\n")
    }

    /// Overview tab context — snapshot data, strategy, yield, holdings, benchmark
    private var overviewContext: String? {
        guard let data = etfData else { return nil }
        var parts: [String] = []

        parts.append("Identity Rating: \(data.identityRating.score)/\(data.identityRating.maxScore), \(data.identityRating.volatilityLabel)")
        parts.append("Strategy: \(data.strategy.hook) Tags: \(data.strategy.tags.joined(separator: ", "))")
        parts.append("Expense Ratio: \(data.netYield.formattedExpenseRatio), Dividend Yield: \(data.netYield.formattedDividendYield), \(data.netYield.payFrequency)")
        parts.append("Verdict: \(data.netYield.verdict)")

        let topHoldings = data.holdingsRisk.topHoldings.prefix(5).map { "\($0.symbol) \($0.formattedWeight)" }.joined(separator: ", ")
        parts.append("Top Holdings: \(topHoldings)")
        parts.append("Concentration: Top \(data.holdingsRisk.concentration.topN) = \(data.holdingsRisk.concentration.formattedWeight)")

        if let bench = data.benchmarkSummary {
            let sign = bench.avgAnnualReturn >= 0 ? "+" : ""
            parts.append("Avg Annual Return: \(sign)\(String(format: "%.1f", bench.avgAnnualReturn))% vs \(bench.benchmarkName): \(String(format: "%.1f", bench.spBenchmark))%")
        }

        return parts.isEmpty ? nil : parts.joined(separator: ". ")
    }

    /// News tab context — recent headlines
    private var newsContext: String? {
        let recent = newsArticles.prefix(3)
        guard !recent.isEmpty else { return nil }
        let headlines = recent.map { "- \($0.headline) (\($0.source.name))" }.joined(separator: "\n")
        return "Recent News:\n\(headlines)"
    }

    /// Build context string for the current tab to inject into AI chat
    var contextForCurrentTab: String? {
        var sections: [String] = []

        if let base = baseETFContext {
            sections.append(base)
        }

        switch selectedTab {
        case .overview:
            if let ctx = overviewContext { sections.append(ctx) }
        case .news:
            if let ctx = newsContext { sections.append(ctx) }
        }

        sections.append("User is viewing the \(selectedTab.rawValue) tab of ETF \(etfSymbol).")

        return sections.isEmpty ? nil : sections.joined(separator: "\n\n")
    }
}
