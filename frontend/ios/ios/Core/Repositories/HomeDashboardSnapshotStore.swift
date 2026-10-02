//
//  HomeDashboardSnapshotStore.swift
//  ios
//
//  The last good Home dashboard, kept on THIS device for THIS account, so a cold launch paints
//  it at once instead of waiting on the network.
//
//  WHY THIS EXISTS. TestFlight 1.0 (9), 2026-09-23, roaming after the US close: "At initial, it
//  loads so slow". Nothing was kept on the device, so the first Home frame waited for the whole
//  chain — launch, DNS + TCP + TLS (~3 round trips at 350-600 ms roaming), the server's six-way
//  gather (p50 1.4 s, 8 s after a deploy) and the download. Now the first frame is the last
//  dashboard this account saw, labelled "Updated <time>", and the live load replaces it in the
//  background.
//
//  WHAT IS STORED — the RAW RESPONSE BYTES, never `HomeDashboardData`. That type holds `Color`s,
//  `UUID`s and a `TrillionClubGroup`, so it is not Codable; and re-mapping the bytes through
//  `HomeRepository`'s decode-safe DTOs (every late field Optional) is what lets a newer build
//  read an older file — or reject it cleanly and fall back to the skeleton.
//
//  WHERE — `Library/Caches/HomeDashboard/home-dashboard-snapshot.v1.plist`, one binary plist,
//  written `.atomic` with `.completeFileProtectionUntilFirstUserAuthentication`. Caches is never
//  backed up, so the file cannot follow a restore onto a new phone that lacks the
//  `...ThisDeviceOnly` Keychain session it belongs to; the OS may evict it, which is right for a
//  re-creatable cache (`LearnAudioCache` is the precedent).
//
//  ⚠️ OWNER-FENCED (auth.md §7). The file holds one account's watchlist, group name and prices.
//    • `prime` runs at launch, BEFORE the tab tree mounts, with the owner read from the stored
//      token's `sub` (`WidgetJWT.subject`). No credential, a foreign owner, another schema, an
//      age past `maxDisplayAge` or a date in the future → the file is deleted and logged.
//    • `bindOwner` (AppState.applyProfile, and again after the account-switch discard) moves
//      the binding; a different owner bumps `epoch` and deletes the file.
//    • `clearForEndedSession` (AppState.discardDataForEndedSession) unbinds and deletes.
//    • `save` is refused unless the caller's captured `epoch` still matches, an owner is bound
//      and the dashboard is complete (`HomeDashboardData.isWorthPersisting`). While the saved
//      snapshot is still displayable and its watchlist has tiles, a body whose watchlist came
//      back EMPTY in a failed read's shape — the server's degraded default, or the same list
//      with every tile gone (`hasDegradedWatchlist`) — is refused too: a degraded 200 never
//      overwrites a good snapshot, and the refusal lapses with the display window.
//  Every disk operation runs in order on ONE detached tail (`diskTail`), so a delete enqueued
//  after a pending write always lands last: a session end cannot be resurrected by a late write.
//
//  ⚠️ NOT A GATE. The server's own redaction (signals tickers, the watchlist) is the real gate;
//  the snapshot keeps exactly what the server sent this account (owner decision 2026-10-01).
//

import Foundation
import OSLog

// File-scope and explicitly `nonisolated`: read from the detached disk tail, and the target
// defaults to MainActor isolation (same lever as `LearnAudioCache`'s constants).
nonisolated private let homeSnapshotLog = Logger(subsystem: "com.phan.caydex", category: "home-snapshot")

/// The on-disk envelope. Binary plist, so `body` is stored as raw bytes (no base64).
///
/// `nonisolated` so its Codable conformance is usable off the main actor — it is encoded and
/// decoded on the detached disk tail.
nonisolated struct HomeDashboardSnapshotEnvelope: Codable, Sendable {
    let schemaVersion: Int
    /// Lowercased. Compared against the bound owner on every read.
    let ownerUserId: String
    let savedAt: Date
    /// The exact `GET /home/dashboard` response bytes.
    let body: Data
}

/// What one read of the file found. A READ failure (I/O, data protection before first unlock)
/// is kept apart from a CORRUPT file on purpose: only the second is grounds to delete.
nonisolated enum HomeSnapshotReadOutcome: Sendable {
    case missing
    case readFailed(String)
    case corrupt(String)
    case envelope(HomeDashboardSnapshotEnvelope)
}

