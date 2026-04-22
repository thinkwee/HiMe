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
        } else {
            try? WCSession.default.updateApplicationContext(data)
        }
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
            try? WCSession.default.updateApplicationContext(data)
        }
    }

    /// Push server config to Watch so it can upload directly
    @MainActor
    func syncServerConfigToWatch() {
        guard WCSession.default.activationState == .activated else { return }
        let config = WebSocketClient.shared.serverConfig
        let ingestURL = config.watchHTTPBaseURL + "/ingest"
        let data: [String: Any] = [
            "type": "server_config",
            "ingest_url": ingestURL
        ]
        try? WCSession.default.updateApplicationContext(data)
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

        let (appState, taskID): (String, UIBackgroundTaskIdentifier) = DispatchQueue.main.sync {
            let s = UIApplication.shared.applicationState
            let state = (s == .active) ? "foreground" : "background"
            let tid = UIApplication.shared.beginBackgroundTask(withName: "WatchDataFlush") { }
            return (state, tid)
        }

        HealthKitManager.bgLog("📱 WC-RECV: \(healthPayloads.count) samples from Watch (appState=\(appState), pending=\(PendingStore.shared.count))")

        Task {
            await WebSocketClient.shared.flushPendingAndWait(appState: appState)
            HealthKitManager.bgLog("📱 WC-RECV: Flush completed (remaining=\(PendingStore.shared.count))")
            await MainActor.run { UIApplication.shared.endBackgroundTask(taskID) }
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
