//
//  MembershipWriteSequencer.swift
//  ios
//
//  The bookkeeping half of `PortfolioStore`'s per-portfolio write chain: which membership
//  write is the newest, how many writes are still queued, and whether a portfolio's
//  membership moved while a `GET /portfolios` was in flight.
//
//  Foundation-only and free of Tasks on purpose: `test_ios_holdings_sort_behaviour.py` pipes
//  this file into `xcrun swift -` and drives the rules directly. The store owns the chain and
//  the confirmed items; this type only answers "is this still the newest write?".
//
//  WHY A CHAIN. Every membership edit (a Manage Tickers drag, a remove, an add) is a WHOLE-LIST
//  `PUT /portfolios/{id}/tickers`, and each gesture used to send its own concurrently:
//    * a late response overwrote a newer order already on screen;
//    * a failure reverted to the list captured before ITS edit, wiping newer edits;
//    * on the server, two interleaved delete-all + upsert swaps could bring a deleted ticker
//      back (the upsert of one request re-adds a row the other removed).
//  The store now sends a portfolio's writes ONE AT A TIME, each with the list as it stood right
//  after its own optimistic edit, and lets only the NEWEST write touch what is on screen.
//

import Foundation

struct MembershipWriteSequencer {
    /// The newest ticket handed out per portfolio.
    private var lastTicket: [String: Int] = [:]
    /// Writes queued or in flight per portfolio (membership and holdings alike).
    private var pending: [String: Int] = [:]
    /// Bumped when a write is queued AND when one finishes. A load compares the value it saw
    /// before its request with the value after: a move means a write was queued or settled
    /// meanwhile, so the loaded membership may predate it. (Bumping on enqueue alone missed a
    /// write already in flight at request time that finished before the answer: the load saw
    /// nothing pending and nothing moved, and published the older list over the new one.)
    private var revisions: [String: Int] = [:]

    /// A membership write is being queued for `portfolioId`: its ticket.
    mutating func enqueueMembership(_ portfolioId: String) -> Int {
        let ticket = (lastTicket[portfolioId] ?? 0) &+ 1
        lastTicket[portfolioId] = ticket
        enqueueOther(portfolioId)
        return ticket
    }

    /// A non-membership write (holdings) joins the same chain. It takes no ticket, so it never
    /// stops the newest MEMBERSHIP write from being applied or reverted.
    mutating func enqueueOther(_ portfolioId: String) {
        pending[portfolioId, default: 0] += 1
        revisions[portfolioId, default: 0] &+= 1
    }

    /// True when no membership write was queued for `portfolioId` after `ticket`.
    func isNewest(_ portfolioId: String, ticket: Int) -> Bool {
        lastTicket[portfolioId] == ticket
    }

    /// A queued write (either kind) has finished, successfully or not.
    mutating func finish(_ portfolioId: String) {
        guard let count = pending[portfolioId] else { return }
        revisions[portfolioId, default: 0] &+= 1
        if count <= 1 {
            pending.removeValue(forKey: portfolioId)
        } else {
            pending[portfolioId] = count - 1
        }
    }

    func hasPending(_ portfolioId: String) -> Bool {
        (pending[portfolioId] ?? 0) > 0
    }

    /// Every portfolio's revision, to compare after a load.
    func revisionSnapshot() -> [String: Int] {
        revisions
    }

    func revision(_ portfolioId: String) -> Int {
        revisions[portfolioId] ?? 0
    }

    /// The portfolio was deleted: forget it. Its queued writes still finish (and fail).
    mutating func forget(_ portfolioId: String) {
        lastTicket.removeValue(forKey: portfolioId)
        pending.removeValue(forKey: portfolioId)
        revisions.removeValue(forKey: portfolioId)
    }

    /// The session ended. Links from it resolve without touching the new session's state
    /// (they check the store's identity epoch), so nothing here may survive.
    mutating func reset() {
        lastTicket = [:]
        pending = [:]
        revisions = [:]
    }
}