/// The file I/O, all synchronous and run ONLY on the store's detached disk tail.
nonisolated enum HomeDashboardSnapshotDisk {

    static func read(at url: URL) -> HomeSnapshotReadOutcome {
        guard FileManager.default.fileExists(atPath: url.path) else { return .missing }
        let bytes: Data
        do {
            bytes = try Data(contentsOf: url)
        } catch {
            return .readFailed("\(type(of: error)): \(error)")
        }
        // The envelope adds a few hundred bytes of plist structure around the body.
        guard bytes.count <= HomeDashboardSnapshotStore.maxBodyBytes + 16 * 1024 else {
            return .corrupt("file is \(bytes.count) bytes, over the \(HomeDashboardSnapshotStore.maxBodyBytes)-byte cap")
        }
        do {
            return .envelope(try PropertyListDecoder().decode(HomeDashboardSnapshotEnvelope.self, from: bytes))
        } catch {
            return .corrupt("envelope decode failed — \(type(of: error)): \(error)")
        }
    }

    static func write(_ envelope: HomeDashboardSnapshotEnvelope, to url: URL) {
        do {
            let encoder = PropertyListEncoder()
            encoder.outputFormat = .binary
            let bytes = try encoder.encode(envelope)
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true
            )
            try bytes.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            homeSnapshotLog.debug("home-snapshot: saved \(bytes.count, privacy: .public) bytes")
        } catch {
            let detail = "\(type(of: error)): \(error)"
            homeSnapshotLog.warning("home-snapshot: write failed — \(detail, privacy: .public)")
        }
    }

    static func delete(at url: URL) {
        let fm = FileManager.default
        guard fm.fileExists(atPath: url.path) else { return }
        do {
            try fm.removeItem(at: url)
            homeSnapshotLog.info("home-snapshot: file deleted")
        } catch {
            // Logged, not retried: the next launch's `prime` re-checks the owner and age of
            // whatever survived and deletes it again.
            let detail = "\(type(of: error)): \(error)"
            homeSnapshotLog.warning("home-snapshot: delete failed — \(detail, privacy: .public)")
        }
    }

    /// Why this envelope must not be shown to `owner`, or nil when it may.
    static func rejection(of envelope: HomeDashboardSnapshotEnvelope, owner: String, now: Date) -> String? {
        if envelope.schemaVersion != HomeDashboardSnapshotStore.schemaVersion {
            return "schema \(envelope.schemaVersion), expected \(HomeDashboardSnapshotStore.schemaVersion)"
        }
        if HomeDashboardSnapshotStore.normalizedOwner(envelope.ownerUserId) != owner {
            return "owned by another account"
        }
        if !HomeDashboardSnapshotStore.isDisplayable(savedAt: envelope.savedAt, now: now) {
            return "saved \(Int(now.timeIntervalSince(envelope.savedAt))) s ago — outside the display window"
        }
        if envelope.body.isEmpty || envelope.body.count > HomeDashboardSnapshotStore.maxBodyBytes {
            return "body is \(envelope.body.count) bytes"
        }
        return nil
    }
}

@MainActor
final class HomeDashboardSnapshotStore {

    /// A snapshot ready to render: the owner it was saved for, when, and the mapped dashboard.
    struct Snapshot {
        let ownerUserId: String
        let savedAt: Date
        let data: HomeDashboardData
    }

    // MARK: - Constants

    /// Bump with any change to the envelope's SHAPE (not the body's — the DTOs absorb that).
    nonisolated static let schemaVersion = 1

    /// 96 h: covers a long weekend (owner decision 2026-10-01). Must stay below the refresh
    /// token's life minus the access token's (7 d − 24 h): a saved snapshot only proves an
    /// access token was minted within 24 h before the save, so this bound keeps a provably dead
    /// session's dashboard off the screen. Pinned by `test_ios_home_instant_paint_guards.py`.
    nonisolated static let maxDisplayAge: TimeInterval = 96 * 60 * 60

    /// A clock set back after the save would otherwise make the snapshot look newer than now.
    nonisolated static let maxFutureSkew: TimeInterval = 5 * 60

    /// A real dashboard is tens of KB. Anything this large is not one, and is never written.
    nonisolated static let maxBodyBytes = 512 * 1024

    nonisolated static let fileName = "home-dashboard-snapshot.v1.plist"

    /// `Library/Caches/HomeDashboard`, or nil when the sandbox has no Caches directory.
    nonisolated static var defaultDirectory: URL? {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
            .appendingPathComponent("HomeDashboard", isDirectory: true)
    }

    static let shared = HomeDashboardSnapshotStore(directory: HomeDashboardSnapshotStore.defaultDirectory)

    // MARK: - State

    /// Bumped by every change of WHO the store serves (bind to another owner, session end) and
    /// by a purge. A load captures it before its request; `save` refuses a stale capture, so a
    /// response that left under the previous identity can never be written under the next.
    private(set) var epoch = 0
    private var boundOwner: String?
    private var current: Snapshot?
    /// The last queued disk operation. Every read, write and delete awaits its predecessor.
    private var diskTail: Task<Void, Never>?
    /// nil = memory only (previews).
    private let fileURL: URL?

