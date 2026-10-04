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
        // A token captured on an earlier run may never have reached the server
        // (offline, wrong address, bad token) — retry until it gets a 2xx.
        DeviceTokenUploader.shared.uploadIfNeeded()
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
        DeviceTokenUploader.shared.setToken(hex)
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        print("APNs registration failed: \(error.localizedDescription)")
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
        // Replies in a non-main thread carry its id; proactive pushes have none (→ main).
        let threadId = response.notification.request.content.userInfo["thread_id"] as? String
        Task { @MainActor in AppRouter.shared.requestChat(threadId: threadId) }
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
            // Not user-initiated: a Disconnect the user chose in Settings is
            // persisted and must survive relaunches.
            WebSocketClient.shared.connect(userInitiated: false)
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
                    DeviceTokenUploader.shared.uploadIfNeeded()
                }
        }
    }
}

// MARK: - APNs device token upload

/// Persists the APNs device token and uploads it to the server until the
/// server answers 2xx. The registration used to be a single fire-and-forget
/// POST, so a transient failure (server down, token not yet entered, wrong
/// address) meant proactive push never worked until iOS happened to hand out
/// the token again. Retried on launch, on becoming active, and after the
/// server address / auth token are saved in Settings.
@MainActor
final class DeviceTokenUploader {
    static let shared = DeviceTokenUploader()

    private let tokenKey = "hime.apnsDeviceToken"
    /// "<token>|<apiBaseURL>" of the last upload the server accepted, so a new
    /// token OR a different server triggers a fresh registration.
    private let uploadedKey = "hime.apnsUploadedTo"
    private var isUploading = false

    private init() {}

    private var consented: Bool {
        UserDefaults.standard.bool(forKey: "hime.hasConsentedToAIDataSharing")
    }

    func setToken(_ token: String) {
        UserDefaults.standard.set(token, forKey: tokenKey)
        uploadIfNeeded()
    }

    func uploadIfNeeded() {
        Task { await upload() }
    }

    private func upload() async {
        guard !isUploading, consented,
              let token = UserDefaults.standard.string(forKey: tokenKey), !token.isEmpty else { return }
        let base = ServerConfig.load().apiBaseURL
        let marker = "\(token)|\(base)"
        guard UserDefaults.standard.string(forKey: uploadedKey) != marker else { return }
        guard let url = URL(string: "\(base)/api/devices/register") else { return }
        isUploading = true
        defer { isUploading = false }

        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 15
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
        guard let (_, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            HealthKitManager.bgLog("APNs: device token upload failed — will retry")
            return
        }
        UserDefaults.standard.set(marker, forKey: uploadedKey)
        HealthKitManager.bgLog("APNs: device token registered")
    }

    /// Consent revoked: stop receiving pushes — tell the server to forget the
    /// token (best effort), unregister from APNs and forget it locally.
    func unregister() {
        let token = UserDefaults.standard.string(forKey: tokenKey) ?? ""
        UserDefaults.standard.removeObject(forKey: tokenKey)
        UserDefaults.standard.removeObject(forKey: uploadedKey)
        UIApplication.shared.unregisterForRemoteNotifications()
        guard !token.isEmpty,
              let url = URL(string: "\(ServerConfig.load().apiBaseURL)/api/devices/unregister") else { return }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 15
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["device_token": token])
        let request = req
        Task { _ = try? await URLSession.shared.data(for: request) }
    }
}
