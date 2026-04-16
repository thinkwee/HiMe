//
//  HimeWidgetsBundle.swift
//  HimeWidgets
//

import WidgetKit
import SwiftUI

@main
struct HimeWidgetsBundle: WidgetBundle {
    var body: some Widget {
        CatStatusWidget()
        HealthMetricsWidget()
        LatestReportWidget()
    }
}
