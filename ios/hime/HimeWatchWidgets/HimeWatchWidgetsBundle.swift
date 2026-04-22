//
//  HimeWatchWidgetsBundle.swift
//  HimeWatchWidgets
//

import WidgetKit
import SwiftUI

@main
struct HimeWatchWidgetsBundle: WidgetBundle {
    var body: some Widget {
        CatStateComplication()
        HealthQuickComplication()
    }
}
