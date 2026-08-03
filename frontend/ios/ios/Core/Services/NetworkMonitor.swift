//
//  NetworkMonitor.swift
//  ios
//
//  Connectivity observation. The app had none — `AppState.isOnline` was declared and never
//  assigned, and there was no `import Network` anywhere in the target.
//
//  That gap is why a signed-in session could spend an entire app run as a guest: auth restore
//  ran exactly once at launch, and if the network happened to be down at that moment there was
//  nothing left in the process that could ever notice it came back.
//

import Foundation
import Network

/// Watches the system network path and announces the **false → true edge** — the moment the
/// device regains connectivity.
///
/// Only the edge is published, deliberately. `NWPathMonitor` fires on every path change
/// (Wi-Fi → cellular, interface reshuffles, DNS changes), and treating each one as "the network
/// came back" would re-trigger session restore repeatedly on a flaky connection. Consumers care
/// about recovery, not churn.
@MainActor
final class NetworkMonitor {

    static let shared = NetworkMonitor()

    /// Posted when connectivity is regained after having been lost.
    static let didRestoreNotification = Notification.Name("caydexNetworkPathRestored")

    /// Last known reachability. `nil` until the monitor delivers its first path, so a consumer
    /// can tell "no signal yet" from "known offline" and avoid acting on an assumption.
    private(set) var isOnline: Bool?

    private let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "com.phan.caydex.network-monitor")
    private var isStarted = false

    private init() {}

    func start() {
        guard !isStarted else { return }   // `start()` is called from the app root; be idempotent
        isStarted = true

        monitor.pathUpdateHandler = { [weak self] path in
            let satisfied = path.status == .satisfied
            Task { @MainActor [weak self] in
                self?.apply(isOnline: satisfied)
            }
        }
        monitor.start(queue: queue)
    }

    private func apply(isOnline satisfied: Bool) {
        let previous = isOnline
        isOnline = satisfied

        // Only the recovery edge. `previous == nil` is the first-ever path report at launch —
        // not a recovery, and firing there would duplicate the launch restore that is already
        // running at that moment.
        guard previous == false, satisfied else { return }
        NotificationCenter.default.post(name: Self.didRestoreNotification, object: nil)
    }
}
