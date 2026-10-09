//
//  AccountSnapshotStore.swift
//  ios
//
//  The last good LIVE answer of an account-scoped tab, kept on THIS device for THIS account, so
//  the first frame after a tap is that answer, labelled "Updated <time>", instead of a
//  skeleton. Two payloads use it: the Updates Market feed (`UpdatesFeedSnapshot.swift`) and the
//  Tracking holdings (`TrackingSnapshot.swift`).
//
//  It copies `HomeDashboardSnapshotStore`'s contract (owner fence, epoch fence, 96 h window,
//  one serial disk tail, raw response bytes through the live DTOs). Home is deliberately NOT
//  moved onto this type: its guard file pins the text of its own class.
//
//  ── PUBLIC API (the ViewModels build against exactly this) ──────────────────────────────────
//
//  Access (all `@MainActor`):
//    UpdatesFeedSnapshotStore.shared   // = AccountSnapshotStore<UpdatesFeedSnapshot>
//    TrackingSnapshotStore.shared      // = AccountSnapshotStore<TrackingSnapshot>
//  Each `.shared` is a computed static on a constrained extension (`where Payload == …`) that
//  returns one file-private instance, declared in the payload's own file.
//
//  @MainActor final class AccountSnapshotStore<Payload: AccountSnapshotPayload>
//    struct Snapshot { let ownerUserId: String; let savedAt: Date; let payload: Payload }
//    let config: AccountSnapshotConfig
//    private(set) var epoch: Int
//        Capture it BEFORE the request (`let snapshotEpoch = snapshotStore.epoch`) and pass it
//        to `save`. Bumped by every bind to another owner, a session end and a purge.
//    func bindLaunchOwner(ownerUserId: String?)            // AppState.configure only. No I/O
//                                                          //   but a delete when nil.
//    func bindOwner(_ userId: String)                      // applyProfile / account switch.
//    func prepare(apiClient: APIClient = .shared) async    // Disk only, single-flight, lazy:
//                                                          //   reads + decodes the file once
//                                                          //   per binding. Never a request.
//    func snapshotForDisplay(now: Date = Date()) -> Snapshot?
//        The BOUND owner's snapshot inside the display window, or nil. Call it after
//        `await prepare(...)`; it never reads the disk itself.
//    func save(parts: [String: Data], payload: Payload, savedAt: Date, epoch captured: Int)
//        `parts` are the exact live response bytes, keyed by the config's part names.
//        `savedAt` = the moment the response was captured. Refused on a stale epoch, no bound
//        owner, a `savedAt` outside the window, a capture older than the snapshot in memory,
//        the payload's `persistDecision`, or a missing / unknown / empty / oversized part.
//    func clearForEndedSession()                           // discardDataForEndedSession only.
//    @discardableResult func purgeCache() -> Task<Void, Never>?
//        Clear Cache, or a confirmed local edit: bumps the epoch, keeps the owner, deletes the
//        file. Await the returned task to know the delete landed.
//    static func inMemory(config:seed:savedAt:owner:) -> AccountSnapshotStore<Payload>  // previews
//
//  nonisolated enum AccountSnapshotPolicy
//    maxDisplayAge (96 h, == Home's), maxFutureSkew (5 min), normalizedOwner(_:),
//    isDisplayable(savedAt:now:), and
//    updatedLabel(savedAt:now:calendar:) -> String   // "Updated 4:02 PM" / "Updated Sep 28, 4:02 PM"
//        The one wording source for both tabs: it forwards to Home's `snapshotStatusText`.
//
//  @MainActor protocol AccountSnapshotPayload
//    static func decode(parts: [String: Data], apiClient: APIClient) async throws -> Self
//        Bytes → payload through `apiClient.decodeBody` and the live DTOs, never a request.
//    func persistDecision(previous: Self?) -> AccountSnapshotPersistDecision
//        .save | .keepPrevious(reason) | .deleteSaved(reason). Also re-asked of a payload read
//        from disk (previous: nil): a file that would not be saved now is deleted, not shown.
//
//  ⚠️ OWNER-FENCED (auth.md §7). The files hold one account's news or holdings.
//    • `bindLaunchOwner` (AppState.configure, BEFORE Home's prime) binds the stored token's `sub`
//      without reading anything. No credential → the file is deleted. Binding here is what
//      stops `applyProfile`'s `bindOwner` (nil → this account) deleting an unread file.
//    • `prepare` reads lazily (each tab's `.task`, after Home has painted). A foreign owner,
//      another schema, an age past `maxDisplayAge`, a date in the future, a bad part or an
//      undecodable body → the file is deleted and logged. A READ failure (data protection
//      before first unlock) keeps the file and retries at the next `prepare`.
//    • `bindOwner` to a different owner bumps `epoch` and deletes; `clearForEndedSession`
//      unbinds and deletes; `purgeCache` deletes and keeps the binding.
//  Every disk operation runs in order on ONE detached tail (`diskTail`), so a delete enqueued
//  after a pending write always lands last.
//
//  ⚠️ SCREENSHOT MODE (DEBUG): every instance, `.shared` included, is memory-only while
//  `StoreScreenshotMode.isOn` — it never reads the real account's file and never writes the
//  canned fixtures to disk.
//
//  ⚠️ NOT A GATE. The server's own answers are the gate; a snapshot keeps exactly what the
//  server sent this account, for at most 96 h, labelled with its real time.
//

