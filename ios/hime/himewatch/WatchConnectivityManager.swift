//
//  WatchConnectivityManager.swift
//  himeWatch Watch App
//

import WatchConnectivity
import Combine
import Foundation
import os
import WidgetKit

// MARK: - Widget snapshot (App Group bridge to HimeWatchWidgets extension)
//
// JSON-compatible with the `HimeWatchSnapshot` struct duplicated inside
// the HimeWatchWidgets target.

struct HimeWatchWidgetSnapshot: Codable, Equatable {
    var catStateRaw: String
    var catMessage: String
    var heartRate: Double?
    var steps: Double?
}

enum HimeWatchWidgetStore {
    static let appGroup: String = {
        guard let id = Bundle.main.bundleIdentifier,
              let range = id.range(of: ".hime", options: .backwards) else { return "" }
        return "group.\(id[id.startIndex..<range.upperBound]).watch"
    }()
    static let fileName = "watch_widget_snapshot.json"

    static var fileURL: URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: appGroup)?
            .appendingPathComponent(fileName)
    }

    static func read() -> HimeWatchWidgetSnapshot {
        guard let url = fileURL,
              let data = try? Data(contentsOf: url),
              let snap = try? JSONDecoder().decode(HimeWatchWidgetSnapshot.self, from: data) else {
            return HimeWatchWidgetSnapshot(catStateRaw: "relaxed", catMessage: "")
        }
        return snap
    }

    /// Minimum spacing between budget-spending complication reloads.
    private static let minReloadInterval: TimeInterval = 15 * 60
    private static var lastReload: Date = .distantPast

    static func write(_ snap: HimeWatchWidgetSnapshot, reload: Bool) {
        guard let url = fileURL else { return }
        guard let data = try? JSONEncoder().encode(snap) else { return }
        try? data.write(to: url, options: .atomic)
        guard reload else { return }
        lastReload = Date()
        WidgetCenter.shared.reloadAllTimelines()
    }

    static func update(_ mutate: (inout HimeWatchWidgetSnapshot) -> Void) {
        var s = read()
        let before = s
        mutate(&s)
        // watchOS caps how often complications may refresh. Reloading on every
        // observer tick (even when nothing changed) exhausts that budget and
        // the watch face stops updating entirely — only write on real changes.
        guard s != before else { return }

        // Heart rate and step count drift on virtually every observer tick, so
        // the equality check above doesn't throttle them at all. Spend the
        // reload budget on what the face actually shows changing — the cat
        // state/message — and let numeric drift ride the next scheduled
        // timeline refresh unless it's been quiet for a while. The file is
        // always written, so whenever the widget does refresh it reads fresh
        // values.
        let stateChanged = s.catStateRaw != before.catStateRaw || s.catMessage != before.catMessage
        let staleEnough = Date().timeIntervalSince(lastReload) >= minReloadInterval
        write(s, reload: stateChanged || staleEnough)
    }
}

private let wcLog = Logger(subsystem: "com.hime.watch", category: "WCSync")

/// Log to both os.Logger and buffer for iPhone forwarding.
func watchSyncLog(_ msg: String) {
    wcLog.info("\(msg)")
    Task { @MainActor in
        WatchConnectivityManager.shared.bufferLog(msg)
    }
}

@MainActor
class WatchConnectivityManager: NSObject, ObservableObject {
    static let shared = WatchConnectivityManager()

    @Published var isPhoneReachable: Bool = false
    @Published var catState: String = "relaxed"
    @Published var catMessage: String = ""
    @Published var lastNotification: String = ""

    private var pendingBatches: [[[String: Any]]] = []
    private let maxPendingBatches = 50

    // MARK: - Server URL for direct HTTP upload

    /// The server ingest URL received from iPhone (e.g. "https://watch.example.com/ingest")
    @Published var serverIngestURL: String? = UserDefaults.standard.string(forKey: "serverIngestURL")

