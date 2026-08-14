//
//  CaydexWidgetsBundle.swift
//  CaydexWidgets
//
//  Entry point for the widget extension. One widget today; the bundle exists so adding
//  a second (an earnings countdown, a watchlist strip) is a one-line change rather than
//  a target restructure.
//

import SwiftUI
import WidgetKit

@main
struct CaydexWidgetsBundle: WidgetBundle {
    var body: some Widget {
        MoversWidget()
    }
}