import Foundation
import OSLog

// File-scope and explicitly `nonisolated`: read from the detached disk tail, and the target
// defaults to MainActor isolation (the same lever as `HomeDashboardSnapshotStore`).
nonisolated private let accountSnapshotLog = Logger(subsystem: "com.phan.caydex", category: "account-snapshot")

/// Log values only, never a computation input. Clamped, so a wild clock cannot trap `Int(_:)`.
nonisolated private enum AccountSnapshotLogValue {
    /// Whole milliseconds since `start`.
    static func millis(since start: Date) -> Int {
        let millis = Date().timeIntervalSince(start) * 1000
        guard millis.isFinite, millis > 0 else { return 0 }
        return Int(min(millis, 86_400_000))
    }

    /// Whole seconds between `savedAt` and now (negative for a future-dated save).
    static func ageSeconds(of savedAt: Date) -> Int {
        let seconds = Date().timeIntervalSince(savedAt)
        guard seconds.isFinite else { return 0 }
        return Int(max(min(seconds, 31_536_000), -31_536_000))
    }
}

// MARK: - Policy

/// The rules every account snapshot shares. Constants live here, not on the store: a generic
/// type cannot hold static stored properties.
nonisolated enum AccountSnapshotPolicy {

    /// 96 h, the same window as Home (owner decision 2026-10-01 and again for these two tabs).
    /// Must stay below the refresh token's life minus the access token's: a saved snapshot only
    /// proves an access token was minted within that much before the save. Pinned equal to
    /// Home's literal by `test_ios_account_snapshot_guards.py` — keep it a plain product.
    static let maxDisplayAge: TimeInterval = 96 * 60 * 60

    /// A clock set back after the save would otherwise make the snapshot look newer than now.
    static let maxFutureSkew: TimeInterval = 5 * 60

    /// Plist structure around the parts. The file cap is the parts' caps plus this.
    static let envelopeOverheadBytes = 16 * 1024

    /// Trimmed and lowercased; "" → nil. The same rule as Home's, so a token `sub` and a
    /// `/users/me` id compare equal.
    static func normalizedOwner(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              !trimmed.isEmpty else { return nil }
        return trimmed
    }

    static func isDisplayable(savedAt: Date, now: Date) -> Bool {
        let age = now.timeIntervalSince(savedAt)
        return age <= maxDisplayAge && age >= -maxFutureSkew
    }

    /// "Updated 4:02 PM" / "Updated Sep 28, 4:02 PM". The one wording source: Home's.
    static func updatedLabel(savedAt: Date, now: Date = Date(), calendar: Calendar = .current) -> String {
        HomeDashboardViewModel.snapshotStatusText(savedAt: savedAt, now: now, calendar: calendar)
    }
}

// MARK: - Config

