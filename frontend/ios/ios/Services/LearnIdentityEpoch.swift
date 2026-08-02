//
//  LearnIdentityEpoch.swift
//  ios
//
//  A monotonic counter of Learn IDENTITY changes (sign-out, account switch).
//
//  WHY THIS EXISTS
//  ---------------
//  The four Learn stores (Books / Journey / Money Moves / Bookmarks) persist to
//  DEVICE-GLOBAL UserDefaults keys — `bookLibrary.completedCores` and friends carry
//  no user id. On sign-out `AppState.signOut()` now clears them, but clearing alone
//  is not enough: a `hydrate()` issued moments earlier is still in flight, and when
//  it lands it would `merge()` the PREVIOUS account's server set back into the empty
//  store — and `pushUnsynced()` would then POST those items into the NEXT account,
//  making one user's completions and bookmarks durable inside another user's data.
//
//  So every store snapshots this epoch before it awaits, and drops the response if
//  the epoch moved while it was in flight. Same shape as each store's existing
//  per-store `localVersion` stale-snapshot guard (which covers local WRITES racing a
//  GET); this covers IDENTITY changing underneath one, which `localVersion` cannot
//  see because two different accounts each start from their own local state.
//
//  Deliberately not tied to a specific user id: the stores never learn who they
//  belong to, and giving them an identity to compare against would mean threading
//  auth state into four singletons. A change counter is enough to answer the only
//  question they need to ask — "is the account still the one I fetched for?"
//

import Foundation

@MainActor
enum LearnIdentityEpoch {

    /// Bumped whenever the Learn-owning identity changes. Read on the main actor only.
    private(set) static var current: UInt64 = 0

    /// Call when the signed-in identity changes (sign-out, or a switch to another account).
    /// Any Learn response already in flight is invalidated by the bump.
    static func bump() { current &+= 1 }
}
