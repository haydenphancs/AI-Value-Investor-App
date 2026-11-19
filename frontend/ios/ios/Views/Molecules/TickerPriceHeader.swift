//
//  TickerPriceHeader.swift
//  ios
//
//  Molecule: Ticker price display with company name, symbol, price and change
//

import SwiftUI

struct TickerPriceHeader: View {
    let companyName: String
    let symbol: String
    let price: String
    let priceChange: String
    let priceChangePercent: String
    let isPositive: Bool
    let marketStatus: MarketStatus
    /// `false` when the backend said the day change is UNKNOWN (`change_known`): the
    /// caller's `isPositive` is then a placeholder, so the header must not assert a
    /// direction — neutral colour, no arrow, no flash. Defaults `true` for the callers
    /// whose models carry no such flag yet.
    var changeKnown: Bool = true
    /// Set for a listing the backend CONFIRMED is delisted (`DelistedListing`): `price` is then
    /// its last close. The market badge gives way to a "Delisted" pill (a delisted listing has
    /// no session, so a clock-based "Market Open" would be false), the day change to "Last
    /// close · Nov 6, 2026", and the price never flashes. Nil for every other caller.
    var delisting: DelistedListing? = nil

    @State private var priceFlashColor: Color?

    private var changeColor: Color {
        guard changeKnown else { return AppColors.textSecondary }
        return isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var arrowIcon: String {
        isPositive ? "arrowtriangle.up.fill" : "arrowtriangle.down.fill"
    }

    var body: some View {
        HStack(alignment: .top) {
            // Left side - Company name and market status
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(companyName)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textSecondary)
                
                statusBadge
            }
            
            Spacer()
            
            // Right side - Price info
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text(price)
                    .font(AppTypography.title)
                    .foregroundColor(priceFlashColor ?? AppColors.textPrimary)
                    .contentTransition(.numericText())
                    .animation(.snappy(duration: 0.3), value: price)

                // Price change — or, for a delisted listing, what the price IS: its last
                // close. There is no day change to show.
                if let delisting {
                    lastCloseLabel(delisting)
                } else {
                    HStack(spacing: 4) {
                        // An arrow asserts a direction; an unknown change has none.
                        if changeKnown {
                            Image(systemName: arrowIcon)
                                .font(AppTypography.iconTiny).fontWeight(.semibold)
                                .foregroundColor(changeColor)
                        }

                        Text("\(priceChange) \(priceChangePercent)")
                            .font(AppTypography.labelSmall)
                            .fontWeight(.semibold)
                            .foregroundColor(changeColor)
                            .contentTransition(.numericText())
                            .animation(.snappy(duration: 0.3), value: priceChange)
                    }
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .onChange(of: price) { _, _ in
            // Brief color flash on price update (green for positive, red for negative)
            guard changeKnown else { return }
            guard delisting == nil else { return }
            priceFlashColor = isPositive ? AppColors.bullish : AppColors.bearish
            withAnimation(.easeOut(duration: 0.6)) {
                priceFlashColor = nil
            }
        }
    }

    /// The left column's status line: the market badge, or — for a confirmed delisting — the
    /// "Delisted" pill over "from NASDAQ · Nov 9, 2026". Not a `MarketStatus` case: that enum
    /// is shared and switched exhaustively in five files. `textSecondary` ink on the pill's
    /// own opaque surface (never `neutral`, which is amber).
    @ViewBuilder
    private var statusBadge: some View {
        if let delisting {
            VStack(alignment: .leading, spacing: 2) {
                TintedTagBadge(text: DelistedListing.pillText, color: AppColors.textSecondary)
                if let line = delisting.detailLine {
                    Text(line)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.85)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(delisting.spokenHeadline)
        } else {
            MarketStatusBadge(status: marketStatus)
        }
    }

    /// "Last close · Nov 6, 2026" under a delisted listing's price: no arrow, neutral ink.
    private func lastCloseLabel(_ delisting: DelistedListing) -> some View {
        Text(delisting.lastCloseLine)
            .font(AppTypography.labelSmall)
            .fontWeight(.semibold)
            .foregroundColor(AppColors.textSecondary)
            .lineLimit(1)
            .minimumScaleFactor(0.85)
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        TickerPriceHeader(
            companyName: "Apple Inc.",
            symbol: "AAPL",
            price: "$178.42",
            priceChange: "+$2.34",
            priceChangePercent: "(+1.33%)",
            isPositive: true,
            // A fixed close inside EDT: `Date()` with "EST" was a false label March to November.
            marketStatus: .closed(
                date: Calendar(identifier: .gregorian).date(from: DateComponents(
                    timeZone: TimeZone(identifier: "America/New_York"),
                    year: 2026, month: 10, day: 28, hour: 16
                ))!,
                time: "4:00 PM",
                timezone: "EDT"
            )
        )

        TickerPriceHeader(
            companyName: "Tesla, Inc.",
            symbol: "TSLA",
            price: "$252.18",
            priceChange: "-$3.45",
            priceChangePercent: "(-1.35%)",
            isPositive: false,
            marketStatus: .open
        )

        // Unknown change: em dash, no arrow, neutral — never a red decline.
        TickerPriceHeader(
            companyName: "S&P 500",
            symbol: "^GSPC",
            price: "6,012.34",
            priceChange: "—",
            priceChangePercent: "",
            isPositive: false,
            marketStatus: .open,
            changeKnown: false
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}

// A confirmed delisting: the pill and its exchange/date line replace the market badge, the
// price is the last close and says so. Nothing here may claim a live session.
#Preview("Delisted") {
    VStack(spacing: AppSpacing.xl) {
        TickerPriceHeader(
            companyName: "Roku, Inc.",
            symbol: "ROKU",
            price: "$160.52",
            priceChange: "—",
            priceChangePercent: "",
            isPositive: false,
            marketStatus: .open,
            changeKnown: false,
            delisting: .sample
        )

        // Fields missing: the clauses drop, nothing is guessed.
        TickerPriceHeader(
            companyName: "Example Corp.",
            symbol: "EXMP",
            price: "$12.04",
            priceChange: "—",
            priceChangePercent: "",
            isPositive: false,
            marketStatus: .open,
            changeKnown: false,
            delisting: DelistedListing(delistedDay: nil, exchange: "NYSE", lastTradeDay: nil)
        )
    }
    .padding(.vertical)
    .background(AppColors.background)
}