/// One per store: where its file lives and which parts it may hold.
nonisolated struct AccountSnapshotConfig: Sendable {
    /// Log prefix, e.g. "tracking-snapshot".
    let name: String
    /// `Library/Caches/<directoryName>` — never backed up, so the file cannot follow a restore
    /// onto a phone that lacks the `...ThisDeviceOnly` Keychain session it belongs to.
    let directoryName: String
    /// `<name>.v<schemaVersion>.plist` (the guard pins the two to agree).
    let fileName: String
    /// Bump with any change to the envelope, the part set or a `persistDecision` rule. An
    /// additive DTO change needs no bump: the bytes decode through the live DTOs, and a decode
    /// failure deletes the file. ⚠️ A bump renames the file (`.vN.`), and every delete targets the
    /// CURRENT name only — the bump must also delete the previous `.v<N-1>.plist`, or an ended
    /// account's old file outlives sign-out and Clear Cache (auth.md §7).
    let schemaVersion: Int
    /// Byte cap per part. The keys ARE the allowed parts.
    let partCaps: [String: Int]
    /// Parts every file must carry. Must be a subset of `partCaps`' keys.
    let requiredParts: Set<String>

    var allowedParts: Set<String> { Set(partCaps.keys) }

    /// The whole file: every part at its cap, plus the plist envelope.
    var maxFileBytes: Int { partCaps.values.reduce(0, +) + AccountSnapshotPolicy.envelopeOverheadBytes }

    /// `Library/Caches/<directoryName>`, or nil when the sandbox has no Caches directory.
    var defaultDirectory: URL? {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
            .appendingPathComponent(directoryName, isDirectory: true)
    }
}

// MARK: - Envelope and disk

/// The on-disk record. Binary plist, so each part is stored as raw bytes (no base64).
nonisolated struct AccountSnapshotEnvelope: Codable, Sendable {
    let schemaVersion: Int
    /// Lowercased. Compared against the bound owner on every read.
    let ownerUserId: String
    let savedAt: Date
    /// The exact live response bytes, keyed by part name.
    let parts: [String: Data]
}

/// What one read of the file found. A READ failure (I/O, data protection before first unlock)
/// is kept apart from a CORRUPT file on purpose: only the second is grounds to delete.
nonisolated enum AccountSnapshotReadOutcome: Sendable {
    case missing
    case readFailed(String)
    case corrupt(String)
    case envelope(AccountSnapshotEnvelope)
}

/// A payload's verdict on a live answer (`save`) — and on a file read back (`previous: nil`).
nonisolated enum AccountSnapshotPersistDecision: Sendable, Equatable {
    /// A complete live answer: write it.
    case save
    /// Not good enough to show next time (degraded, partial, another scope); the saved file,
    /// if any, stays.
    case keepPrevious(String)
    /// A complete live answer that says there is nothing to show: the saved file goes too, so a
    /// later launch never paints what the account no longer has.
    case deleteSaved(String)
}

/// A file whose bytes cannot become a payload.
nonisolated enum AccountSnapshotDecodeError: Error, CustomStringConvertible {
    case missingPart(String)
    case unreadablePart(String)

    var description: String {
        switch self {
        case .missingPart(let name): return "the \(name) part is missing"
        case .unreadablePart(let name): return "the \(name) part is unreadable"
        }
    }
}