    /// Accumulated log lines to be forwarded to iPhone on next health data send.
    private var logBuffer: [String] = []
    private let maxLogBuffer = 200

    private override init() {
        super.init()
        if WCSession.isSupported() {
            let session = WCSession.default
            session.delegate = self
            session.activate()
        }
    }

    // MARK: - Log forwarding to iPhone

    /// Buffer a log line for forwarding to iPhone. Called from watchLog wrapper.
    func bufferLog(_ message: String) {
        let ts = Date().formatted(date: .omitted, time: .standard)
        logBuffer.append("[\(ts)] \(message)")
        if logBuffer.count > maxLogBuffer {
            logBuffer.removeFirst()
        }
    }

    /// Timestamp of the last log transfer, for coalescing (see `flushLogs`).
    private var lastLogFlush: Date = .distantPast
    private let logFlushInterval: TimeInterval = 300

    /// Flush buffered logs to iPhone via transferUserInfo (best-effort, won't block health data).
    ///
    /// Coalesced: this is called after *every* observer tick, including the very
    /// common "0 new samples" case, and watchOS budgets `transferUserInfo`.
    /// Firing one transfer per tick just to ship debug lines backs the queue up
    /// and delays the health-data transfers that share it.
    nonisolated func flushLogs() {
        Task { @MainActor in
            guard !self.logBuffer.isEmpty else { return }
            let now = Date()
            guard now.timeIntervalSince(self.lastLogFlush) >= self.logFlushInterval else { return }
            // Check activation before draining — the previous order dropped the
            // buffer on the floor whenever the session wasn't up yet.
            guard WCSession.default.activationState == .activated else { return }

            let lines = self.logBuffer
            self.logBuffer.removeAll()
            self.lastLogFlush = now
            let message: [String: Any] = ["type": "watch_logs", "lines": lines]
            WCSession.default.transferUserInfo(message)
        }
    }

    // MARK: - Send health data to iPhone via WatchConnectivity

    nonisolated func sendHealthData(_ payloads: [[String: Any]]) async {
        guard WCSession.default.activationState == .activated else {
            watchSyncLog("⌚ WC-SEND: session not activated, queuing \(payloads.count) samples to pendingBatches")
            await MainActor.run {
                self.pendingBatches.append(payloads)
                if self.pendingBatches.count > self.maxPendingBatches {
                    self.pendingBatches.removeFirst()
                }
            }
            return
        }

        let message: [String: Any] = ["type": "health_data", "payloads": payloads, "source": "watch"]

        if WCSession.default.isReachable {
            watchSyncLog("⌚ WC-SEND: phone reachable, sendMessage \(payloads.count) samples")
            WCSession.default.sendMessage(
                message,
                replyHandler: { (_: [String: Any]) in
                    watchSyncLog("⌚ WC-SEND: sendMessage succeeded (\(payloads.count) samples)")
                },
                errorHandler: { (error: Error) in
                    watchSyncLog("⌚ WC-SEND: sendMessage failed (\(error.localizedDescription)), falling back to transferUserInfo")
                    WCSession.default.transferUserInfo(message)
                }
            )
        } else {
            watchSyncLog("⌚ WC-SEND: phone NOT reachable, using transferUserInfo for \(payloads.count) samples")
            WCSession.default.transferUserInfo(message)
        }
    }

    nonisolated func flushPending() async {
        let batches = await MainActor.run { () -> [[[String: Any]]] in
            let b = self.pendingBatches
            self.pendingBatches.removeAll()
            return b
        }
        for batch in batches {
            await sendHealthData(batch)
        }
    }

    // MARK: - Direct HTTP upload to server (bypasses iPhone)