    init(directory: URL?) {
        fileURL = directory?.appendingPathComponent(Self.fileName, isDirectory: false)
    }

    /// A memory-only store, optionally pre-seeded — for previews. Never touches the disk.
    static func inMemory(
        seed: HomeDashboardData? = nil,
        savedAt: Date = Date(),
        owner: String = "preview-user"
    ) -> HomeDashboardSnapshotStore {
        let store = HomeDashboardSnapshotStore(directory: nil)
        let owner = normalizedOwner(owner) ?? "preview-user"
        store.boundOwner = owner
        if let seed {
            store.current = Snapshot(ownerUserId: owner, savedAt: savedAt, data: seed)
        }
        return store
    }

    // MARK: - Owner

    nonisolated static func normalizedOwner(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              !trimmed.isEmpty else { return nil }
        return trimmed
    }

    nonisolated static func isDisplayable(savedAt: Date, now: Date) -> Bool {
        let age = now.timeIntervalSince(savedAt)
        return age <= maxDisplayAge && age >= -maxFutureSkew
    }

    /// Launch: bind the stored credential's owner and load that owner's snapshot into memory.
    ///
    /// Called from `AppState.configure` AFTER the credential is armed and BEFORE the restore
    /// publishes `.restoring` — that publish is what mounts the tabs, and the Home ViewModel
    /// seeds itself from `snapshotForDisplay()` in its init. Reads the token's `sub` claim only;
    /// it authenticates nothing.
    func prime(ownerUserId: String?, apiClient: APIClient = .shared) async {
        // Timed: this runs on the launch path, ahead of the restore.
        let started = Date()
        let owner = Self.normalizedOwner(ownerUserId)
        if owner != boundOwner {
            epoch &+= 1
            current = nil
        }
        boundOwner = owner
        let primedEpoch = epoch

        guard let owner else {
            // No credential, so nobody's dashboard may stay on this device.
            enqueueDelete()
            return
        }
        guard let fileURL else { return }

        let outcome = await readOnDiskTail(fileURL)
        // Anything that moved the binding while the read was suspended owns the file now.
        guard epoch == primedEpoch, boundOwner == owner else {
            homeSnapshotLog.info("home-snapshot: prime superseded during the read — file ignored")
            return
        }

        let envelope: HomeDashboardSnapshotEnvelope
        switch outcome {
        case .missing:
            homeSnapshotLog.info("home-snapshot: miss — nothing saved on this device")
            return
        case .readFailed(let reason):
            // Kept: a read refused by data protection (a launch before first unlock) says
            // nothing about the file.
            homeSnapshotLog.warning("home-snapshot: read failed, file kept — \(reason, privacy: .public)")
            return
        case .corrupt(let reason):
            homeSnapshotLog.warning("home-snapshot: discarding the file — \(reason, privacy: .public)")
            enqueueDelete()
            return
        case .envelope(let read):
            envelope = read
        }

        if let reason = HomeDashboardSnapshotDisk.rejection(of: envelope, owner: owner, now: Date()) {
            homeSnapshotLog.warning("home-snapshot: discarding the file — \(reason, privacy: .public)")
            enqueueDelete()
            return
        }

        do {
            let data = try await HomeRepository.dashboard(fromSnapshotBody: envelope.body, apiClient: apiClient)
            // Re-checked after the decode too. `current == nil`: a live save that landed in
            // the meantime is newer than this file and must not be replaced by it.
            guard epoch == primedEpoch, boundOwner == owner, current == nil else {
                homeSnapshotLog.info("home-snapshot: prime superseded during the decode — file ignored")
                return
            }
            current = Snapshot(ownerUserId: owner, savedAt: envelope.savedAt, data: data)
            let ageSeconds = Int(Date().timeIntervalSince(envelope.savedAt))
            let byteCount = envelope.body.count
            let primeMillis = Int(Date().timeIntervalSince(started) * 1000)
            homeSnapshotLog.info("home-snapshot: hit — \(ageSeconds, privacy: .public) s old, \(byteCount, privacy: .public) bytes, primed in \(primeMillis, privacy: .public) ms")
        } catch {
            let detail = "\(type(of: error)): \(error)"
            homeSnapshotLog.warning("home-snapshot: decode failed — \(detail, privacy: .public)")
            // Only while nothing newer was saved: a delete now would remove THAT file.
            if epoch == primedEpoch, boundOwner == owner, current == nil {
                enqueueDelete()
            }
        }
    }

    /// The signed-in account is now `userId`. A different owner than the bound one drops the
    /// in-memory snapshot, deletes the file and voids every captured epoch.
    func bindOwner(_ userId: String) {
        let owner = Self.normalizedOwner(userId)
        guard owner != boundOwner else { return }
        epoch &+= 1
        current = nil
        boundOwner = owner
        enqueueDelete()
        homeSnapshotLog.info("home-snapshot: bound to a new account — previous snapshot dropped")
    }