/// The file I/O, all synchronous and run ONLY on the store's detached disk tail.
nonisolated enum AccountSnapshotDisk {

    static func read(at url: URL, config: AccountSnapshotConfig) -> AccountSnapshotReadOutcome {
        guard FileManager.default.fileExists(atPath: url.path) else { return .missing }
        let bytes: Data
        do {
            bytes = try Data(contentsOf: url)
        } catch {
            return .readFailed("\(type(of: error)): \(error)")
        }
        let cap = config.maxFileBytes
        guard bytes.count <= cap else {
            return .corrupt("file is \(bytes.count) bytes, over the \(cap)-byte cap")
        }
        do {
            return .envelope(try PropertyListDecoder().decode(AccountSnapshotEnvelope.self, from: bytes))
        } catch {
            return .corrupt("envelope decode failed — \(type(of: error)): \(error)")
        }
    }

    static func write(_ envelope: AccountSnapshotEnvelope, to url: URL, config: AccountSnapshotConfig) {
        let name = config.name
        do {
            let encoder = PropertyListEncoder()
            encoder.outputFormat = .binary
            let bytes = try encoder.encode(envelope)
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true
            )
            try bytes.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            accountSnapshotLog.debug("\(name, privacy: .public): saved \(bytes.count, privacy: .public) bytes")
        } catch {
            let detail = "\(type(of: error)): \(error)"
            accountSnapshotLog.warning("\(name, privacy: .public): write failed — \(detail, privacy: .public)")
        }
    }

    static func delete(at url: URL, config: AccountSnapshotConfig) {
        let name = config.name
        let fm = FileManager.default
        guard fm.fileExists(atPath: url.path) else { return }
        do {
            try fm.removeItem(at: url)
            accountSnapshotLog.info("\(name, privacy: .public): file deleted")
        } catch {
            // Logged, not retried: the next `prepare` re-checks the owner and age of whatever
            // survived and deletes it again.
            let detail = "\(type(of: error)): \(error)"
            accountSnapshotLog.warning("\(name, privacy: .public): delete failed — \(detail, privacy: .public)")
        }
    }

    /// Why this envelope must not be shown to `owner`, or nil when it may.
    static func rejection(
        of envelope: AccountSnapshotEnvelope, owner: String, now: Date, config: AccountSnapshotConfig
    ) -> String? {
        if envelope.schemaVersion != config.schemaVersion {
            return "schema \(envelope.schemaVersion), expected \(config.schemaVersion)"
        }
        if AccountSnapshotPolicy.normalizedOwner(envelope.ownerUserId) != owner {
            return "owned by another account"
        }
        if !AccountSnapshotPolicy.isDisplayable(savedAt: envelope.savedAt, now: now) {
            return "saved \(AccountSnapshotLogValue.ageSeconds(of: envelope.savedAt)) s ago — outside the display window"
        }
        if let problem = partsProblem(envelope.parts, config: config) {
            return problem
        }
        return nil
    }

    /// Why this part set may not be stored or shown, or nil when it may: every required part,
    /// no unknown part, and each part non-empty and under its own cap.
    static func partsProblem(_ parts: [String: Data], config: AccountSnapshotConfig) -> String? {
        let names = Set(parts.keys)
        let missing = config.requiredParts.subtracting(names)
        if !missing.isEmpty {
            return "missing part(s) \(missing.sorted())"
        }
        let unknown = names.subtracting(config.allowedParts)
        if !unknown.isEmpty {
            return "unknown part(s) \(unknown.sorted())"
        }
        for name in names.sorted() {
            let size = parts[name]?.count ?? 0
            let cap = config.partCaps[name] ?? 0
            if size == 0 || size > cap {
                return "part \(name) is \(size) bytes (cap \(cap))"
            }
        }
        return nil
    }
}

// MARK: - Payload

/// What a store keeps: the decode step and the "is this answer worth keeping" rule of ONE tab.
@MainActor
protocol AccountSnapshotPayload {
    /// Bytes → payload, through `apiClient.decodeBody` and the live DTOs. Never a request.
    static func decode(parts: [String: Data], apiClient: APIClient) async throws -> Self
    /// `previous` is this account's snapshot still in memory and displayable, or nil.
    func persistDecision(previous: Self?) -> AccountSnapshotPersistDecision
}

// MARK: - Store

@MainActor
final class AccountSnapshotStore<Payload: AccountSnapshotPayload> {

    /// A snapshot ready to render: the owner it was saved for, when, and the decoded payload.
    struct Snapshot {
        let ownerUserId: String
        let savedAt: Date
        let payload: Payload
    }

    let config: AccountSnapshotConfig

    // MARK: - State

