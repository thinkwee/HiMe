//
//  PhoneConnectivityManager.swift
//  hime
//
//  Created by HIME on 2026/3/20.
//

import WatchConnectivity
import Foundation
import Combine
import UIKit

class PhoneConnectivityManager: NSObject, ObservableObject {
    @MainActor static let shared = PhoneConnectivityManager()

    @MainActor @Published var isWatchReachable: Bool = false
    @MainActor @Published var watchSamplesReceived: Int = 0

    private override init() {
        super.init()
        if WCSession.isSupported() {
            let session = WCSession.default
            session.delegate = self
            session.activate()
        }
    }

    /// WCSession holds exactly ONE application context. Writing different
    /// message kinds into it made each overwrite the last — a `server_config`
    /// push clobbered by the next `cat_state` write left the watch with a nil
    /// `serverIngestURL` forever, permanently disabling direct HTTP upload.
    /// Keep one composite dictionary and merge updates into it.
    @MainActor private var latestContext: [String: Any] = ["type": "composite"]

    @MainActor
    private func mergeApplicationContext(_ delta: [String: Any]) {
        guard WCSession.default.activationState == .activated else { return }
        latestContext.merge(delta) { _, new in new }
        do {
            try WCSession.default.updateApplicationContext(latestContext)
        } catch {
            LogManager.shared.log("WCSession updateApplicationContext failed: \(error.localizedDescription)")
        }
    }

    /// Send cat state to Watch after quick analysis
    @MainActor
    func sendCatState(_ state: String, message: String) {
        guard WCSession.default.activationState == .activated else { return }
        let data: [String: Any] = [
            "type": "cat_state",
            "state": state,
            "message": message
        ]
        if WCSession.default.isReachable {
            WCSession.default.sendMessage(data, replyHandler: nil, errorHandler: { error in
                LogManager.shared.log("WCSession sendCatState failed: \(error.localizedDescription)")
            })
        }
        // Always mirror into the composite context so a watch that wakes later
        // still sees the newest state (and doesn't lose the server config).
        mergeApplicationContext(["cat_state": state, "cat_message": message])
    }

    /// Send notification text to Watch
    @MainActor
    func sendNotification(_ text: String) {
        guard WCSession.default.activationState == .activated else { return }
        let data: [String: Any] = ["type": "notification", "text": text]
        if WCSession.default.isReachable {
            WCSession.default.sendMessage(data, replyHandler: nil, errorHandler: { error in
                LogManager.shared.log("WCSession sendNotification failed: \(error.localizedDescription)")
            })
        } else {
            // Transient one-shot — queue it rather than parking it in the
            // single application-context slot where it would clobber config.
            WCSession.default.transferUserInfo(data)
        }
    }

    /// Push server config to Watch so it can upload directly
    @MainActor
    func syncServerConfigToWatch() {
        guard WCSession.default.activationState == .activated else { return }
        let config = WebSocketClient.shared.serverConfig
        let ingestURL = config.watchHTTPBaseURL + "/ingest"
        mergeApplicationContext(["ingest_url": ingestURL])
    }

    /// Process health data received from Watch
    @MainActor
    private func processWatchHealthData(_ payloads: [[String: Any]]) {
        var healthPayloads: [HealthPayload] = []
        for p in payloads {
            guard let ts = p["ts"] as? Double,
                  let v = p["v"] as? Double,
                  let f = p["f"] as? String else { continue }
            healthPayloads.append(HealthPayload(ts: ts, value: v, feature: f))
        }

        guard !healthPayloads.isEmpty else { return }

        PendingStore.shared.append(healthPayloads)
        watchSamplesReceived += healthPayloads.count

        // This method is @MainActor and is only invoked from `Task { @MainActor
        // in … }`, so we are already on the main thread. UIApplication APIs are
        // main-thread-only, so call them directly — wrapping them in
        // `DispatchQueue.main.sync` from the main thread deadlocks the thread on
        // itself, freezing the app every time the Watch delivers health data.
        let appState = (UIApplication.shared.applicationState == .active) ? "foreground" : "background"

        // The expiration handler MUST end the task. An empty handler means the
        // watchdog force-kills the process (0x8badf00d) whenever the flush
        // outlives the ~30s budget — e.g. when the server is unreachable.
        var taskID: UIBackgroundTaskIdentifier = .invalid
        var flushTask: Task<Void, Never>?
        taskID = UIApplication.shared.beginBackgroundTask(withName: "WatchDataFlush") {
            flushTask?.cancel()
            if taskID != .invalid {
                UIApplication.shared.endBackgroundTask(taskID)
                taskID = .invalid
            }
        }

        HealthKitManager.bgLog("📱 WC-RECV: \(healthPayloads.count) samples from Watch (appState=\(appState), pending=\(PendingStore.shared.count))")

        flushTask = Task {
            await WebSocketClient.shared.flushPendingAndWait(appState: appState)
            HealthKitManager.bgLog("📱 WC-RECV: Flush completed (remaining=\(PendingStore.shared.count))")
            await MainActor.run {
                if taskID != .invalid {
                    UIApplication.shared.endBackgroundTask(taskID)
                    taskID = .invalid
                }
            }
        }
    }
}

