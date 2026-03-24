//
//  LivePriceModels.swift
//  ios
//
//  Codable models for WebSocket live price messages from the backend.
//

import Foundation

struct LivePriceMessage: Codable {
    let type: String
    let symbol: String?
    let price: Double?
    let change: Double?
    let changePercent: Double?
    let volume: Int?
    let timestamp: Int?
    let message: String?

    enum CodingKeys: String, CodingKey {
        case type, symbol, price, change
        case changePercent = "change_percent"
        case volume, timestamp, message
    }
}