    /// Bumped by every change of WHO the store serves (bind to another owner, session end) and
    /// by a purge. A load captures it before its request; `save` refuses a stale capture, so a
    /// response that left under the previous identity can never be written under the next.
    private(set) var epoch = 0
    private var boundOwner: String?
    private var current: Snapshot?
    /// The bound owner's file has not been read yet for this binding (set by the launch bind;
    /// set again by a READ failure, so the next `prepare` retries).
    private var fileUnread = false
    /// Bumped by every LIVE answer `save` acts on (a write or a `.deleteSaved`). A prepare read
    /// that started before one is older than it and must not publish — `current == nil` alone
    /// cannot tell "nothing yet" from "a live answer just said: nothing to show".
    private var liveAnswerGeneration = 0
    /// The one in-flight read. A second `prepare` joins it; it clears itself when done.
    private var readTask: Task<Void, Never>?
    /// The last queued disk operation. Every read, write and delete awaits its predecessor.
    private var diskTail: Task<Void, Never>?
    /// nil = memory only (previews, and every instance in DEBUG screenshot mode).
    private let fileURL: URL?

    init(config: AccountSnapshotConfig, directory: URL?) {
        assert(config.requiredParts.isSubset(of: config.allowedParts), "\(config.name): a required part has no cap")
        self.config = config
        let url = directory?.appendingPathComponent(config.fileName, isDirectory: false)
        #if DEBUG
        fileURL = StoreScreenshotMode.isOn ? nil : url
        #else
        fileURL = url
        #endif
    }

    /// A memory-only store, optionally pre-seeded — for previews. Never touches the disk.
    static func inMemory(
        config: AccountSnapshotConfig,
        seed: Payload? = nil,
        savedAt: Date = Date(),
        owner: String = "preview-user"
    ) -> AccountSnapshotStore<Payload> {
        let store = AccountSnapshotStore<Payload>(config: config, directory: nil)
        let owner = AccountSnapshotPolicy.normalizedOwner(owner) ?? "preview-user"
        store.boundOwner = owner
        if let seed {
            store.current = Snapshot(ownerUserId: owner, savedAt: savedAt, payload: seed)
        }
        return store
    }

    // MARK: - Owner

    /// Launch: bind the stored credential's owner WITHOUT reading the file.
    ///
    /// Called from `AppState.configure` after the widget seed and BEFORE Home's awaited prime,
    /// so it costs Home's first frame nothing. Reads the token's `sub` claim only; it
    /// authenticates nothing. The read happens later, in `prepare`, at tab mount.
    func bindLaunchOwner(ownerUserId: String?) {
        let name = config.name
        let owner = AccountSnapshotPolicy.normalizedOwner(ownerUserId)
        if owner != boundOwner {
            epoch &+= 1
            current = nil
            fileUnread = owner != nil
        }
        boundOwner = owner
        guard owner != nil else {
            // No credential, so nobody's snapshot may stay on this device.
            fileUnread = false
            enqueueDelete()
            accountSnapshotLog.info("\(name, privacy: .public): no stored credential at launch — file deleted")
            return
        }
        accountSnapshotLog.debug("\(name, privacy: .public): bound at launch — the file is read at first prepare")
    }

    /// The signed-in account is now `userId`. A different owner than the bound one drops the
    /// in-memory snapshot, deletes the file and voids every captured epoch.
    func bindOwner(_ userId: String) {
        let name = config.name
        let owner = AccountSnapshotPolicy.normalizedOwner(userId)
        guard owner != boundOwner else { return }
        epoch &+= 1
        current = nil
        fileUnread = false
        boundOwner = owner
        enqueueDelete()
        accountSnapshotLog.info("\(name, privacy: .public): bound to a new account — previous snapshot dropped")
    }

    // MARK: - Read

    /// Read and decode the bound owner's file into memory, once per binding. Disk only — it
    /// never issues a request — and single-flight: a second caller joins the running read, and
    /// a caller's cancellation does not cancel the read.
    func prepare(apiClient: APIClient = .shared) async {
        if let running = readTask {
            await running.value
            return
        }
        guard fileUnread, let owner = boundOwner, let fileURL else { return }
        fileUnread = false
        let readEpoch = epoch
        let readGeneration = liveAnswerGeneration
        let task = Task { [weak self] in
            guard let self else { return }
            await self.readAndPublish(
                owner: owner, fileURL: fileURL, readEpoch: readEpoch,
                readGeneration: readGeneration, apiClient: apiClient
            )
            self.readTask = nil
        }
        readTask = task
        await task.value
    }

