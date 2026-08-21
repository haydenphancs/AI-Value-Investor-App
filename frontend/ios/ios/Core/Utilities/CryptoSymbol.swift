//
//  CryptoSymbol.swift
//  ios
//
//  One place that knows the two forms a crypto symbol comes in.
//
//  The app receives BOTH: the search router and the notification routes hand over
//  the bare coin symbol ("BTC"), while Home's Market Pulse hands over the FMP pair
//  form ("BTCUSD" — see `_PULSE_SYMBOLS` in home_dashboard_service.py). Every site
//  that needed the pair built it inline as `"\(symbol)USD"`, which produced
//  "BTCUSDUSD" for anything arriving from Home. That symbol does not exist, so the
//  live-price WebSocket never delivered a tick and the 30-second intraday chart
//  refresh fetched nothing — the header price sat frozen for the whole session with
//  no error surfaced anywhere.
//
//  Both helpers are idempotent, so it is always safe to call them again.
//

import Foundation

enum CryptoSymbol {

    /// The bare coin symbol: `"BTCUSD" → "BTC"`, `"BTC" → "BTC"`.
    ///
    /// Strips only a TRAILING `USD`. A global replace is the bug this exists to
    /// prevent — it turns `USDT` into `T` and `USDC` into `C`, which is exactly
    /// what the backend's `_normalize_crypto_symbol` was fixed for. The
    /// `count > 3` guard keeps the currency ticker `USD` itself intact.
    static func bare(_ symbol: String) -> String {
        let s = symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard s.count > 3, s.hasSuffix("USD") else { return s }
        return String(s.dropLast(3))
    }

    /// The FMP pair form: `"BTC" → "BTCUSD"`, `"BTCUSD" → "BTCUSD"`.
    static func pair(_ symbol: String) -> String {
        bare(symbol) + "USD"
    }
}