    /// Send health data directly to the server via HTTP POST.
    /// This works even when the iPhone is not reachable or the iPhone app is suspended.
    nonisolated func sendHealthDataHTTP(_ payloads: [[String: Any]]) async {
        guard let urlString = await MainActor.run(body: { self.serverIngestURL }),
              let url = URL(string: urlString) else {
            watchSyncLog("⌚ HTTP-SEND: no serverIngestURL configured, skipping direct upload of \(payloads.count) samples")
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("watch-background", forHTTPHeaderField: "X-Sync-Mode")
        request.timeoutInterval = 30

        let wireData = payloads.map { p -> [String: Any] in
            ["ts": p["ts"] as Any, "f": p["f"] as Any, "v": p["v"] as Any]
        }
        guard let data = try? JSONSerialization.data(withJSONObject: wireData) else { return }
        request.httpBody = data

        watchSyncLog("⌚ HTTP-SEND: POSTing \(payloads.count) samples to \(urlString)")
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse {
                if http.statusCode == 200 {
                    watchSyncLog("⌚ HTTP-SEND: success (\(payloads.count) samples, status 200)")
                } else {
                    watchSyncLog("⌚ HTTP-SEND: server returned status \(http.statusCode)")
                }
            }
        } catch {
            watchSyncLog("⌚ HTTP-SEND: failed — \(error.localizedDescription)")
        }
    }
}

extension WatchConnectivityManager: WCSessionDelegate {
    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        watchSyncLog("⌚ WC-SESSION: activated (state=\(activationState.rawValue), reachable=\(session.isReachable))")
        Task { @MainActor in
            self.isPhoneReachable = session.isReachable
            let ctx = session.receivedApplicationContext
            if !ctx.isEmpty {
                self.handleIncoming(ctx)
            }
            await self.flushPending()
        }
    }

    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        watchSyncLog("⌚ WC-SESSION: reachability changed → \(session.isReachable)")
        Task { @MainActor in
            self.isPhoneReachable = session.isReachable
            if session.isReachable {
                watchSyncLog("⌚ WC-SESSION: phone reachable, flushing pending batches")
                await self.flushPending()
            }
        }
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        Task { @MainActor in
            self.handleIncoming(message)
        }
    }

    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
        Task { @MainActor in
            self.handleIncoming(userInfo)
        }
    }

    nonisolated func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        Task { @MainActor in
            self.handleIncoming(applicationContext)
        }
    }

    @MainActor
    private func handleIncoming(_ data: [String: Any]) {
        guard let type = data["type"] as? String else { return }
        switch type {
        case "composite":
            // Merged application context from the iPhone — carries whichever
            // of cat state / server config is currently known. Each key is
            // applied independently so a cat-state update can never wipe the
            // ingest URL (and vice versa).
            var catChanged = false
            if let state = data["cat_state"] as? String, state != self.catState {
                self.catState = state
                catChanged = true
            }
            if let message = data["cat_message"] as? String, message != self.catMessage {
                self.catMessage = message
                catChanged = true
            }
            if let ingestURL = data["ingest_url"] as? String, ingestURL != self.serverIngestURL {
                self.serverIngestURL = ingestURL
                UserDefaults.standard.set(ingestURL, forKey: "serverIngestURL")
            }
            if catChanged { self.publishWatchSnapshot() }
        case "cat_state":
            self.catState = data["state"] as? String ?? "relaxed"
            self.catMessage = data["message"] as? String ?? ""
            self.publishWatchSnapshot()
        case "notification":
            self.lastNotification = data["text"] as? String ?? ""
        case "server_config":
            if let ingestURL = data["ingest_url"] as? String {
                self.serverIngestURL = ingestURL
                UserDefaults.standard.set(ingestURL, forKey: "serverIngestURL")
            }
        default:
            break
        }
    }

    /// Persist the latest cat state (and any cached HR/steps) into the
    /// watch App Group container so the complications can read it.
    @MainActor
    func publishWatchSnapshot(heartRate: Double? = nil, steps: Double? = nil) {
        let state = self.catState
        let message = self.catMessage
        HimeWatchWidgetStore.update { snap in
            snap.catStateRaw = state
            snap.catMessage = message
            if let hr = heartRate { snap.heartRate = hr }
            if let s = steps { snap.steps = s }
        }
    }
}