    private func readAndPublish(
        owner: String, fileURL: URL, readEpoch: Int, readGeneration: Int, apiClient: APIClient
    ) async {
        let name = config.name
        let started = Date()
        let outcome = await readOnDiskTail(fileURL)
        // Anything that moved the binding while the read was suspended owns the file now — and
        // `current == nil`: a live save that landed meanwhile is newer than this file, and a
        // rejection below would otherwise delete the file that save just wrote. A live
        // `.deleteSaved` meanwhile leaves `current == nil`, so the generation check is what keeps
        // this older file from coming back.
        guard epoch == readEpoch, boundOwner == owner, current == nil,
              liveAnswerGeneration == readGeneration else {
            accountSnapshotLog.info("\(name, privacy: .public): prepare superseded during the read — file ignored")
            return
        }

        let envelope: AccountSnapshotEnvelope
        switch outcome {
        case .missing:
            accountSnapshotLog.info("\(name, privacy: .public): miss — nothing saved on this device")
            return
        case .readFailed(let reason):
            // Kept, and retried at the next prepare: a read refused by data protection (a launch
            // before first unlock) says nothing about the file.
            fileUnread = true
            accountSnapshotLog.warning("\(name, privacy: .public): read failed, file kept — \(reason, privacy: .public)")
            return
        case .corrupt(let reason):
            accountSnapshotLog.warning("\(name, privacy: .public): discarding the file — \(reason, privacy: .public)")
            enqueueDelete()
            return
        case .envelope(let read):
            envelope = read
        }

        if let reason = AccountSnapshotDisk.rejection(of: envelope, owner: owner, now: Date(), config: config) {
            accountSnapshotLog.warning("\(name, privacy: .public): discarding the file — \(reason, privacy: .public)")
            enqueueDelete()
            return
        }

        let readMillis = AccountSnapshotLogValue.millis(since: started)
        do {
            let payload = try await Payload.decode(parts: envelope.parts, apiClient: apiClient)
            // Re-checked after the decode too, for the same reasons as after the read.
            guard epoch == readEpoch, boundOwner == owner, current == nil,
                  liveAnswerGeneration == readGeneration else {
                accountSnapshotLog.info("\(name, privacy: .public): prepare superseded during the decode — file ignored")
                return
            }
            // A file this build would not save now is not shown either (a rule or DTO changed).
            let decision = payload.persistDecision(previous: nil)
            guard decision == .save else {
                let detail = "\(decision)"
                accountSnapshotLog.warning("\(name, privacy: .public): discarding the file — \(detail, privacy: .public)")
                enqueueDelete()
                return
            }
            current = Snapshot(ownerUserId: owner, savedAt: envelope.savedAt, payload: payload)
            let ageSeconds = AccountSnapshotLogValue.ageSeconds(of: envelope.savedAt)
            let byteCount = envelope.parts.values.reduce(0) { $0 + $1.count }
            let totalMillis = AccountSnapshotLogValue.millis(since: started)
            accountSnapshotLog.info("\(name, privacy: .public): hit — \(ageSeconds, privacy: .public) s old, \(byteCount, privacy: .public) bytes, read \(readMillis, privacy: .public) ms, read+decode \(totalMillis, privacy: .public) ms")
        } catch {
            let detail = "\(type(of: error)): \(error)"
            accountSnapshotLog.warning("\(name, privacy: .public): decode failed — \(detail, privacy: .public)")
            // Only while nothing newer was saved: a delete now would remove THAT file.
            if epoch == readEpoch, boundOwner == owner, current == nil,
               liveAnswerGeneration == readGeneration {
                enqueueDelete()
            }
        }
    }

    /// The snapshot to paint, or nil. Only the BOUND owner's, and only inside the display window.
    func snapshotForDisplay(now: Date = Date()) -> Snapshot? {
        guard let owner = boundOwner,
              let snapshot = current,
              snapshot.ownerUserId == owner,
              AccountSnapshotPolicy.isDisplayable(savedAt: snapshot.savedAt, now: now) else { return nil }
        return snapshot
    }

    // MARK: - Write

