//
//  AppRouter.swift
//  hime
//
//  Lightweight app-wide navigation router. Lets non-View code (the APNs tap
//  handler in AppDelegate) ask the UI to deep-link somewhere — currently just
//  "open the Chat screen" when the user taps a proactive notification.
//

import Combine
import SwiftUI

/// Routes the app can deep-link to.
enum AppRoute: Hashable {
    case chat
    /// Open the Reports tab and expand a specific report (from a chat bubble's
    /// "view full report" button).
    case report(id: Int)
}

@MainActor
final class AppRouter: ObservableObject {
    static let shared = AppRouter()

    /// Set by the notification-tap handler; ContentView observes it and pushes
    /// the Chat screen, then clears it. Published so a cold launch (value set
    /// before the view subscribes) still routes via `onReceive`.
    @Published var pendingRoute: AppRoute?

    /// The report the user asked to open from a chat bubble. ContentView switches
    /// to the Dashboard tab; DashboardView selects the Reports section and
    /// ReportsListSection scrolls to + expands this id, then clears it.
    @Published var pendingReportId: Int?

    private init() {}

    func requestChat() { pendingRoute = .chat }

    func requestReport(_ id: Int) {
        pendingReportId = id
        pendingRoute = .report(id: id)
    }
}
