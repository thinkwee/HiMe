//
//  himeApp.swift
//  hime
//
//  Created by HIME on 2026/3/13.
//

import SwiftUI
import BackgroundTasks

private let kBGRefreshID = "com.hime.healthkit.refresh"

// MARK: - AppDelegate

class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        handleEventsForBackgroundURLSession identifier: String,
        completionHandler: @escaping () -> Void
    ) {
        WebSocketClient.shared.addBackgroundCompletionHandler(identifier: identifier, completion: completionHandler)
    }

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: kBGRefreshID, using: nil) { task in
            guard let refreshTask = task as? BGAppRefreshTask else { return }
            HealthKitManager.shared.handleBackgroundRefresh(task: refreshTask)
        }

        return true
    }
}

// MARK: - App entry point

@main
struct himeApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    @StateObject private var hk = HealthKitManager.shared
    @StateObject private var ws = WebSocketClient.shared

    @AppStorage("hime.hasOnboarded") private var hasOnboarded: Bool = false
    @AppStorage("hime.hasConsentedToAIDataSharing") private var hasConsentedToAI: Bool = false

    private var isReady: Bool { hasOnboarded && hasConsentedToAI }

    init() {
        // Initialize WatchConnectivity (just access shared to trigger init)
        _ = PhoneConnectivityManager.shared
        // Only request HealthKit + run bootstrap if the user has already
        // completed onboarding AND granted AI data-sharing consent.
        if hasOnboarded && hasConsentedToAI {
            // Open the WebSocket before kicking HealthKit. Observer
            // callbacks fire immediately after registration and each one
            // triggers a flush; if WS isn't up yet, those flushes have
            // nowhere to go (foreground is WS-only by policy). Opening WS
            // here ensures it's ready by the time setup() finishes.
            WebSocketClient.shared.connect()
            Task {
                await HealthKitManager.shared.setup()
            }
        }
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if isReady {
                    ContentView()
                } else {
                    OnboardingView(hasOnboarded: $hasOnboarded)
                }
            }
                .environmentObject(hk)
                .environmentObject(ws)
                .onChange(of: hasOnboarded) { _, onboarded in
                    if onboarded {
                        // User just finished onboarding — HealthKit setup
                        // already happened during the Grant Access step.
                    }
                }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
                    let pending = PendingStore.shared.count
                    let bgTimeRemaining = UIApplication.shared.backgroundTimeRemaining
                    let timeStr = bgTimeRemaining > 999999 ? "unlimited" : String(format: "%.1fs", bgTimeRemaining)
                    HealthKitManager.bgLog("📱 LIFECYCLE: → BACKGROUND (pending=\(pending), bgTimeRemaining=\(timeStr), burst=\(HealthKitManager.shared.isBurstModeEnabled))")

                    let taskID = UIApplication.shared.beginBackgroundTask(withName: "HimeBackgroundFlush") {
                        // expiration handled below via taskID
                    }

                    if !HealthKitManager.shared.isBurstModeEnabled {
                        WebSocketClient.shared.disconnect(userInitiated: false)
                    }

                    Task {
                        await WebSocketClient.shared.flushPendingAndWait(appState: "background")
                        HealthKitManager.bgLog("📱 LIFECYCLE: background flush done (remaining=\(PendingStore.shared.count))")
                        UIApplication.shared.endBackgroundTask(taskID)
                    }

                    HealthKitManager.scheduleBackgroundRefresh()
                }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
                    let pending = PendingStore.shared.count
                    HealthKitManager.bgLog("📱 LIFECYCLE: → FOREGROUND (pending=\(pending))")
                    WebSocketClient.shared.reconnectIfNeeded()
                    WebSocketClient.shared.flushPending(appState: "foreground")
                }
        }
    }
}