    /// Persist a LIVE answer. `epoch` is the value the caller captured BEFORE its request;
    /// `savedAt` is when the response was captured.
    func save(parts: [String: Data], payload: Payload, savedAt: Date, epoch captured: Int) {
        // Values are hoisted into locals before every log call: OSLog interpolations are
        // escaping autoclosures, which must not read main-actor state.
        let name = config.name
        guard captured == epoch else {
            accountSnapshotLog.info("\(name, privacy: .public): save refused — the account changed while the load was in flight")
            return
        }
        guard let owner = boundOwner else {
            accountSnapshotLog.info("\(name, privacy: .public): save refused — no account bound")
            return
        }
        guard AccountSnapshotPolicy.isDisplayable(savedAt: savedAt, now: Date()) else {
            let ageSeconds = AccountSnapshotLogValue.ageSeconds(of: savedAt)
            accountSnapshotLog.warning("\(name, privacy: .public): save refused — captured \(ageSeconds, privacy: .public) s ago, outside the display window")
            return
        }
        let previous = snapshotForDisplay()
        if let previous, previous.savedAt > savedAt {
            accountSnapshotLog.info("\(name, privacy: .public): save refused — an older capture never replaces a newer snapshot")
            return
        }
        switch payload.persistDecision(previous: previous?.payload) {
        case .save:
            break
        case .keepPrevious(let reason):
            accountSnapshotLog.info("\(name, privacy: .public): save refused — \(reason, privacy: .public); the previous snapshot is kept")
            return
        case .deleteSaved(let reason):
            liveAnswerGeneration &+= 1
            current = nil
            fileUnread = false
            enqueueDelete()
            accountSnapshotLog.info("\(name, privacy: .public): saved snapshot dropped — \(reason, privacy: .public)")
            return
        }
        if let problem = AccountSnapshotDisk.partsProblem(parts, config: config) {
            accountSnapshotLog.warning("\(name, privacy: .public): save refused — \(problem, privacy: .public)")
            return
        }
        liveAnswerGeneration &+= 1
        current = Snapshot(ownerUserId: owner, savedAt: savedAt, payload: payload)
        fileUnread = false
        guard let fileURL else { return }
        let envelope = AccountSnapshotEnvelope(
            schemaVersion: config.schemaVersion, ownerUserId: owner, savedAt: savedAt, parts: parts
        )
        let diskConfig = self.config
        enqueue { AccountSnapshotDisk.write(envelope, to: fileURL, config: diskConfig) }
    }

    // MARK: - Clear

    /// The session ended (auth.md §7). Unbinds, so nothing is shown or saved until an owner is
    /// bound again, and deletes the file.
    func clearForEndedSession() {
        let name = config.name
        epoch &+= 1
        boundOwner = nil
        current = nil
        fileUnread = false
        enqueueDelete()
        accountSnapshotLog.info("\(name, privacy: .public): cleared for the ended session")
    }

    /// Settings › Clear Cache, or a confirmed local edit. Drops the snapshot but keeps the
    /// binding, so the next live load saves a fresh one. Returns the queued delete so the caller
    /// can wait for it to land.
    @discardableResult
    func purgeCache() -> Task<Void, Never>? {
        epoch &+= 1
        current = nil
        fileUnread = false
        enqueueDelete()
        return diskTail
    }

    // MARK: - Disk tail

    private func enqueue(_ operation: @escaping @Sendable () -> Void) {
        let previous = diskTail
        diskTail = Task.detached(priority: .utility) {
            await previous?.value
            operation()
        }
    }

    private func enqueueDelete() {
        guard let fileURL else { return }
        let diskConfig = self.config
        enqueue { AccountSnapshotDisk.delete(at: fileURL, config: diskConfig) }
    }

    /// The prepare read, ordered behind anything already queued and ahead of anything after it.
    private func readOnDiskTail(_ url: URL) async -> AccountSnapshotReadOutcome {
        let diskConfig = self.config
        let previous = diskTail
        let read = Task.detached(priority: .userInitiated) { () -> AccountSnapshotReadOutcome in
            await previous?.value
            return AccountSnapshotDisk.read(at: url, config: diskConfig)
        }
        diskTail = Task.detached(priority: .utility) { _ = await read.value }
        return await read.value
    }
}