    // MARK: - Read

    /// The snapshot to paint, or nil. Only the BOUND owner's, and only inside the display window.
    func snapshotForDisplay(now: Date = Date()) -> Snapshot? {
        guard let owner = boundOwner,
              let snapshot = current,
              snapshot.ownerUserId == owner,
              Self.isDisplayable(savedAt: snapshot.savedAt, now: now) else { return nil }
        return snapshot
    }

    // MARK: - Write

    /// Persist a LIVE dashboard. `epoch` is the value the caller captured BEFORE its request.
    func save(body: Data, data: HomeDashboardData, epoch captured: Int) {
        guard captured == epoch else {
            homeSnapshotLog.info("home-snapshot: save refused — the account changed while the load was in flight")
            return
        }
        guard let owner = boundOwner else {
            homeSnapshotLog.info("home-snapshot: save refused — no account bound")
            return
        }
        // Values are hoisted into locals before every log call: OSLog interpolations are
        // escaping autoclosures, which must not read main-actor state.
        guard data.isWorthPersisting else {
            let tiles = data.equityPulseTileCount
            let expected = HomeDashboardData.expectedEquityPulseTiles
            homeSnapshotLog.warning("home-snapshot: save refused — degraded dashboard (\(tiles, privacy: .public)/\(expected, privacy: .public) equity pulse tiles, or every other section empty); the previous snapshot is kept")
            return
        }
        // The server flags no degraded watchlist. A read that timed out or failed comes back as
        // the default title, not a group, no tiles — what a user with no tickers gets — and a
        // failed quote fetch drops every tile of the SAME list (`_build_watchlist`). Over a
        // saved watchlist WITH tiles, assume the blip (`hasDegradedWatchlist`): otherwise the
        // next cold launch paints Home without the user's own tickers. Only while the saved
        // snapshot is still displayable, so the refusal lapses with it. Accepted cost: a user
        // who really emptied that list keeps the older snapshot until then (`maxDisplayAge`) —
        // it is labelled with its real time, and live data replaces it within seconds.
        if let previous = current,
           previous.ownerUserId == owner,
           Self.isDisplayable(savedAt: previous.savedAt, now: Date()),
           data.hasDegradedWatchlist(comparedTo: previous.data) {
            let keptTiles = previous.data.watchlist.count
            let shape: String = data.hasDefaultEmptyWatchlist
                ? "under the default title (the server's degraded-read shape)"
                : "under the same list (a failed quote fetch drops every tile)"
            homeSnapshotLog.info("home-snapshot: save refused — the watchlist came back empty \(shape, privacy: .public) over a saved one with \(keptTiles, privacy: .public) tiles; the previous snapshot is kept")
            return
        }
        let byteCount = body.count
        guard byteCount > 0, byteCount <= Self.maxBodyBytes else {
            homeSnapshotLog.warning("home-snapshot: save refused — body is \(byteCount, privacy: .public) bytes")
            return
        }
        let savedAt = Date()
        current = Snapshot(ownerUserId: owner, savedAt: savedAt, data: data)
        guard let fileURL else { return }
        let envelope = HomeDashboardSnapshotEnvelope(
            schemaVersion: Self.schemaVersion, ownerUserId: owner, savedAt: savedAt, body: body
        )
        enqueue { HomeDashboardSnapshotDisk.write(envelope, to: fileURL) }
    }

    // MARK: - Clear

    /// The session ended (auth.md §7). Unbinds, so nothing is shown or saved until an owner is
    /// bound again, and deletes the file.
    func clearForEndedSession() {
        epoch &+= 1
        boundOwner = nil
        current = nil
        enqueueDelete()
        homeSnapshotLog.info("home-snapshot: cleared for the ended session")
    }

    /// Settings › Clear Cache. Drops the snapshot but keeps the binding, so the next live load
    /// saves a fresh one. Returns the queued delete so the caller can recount the cache size
    /// once it has landed.
    @discardableResult
    func purgeCache() -> Task<Void, Never>? {
        epoch &+= 1
        current = nil
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
        enqueue { HomeDashboardSnapshotDisk.delete(at: fileURL) }
    }

    /// The launch read, ordered behind anything already queued and ahead of anything after it.
    private func readOnDiskTail(_ url: URL) async -> HomeSnapshotReadOutcome {
        let previous = diskTail
        let read = Task.detached(priority: .userInitiated) { () -> HomeSnapshotReadOutcome in
            await previous?.value
            return HomeDashboardSnapshotDisk.read(at: url)
        }
        diskTail = Task.detached(priority: .utility) { _ = await read.value }
        return await read.value
    }
}