// MARK: - WCSessionDelegate

extension PhoneConnectivityManager: WCSessionDelegate {
    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        Task { @MainActor in
            self.isWatchReachable = session.isReachable
            HealthKitManager.bgLog("📱 WC-SESSION: activated (state=\(activationState.rawValue), watchReachable=\(session.isReachable))")
            if activationState == .activated {
                self.syncServerConfigToWatch()
            }
        }
    }

    func sessionDidBecomeInactive(_ session: WCSession) {
        Task { @MainActor in
            HealthKitManager.bgLog("📱 WC-SESSION: became inactive")
        }
    }
    func sessionDidDeactivate(_ session: WCSession) {
        Task { @MainActor in
            HealthKitManager.bgLog("📱 WC-SESSION: deactivated — reactivating")
        }
        session.activate()
    }

    func sessionReachabilityDidChange(_ session: WCSession) {
        Task { @MainActor in
            self.isWatchReachable = session.isReachable
            HealthKitManager.bgLog("📱 WC-SESSION: reachability changed → watchReachable=\(session.isReachable)")
            if session.isReachable {
                self.syncServerConfigToWatch()
            }
        }
    }

    // Real-time messages from Watch
    func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        Task { @MainActor in
            self.handleWatchMessage(message)
        }
    }

    /// The watch sends via `sendMessage(_:replyHandler:errorHandler:)`
    /// (WatchConnectivityManager.sendHealthData). WatchConnectivity delivers a
    /// message that carries a replyHandler ONLY to this variant — without it
    /// the sender's errorHandler fires every time and the realtime channel
    /// silently degrades to the batched transferUserInfo queue.
    func session(_ session: WCSession, didReceiveMessage message: [String: Any],
                 replyHandler: @escaping ([String: Any]) -> Void) {
        Task { @MainActor in
            self.handleWatchMessage(message)
        }
        replyHandler([:])
    }

    @MainActor
    private func handleWatchMessage(_ message: [String: Any]) {
        guard let type = message["type"] as? String else { return }
        if type == "health_data", let payloads = message["payloads"] as? [[String: Any]] {
            HealthKitManager.bgLog("📱 WC-MSG: didReceiveMessage with \(payloads.count) health payloads (realtime)")
            self.processWatchHealthData(payloads)
        } else if type == "watch_logs", let lines = message["lines"] as? [String] {
            for line in lines {
                LogManager.shared.log(line)
            }
        }
    }

    // Background transfers from Watch
    func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
        Task { @MainActor in
            let appState = UIApplication.shared.applicationState
            let stateStr = appState == .active ? "active" : appState == .background ? "background" : "inactive"
            guard let type = userInfo["type"] as? String else { return }

            switch type {
            case "health_data":
                if let payloads = userInfo["payloads"] as? [[String: Any]] {
                    HealthKitManager.bgLog("📱 WC-USERINFO: didReceiveUserInfo with \(payloads.count) health payloads (appState=\(stateStr))")
                    self.processWatchHealthData(payloads)
                }
            case "watch_logs":
                if let lines = userInfo["lines"] as? [String] {
                    for line in lines {
                        LogManager.shared.log(line)
                    }
                }
            default:
                HealthKitManager.bgLog("📱 WC-USERINFO: received userInfo (type=\(type), appState=\(stateStr))")
            }
        }
    }
}
