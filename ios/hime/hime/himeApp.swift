//
//  himeApp.swift
//  hime
//
//  Created by HIME on 2026/3/13.
//

import SwiftUI
import BackgroundTasks
import UserNotifications

private let kBGRefreshID = "com.hime.healthkit.refresh"

// MARK: - AppDelegate

class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
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

        UNUserNotificationCenter.current().delegate = self
        registerForPushIfConsented(application)
        return true
    }

    // MARK: - APNs (proactive push for in-app chat / reports)

    /// Request notification permission and register for remote notifications,
    /// but only once the user has onboarded and consented (mirrors the gate
    /// used for HealthKit). Idempotent — safe to call again post-consent.
    func registerForPushIfConsented(_ application: UIApplication) {
        let onboarded = UserDefaults.standard.bool(forKey: "hime.hasOnboarded")
        let consented = UserDefaults.standard.bool(forKey: "hime.hasConsentedToAIDataSharing")
        guard onboarded && consented else { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            guard granted else { return }
            DispatchQueue.main.async { application.registerForRemoteNotifications() }
        }
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { await uploadDeviceToken(hex) }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        print("APNs registration failed: \(error.localizedDescription)")
    }

    private func uploadDeviceToken(_ token: String) async {
        guard let url = URL(string: "\(ServerConfig.load().apiBaseURL)/api/devices/register") else { return }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        #if DEBUG
        let env = "sandbox"
        #else
        let env = "production"
        #endif
        let body: [String: Any] = [
            "device_token": token,
            "bundle_id": Bundle.main.bundleIdentifier ?? "",
            "environment": env,
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    // MARK: - UNUserNotificationCenterDelegate

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        // Show as a banner even when foregrounded (e.g. user on another tab).
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        // Every proactive notification we send is a chat reply / report, so a
        // tap deep-links into the Chat screen. ContentView observes the router
        // and pushes Chat (reconcile() then pulls in the just-arrived message).
        Task { @MainActor in AppRouter.shared.requestChat() }
        completionHandler()
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
                        // already happened during the Grant Access step. Now
                        // that consent is in place, register for push.
                        appDelegate.registerForPushIfConsented(UIApplication.shared)
                    }
                }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
                    let pending = PendingStore.shared.count
                    let bgTimeRemaining = UIApplication.shared.backgroundTimeRemaining
                    let timeStr = bgTimeRemaining > 999999 ? "unlimited" : String(format: "%.1fs", bgTimeRemaining)
                    HealthKitManager.bgLog("📱 LIFECYCLE: → BACKGROUND (pending=\(pending), bgTimeRemaining=\(timeStr), burst=\(HealthKitManager.shared.isBurstModeEnabled))")

                    // An empty expiration handler is fatal: when the server is
                    // unreachable the flush blocks past the ~30s budget and the
                    // watchdog kills the process (0x8badf00d), losing state that
                    // hasn't been persisted. Cancel the flush and end the task.
                    var taskID: UIBackgroundTaskIdentifier = .invalid
                    var flushTask: Task<Void, Never>?
                    taskID = UIApplication.shared.beginBackgroundTask(withName: "HimeBackgroundFlush") {
                        flushTask?.cancel()
                        if taskID != .invalid {
                            UIApplication.shared.endBackgroundTask(taskID)
                            taskID = .invalid
                        }
                    }

                    if !HealthKitManager.shared.isBurstModeEnabled {
                        WebSocketClient.shared.disconnect(userInitiated: false)
                    }

                    flushTask = Task {
                        await WebSocketClient.shared.flushPendingAndWait(appState: "background")
                        HealthKitManager.bgLog("📱 LIFECYCLE: background flush done (remaining=\(PendingStore.shared.count))")
                        if taskID != .invalid {
                            UIApplication.shared.endBackgroundTask(taskID)
                            taskID = .invalid
                        }
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
