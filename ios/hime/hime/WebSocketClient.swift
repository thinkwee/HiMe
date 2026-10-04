@preconcurrency import Dispatch
import Foundation
import Combine
import UIKit

// MARK: - WebSocket & HTTP Session Delegate

private final class SessionDelegate: NSObject, URLSessionWebSocketDelegate, URLSessionTaskDelegate, URLSessionDelegate, @unchecked Sendable {
    weak var client: WebSocketClient?

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        guard let identifier = session.configuration.identifier else { return }
        Task { @MainActor in
            client?.executeBackgroundCompletionHandler(for: identifier)
        }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        Task { @MainActor in
            await client?.onWSOpened()
        }
    }

    /// Fired when URLSession has a task that cannot proceed because iOS
    /// considers the target unreachable. Seeing this in the log narrows
    /// the diagnosis to network-path / Local-Network-permission issues
    /// rather than server or payload problems.
    func urlSession(_ session: URLSession, taskIsWaitingForConnectivity task: URLSessionTask) {
        let url = task.currentRequest?.url?.absoluteString ?? "?"
        Task { @MainActor in
            HealthKitManager.bgLog("HTTP: Task waiting for connectivity — \(url)")
        }
    }

    /// `taskIdentifier` is only unique WITHIN one URLSession, and this delegate
    /// serves both fgSession and bgSession — they share an ID space. Namespace
    /// the key by session so the two can never collide in `pendingHTTPTasks`.
    static func taskKey(session: URLSession, task: URLSessionTask) -> String {
        "\(session.configuration.identifier ?? "fg")#\(task.taskIdentifier)"
    }

    /// WebSocket/HTTP failure
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let isWS = task is URLSessionWebSocketTask
        let fileURLString = task.taskDescription
        let key = SessionDelegate.taskKey(session: session, task: task)
        // A nil transport error does NOT imply HTTP 2xx. Treating 4xx/5xx as
        // success would pop the batch out of PendingStore and destroy health
        // data the server never accepted (401 bad token, 403 sync disabled,
        // 400 malformed body). Route non-2xx through the failure path so the
        // records are retained and retried with backoff.
        let statusCode = (task.response as? HTTPURLResponse)?.statusCode

        Task { @MainActor in
            if let error = error {
                if isWS {
                    client?.onWSTaskFailed(task: task, error: error)
                } else {
                    client?.onHTTPTaskFailed(key: key, error: error)
                }
            } else if !isWS {
                if let code = statusCode, (200...299).contains(code) {
                    client?.onHTTPTaskSucceeded(key: key)
                } else {
                    let code = statusCode ?? -1
                    let httpError = NSError(
                        domain: "hime.http", code: code,
                        userInfo: [NSLocalizedDescriptionKey: "HTTP status \(code)"]
                    )
                    client?.onHTTPTaskFailed(key: key, error: httpError)
                }
            }

            // Cleanup temp file if this was an upload task
            if let desc = fileURLString, let fileURL = URL(string: desc) {
                try? FileManager.default.removeItem(at: fileURL)
            }
        }
    }
}

/// Guards a CheckedContinuation so racing callers (send completion vs. timeout
/// fallback) can't double-resume and won't leak the continuation if neither
/// path ever fires. Used by _sendWS. Touched only from @MainActor.
@MainActor
private final class ContinuationGuard {
    private var continuation: CheckedContinuation<Bool, Never>?
    init(_ c: CheckedContinuation<Bool, Never>) { continuation = c }
    /// `ok` is whether the frame was handed to the socket successfully.
    @discardableResult
    func resume(_ ok: Bool) -> Bool {
        guard let c = continuation else { return false }
        continuation = nil
        c.resume(returning: ok)
        return true
    }
}

/// Server reply to one WS data frame (see `server.py` — hello / ack / nack).
private enum WSAckResult {
    case acked
    case nacked(String)
    /// Timeout or the socket went away before a reply arrived.
    case failed
}

/// One in-flight WS batch waiting for the server's ack. Same once-only
/// discipline as ContinuationGuard: reply, timeout and teardown race.
@MainActor
private final class AckWaiter {
    let id: String
    private var continuation: CheckedContinuation<WSAckResult, Never>?
    init(id: String, continuation: CheckedContinuation<WSAckResult, Never>) {
        self.id = id
        self.continuation = continuation
    }
    @discardableResult
    func resolve(_ result: WSAckResult) -> Bool {
        guard let c = continuation else { return false }
        continuation = nil
        c.resume(returning: result)
        return true
    }
}

@MainActor
final class WebSocketClient: ObservableObject {
    static let shared = WebSocketClient()

    @Published private(set) var isConnected = false
    @Published var serverConfig: ServerConfig = ServerConfig.load() {
        didSet { serverConfig.save() }
    }

    @Published var isSyncActive: Bool = !UserDefaults.standard.bool(forKey: "userRequestedDisconnect") {
        didSet {
            UserDefaults.standard.set(!isSyncActive, forKey: "userRequestedDisconnect")
        }
    }

    /// Global sync toggle based on whether the user has requested a connection.
    /// If false, no data will be sent via WS or HTTP.
    var isSyncEnabled: Bool {
        isSyncActive
    }

    /// WebSocket URL for data sync (watch exporter).
    var serverURL: String { serverConfig.watchURL }

    /// HTTP base URL for watch exporter uploads.
    var httpBaseURL: String { serverConfig.watchHTTPBaseURL }

    // MARK: - Private state

    private var wsTask: URLSessionWebSocketTask?
    private var shouldReconnect = false
    private var reconnectDelay: TimeInterval = 1.0
    private let maxReconnectDelay: TimeInterval = 60.0

    // Heartbeat / dead-connection detection
    private var heartbeatTimer: DispatchSourceTimer?
    private let heartbeatInterval: TimeInterval = 30.0
    private let heartbeatTimeout: TimeInterval = 10.0
    
    private let chunkSize = 500 // Max records per batch to avoid timeouts
    private var _isFlushing = false // Guard against concurrent flush calls

    // WS application-level ack protocol (capability-negotiated per connection).
    // A new server sends {"type":"hello","ack":1} right after the upgrade; from
    // then on every data frame is answered with {"type":"ack"} (committed) or
    // {"type":"nack"} (e.g. sync disabled) and the batch is popped from
    // PendingStore ONLY on ack. Without a hello (old server) we keep the legacy
    // behaviour of popping once the frame is handed to the socket.
    private var wsServerAcks = false
    /// True once the hello arrived or the grace period after connect elapsed;
    /// WS drains wait for it so the first batch isn't sent un-acked by mistake.
    private var wsHelloSettled = false
    private var wsAckWaiter: AckWaiter?
    private let wsAckTimeout: TimeInterval = 15.0
    /// After a nack the WS drain pauses (data stays queued) and retries later.
    private var wsPausedUntil: Date = .distantPast
    private var wsResumeTask: Task<Void, Never>?
    private let wsNackPause: TimeInterval = 60.0

    // HTTP poison-batch protection: after this many consecutive non-retryable
    // 4xx answers the head batch is moved to a quarantine file so it can't
    // block the queue forever.
    private var httpConsecutiveClientErrors = 0
    private let maxHttpClientErrors = 5

    // HTTP retry backoff: if an upload fails the queue has no natural pump,
    // so we must re-kick _flush ourselves. Exponential, resets on any success.
    private var httpRetryDelay: TimeInterval = 2.0
    private let maxHttpRetryDelay: TimeInterval = 60.0

    private let queue = DispatchQueue(label: "hime.websocket", qos: .utility)
    private let sessionDelegate = SessionDelegate()

    // Map of "<session-id>#<taskIdentifier>" -> Number of payloads sent.
    // Persisted to UserDefaults so that background URLSession delegate callbacks
    // can correctly pop PendingStore even after app suspension/termination.
    private static let kPendingHTTPKey = "hime.pendingHTTPTasks"
    private var pendingHTTPTasks: [String: Int] = [:] {
        didSet { _persistPendingHTTPTasks() }
    }

    private lazy var fgSession: URLSession = {
        let cfg = URLSessionConfiguration.default
        // waitsForConnectivity = false: if iOS considers the LAN target
        // unreachable, fail the task fast rather than parking it in an
        // opaque "waiting" state — the reconnect backoff handles recovery.
        cfg.waitsForConnectivity = false
        cfg.timeoutIntervalForRequest = 30
        // Leave timeoutIntervalForResource at the default (7 days). This
        // session hosts the long-lived WebSocket task alongside short
        // HTTP tasks; a short resource timeout here would kill the WS
        // periodically and force a full reconnect — 500 records/minute
        // instead of 500 records/100ms.
        sessionDelegate.client = self
        return URLSession(configuration: cfg, delegate: sessionDelegate, delegateQueue: .main)
    }()

    private lazy var bgSession: URLSession = {
        let cfg = URLSessionConfiguration.background(withIdentifier: "com.hime.healthkit.upload")
        cfg.isDiscretionary = false
        cfg.sessionSendsLaunchEvents = true
        sessionDelegate.client = self
        return URLSession(configuration: cfg, delegate: sessionDelegate, delegateQueue: .main)
    }()

    private init() {
        // Clear persisted in-flight HTTP task tracking. Only bgSession tasks
        // can meaningfully survive an app launch (URLSessionConfiguration.
        // background reattaches them), but our per-task count map doesn't
        // round-trip reliably — cleared entries just mean one batch might
        // be re-sent on the next flush, which the server upserts
        // idempotently. Preferable to leaking stale entries that would
        // block the background-HTTP dedup guard.
        UserDefaults.standard.removeObject(forKey: Self.kPendingHTTPKey)
    }

    private func _persistPendingHTTPTasks() {
        UserDefaults.standard.set(pendingHTTPTasks, forKey: Self.kPendingHTTPKey)
    }

    // MARK: - Public API

    /// Open (or re-open) the WebSocket.
    ///
    /// `userInitiated: true` (the default — Settings "Connect") clears the
    /// persisted "user disconnected" flag. Automatic paths (app launch,
    /// onboarding, server-settings changes) pass `false` so a Disconnect the
    /// user chose survives relaunches instead of being silently undone.
    func connect(userInitiated: Bool = true) {
        if userInitiated {
            self.isSyncActive = true
        } else if !isSyncActive {
            return
        }
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                // Explicitly kill any zombie task waiting for old IP connectivity
                self.wsTask?.cancel(with: .normalClosure, reason: nil)
                self.wsTask = nil
                self._resetWSProtocolState()
                self.isConnected = false
                self._openWS()
            }
        }
    }

    /// The server address or auth token changed (Settings). The live socket
    /// still points at the old address / carries the old token, and the watch
    /// and APNs registration hold stale copies — refresh all three.
    func serverSettingsDidChange() {
        connect(userInitiated: false)
        PhoneConnectivityManager.shared.syncServerConfigToWatch()
        DeviceTokenUploader.shared.uploadIfNeeded()
    }

    func reconnectIfNeeded() {
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                guard self.isSyncActive else { return }
                self._openWS()
            }
        }
    }

    func disconnect(userInitiated: Bool = true) {
        if userInitiated {
            self.isSyncActive = false
        }
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                self.shouldReconnect = false
                self._stopHeartbeat()
                self.wsTask?.cancel(with: .normalClosure, reason: nil)
                self.wsTask = nil
                self._resetWSProtocolState()
                self.isConnected = false
                HealthKitManager.bgLog("WS: User disconnected")
            }
        }
    }

    func send(_ payload: HealthPayload) {
        PendingStore.shared.append([payload])
        if isSyncEnabled {
            flushPending()
        }
    }

    func flushPending(appState: String = "foreground") {
        Task {
            guard self.isSyncEnabled else { return }
            await self._flush(appState: appState)
        }
    }

    func flushPendingAndWait(appState: String = "foreground") async {
        guard self.isSyncEnabled else { return }
        await self._flush(appState: appState)
    }

    // MARK: - Transports

    private func _flush(appState: String = "foreground") async {
        // Every drain chain (WS loop, HTTP success/failure re-kicks,
        // onWSOpened) funnels through here, so the sync gate lives here: a
        // user "Disconnect" must stop uploads on all of them.
        guard isSyncEnabled else { return }
        guard !_isFlushing else { return }
        _isFlushing = true
        defer { _isFlushing = false }

        // Transport policy. `appState` is the real UIApplication state of the
        // caller: "active" / "foreground", "inactive", or "background".
        //
        //   Active → WS exclusively.
        //     If WS isn't up yet, return. PendingStore is file-backed and
        //     loss-proof; samples sit there until onWSOpened re-kicks
        //     _flush and drains them over WS. HTTP was a fallback in the
        //     prior design, but that caused first-launch observer bursts
        //     to race the WS handshake and get stranded on URLSession
        //     upload tasks that never completed (iOS "waiting for
        //     connectivity"). WS-only in foreground eliminates the race.
        //
        //   Not active → WS if it happens to be connected (burst mode keeps
        //     it alive), otherwise the HTTP background session. WebSocket
        //     tasks don't survive app suspension, so bgSession
        //     (URLSessionConfiguration.background) carries the drain. An
        //     explicit "background" always uses HTTP.
        //
        // This function drains in a loop rather than via recursive calls.
        // Previously _sendWS's success callback called _flush() again, but
        // at that point the outer _flush was still awaiting the continuation
        // and _isFlushing was still true — the recursive call tripped the
        // guard and did nothing. That turned every successful WS send into
        // a dead end; the pipeline only limped forward when an external
        // event (observer fire, WS reconnect) happened to call _flush with
        // _isFlushing = false.
        let isActive = (appState == "foreground" || appState == "active")
        while true {
            // Callers wrap this in a UIBackgroundTask whose expiration handler
            // cancels the Task; honour that so we release the background
            // assertion instead of being watchdog-killed mid-drain.
            guard !Task.isCancelled else {
                HealthKitManager.bgLog("📤 FLUSH: cancelled — \(PendingStore.shared.count) records kept")
                return
            }
            guard isSyncEnabled else { return }
            let storeCount = PendingStore.shared.count
            guard storeCount > 0 else { return }

            if appState != "background" && isConnected && wsHelloSettled {
                // An HTTP upload started while backgrounded can still be in
                // flight when the user foregrounds the app. Sending the same
                // top-N over WS would pop it, and the HTTP completion would
                // then pop a SECOND batch that was never transmitted. Wait for
                // the outstanding task — its delegate re-kicks _flush.
                guard pendingHTTPTasks.isEmpty else {
                    HealthKitManager.bgLog("📤 FLUSH: deferred — \(pendingHTTPTasks.count) HTTP upload(s) still in flight")
                    return
                }
                guard Date() >= wsPausedUntil else {
                    HealthKitManager.bgLog("📤 FLUSH: paused — server refused data, \(storeCount) records kept")
                    return
                }
                let payloads = PendingStore.shared.peek(limit: chunkSize)
                guard !payloads.isEmpty else { return }
                HealthKitManager.bgLog("📤 FLUSH: \(payloads.count)/\(storeCount) records via WS (appState=\(appState))")
                switch await _sendWS(payloads) {
                case .sent:
                    // Next iteration peeks the next chunk itself.
                    continue
                case .paused:
                    _scheduleWSResume()
                    return
                case .failed:
                    // isConnected is now false (or the batch is retained for
                    // the next attempt); PendingStore keeps the unsent top-N.
                    return
                }
            }

            if isActive {
                HealthKitManager.bgLog("📤 FLUSH: deferred — WS not ready, \(storeCount) queued (will drain on onWSOpened)")
                return
            }

            // HTTP path (background / inactive with no live WS). De-dup
            // in-flight tasks so bg refresh events don't fire duplicates for
            // the same top-N while an upload is still outstanding; the
            // delegate re-kicks _flush when it completes.
            if !pendingHTTPTasks.isEmpty { return }
            let payloads = PendingStore.shared.peek(limit: chunkSize)
            guard !payloads.isEmpty else { return }
            HealthKitManager.bgLog("📤 FLUSH: \(payloads.count)/\(storeCount) records via HTTP (appState=\(appState))")
            _sendHTTP(payloads, appState: appState)
            // HTTP is fire-and-forget here; the session delegate re-kicks
            // _flush when the bgSession task resolves.
            return
        }
    }

    private enum WSSendOutcome {
        case sent       // batch confirmed (or, on an old server, handed to the socket) and popped
        case paused     // server explicitly refused (nack): data kept, drain paused
        case failed     // transport failure / timeout: data kept
    }

    private func _sendWS(_ payloads: [HealthPayload]) async -> WSSendOutcome {
        let wireItems = payloads.map { ["ts": $0.ts, "f": $0.f, "v": $0.v] }
        // Only an ack-capable server understands the {"id","items"} wrapper; an
        // old server would parse it as one flattened payload and store bogus
        // "id"/"items" rows. So the wrapper is sent strictly after a hello.
        let useAck = wsServerAcks
        let batchID = UUID().uuidString
        let body: Any
        if useAck {
            body = ["id": batchID, "items": wireItems] as [String: Any]
        } else {
            body = wireItems
        }
        guard let data = try? JSONSerialization.data(withJSONObject: body) else {
            HealthKitManager.bgLog("WS: payload serialization failed — batch kept")
            return .failed
        }

        let sent: Bool = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            guard let task = wsTask else {
                // isConnected can be true while wsTask is nil (e.g. connect()
                // bailed on an invalid URL). Without clearing it here, _flush's
                // `while true` loop would spin forever on the MainActor —
                // nothing is popped and the isConnected guard never trips.
                isConnected = false
                continuation.resume(returning: false)
                return
            }

            let guardBox = ContinuationGuard(continuation)

            // If task.send's completion never fires (e.g. WS cancelled in a
            // weird state), this prevents _flush from hanging forever with
            // _isFlushing stuck true, which would freeze the entire queue.
            let timeoutTask = Task { @MainActor in
                try? await Task.sleep(nanoseconds: 60 * 1_000_000_000)
                guard !Task.isCancelled else { return }
                if guardBox.resume(false) {
                    HealthKitManager.bgLog("WS: Send timeout — forcing resume")
                    self.isConnected = false
                    self.wsTask?.cancel(with: .abnormalClosure, reason: nil)
                    self.wsTask = nil
                    self._resetWSProtocolState()
                    if self.shouldReconnect { self._scheduleReconnect() }
                }
            }

            task.send(.data(data)) { [weak self] error in
                guard let self else {
                    Task { @MainActor in
                        timeoutTask.cancel()
                        guardBox.resume(false)
                    }
                    return
                }
                Task { @MainActor in
                    timeoutTask.cancel()
                    if let error = error {
                        HealthKitManager.bgLog("WS: Send ERR — \(error.localizedDescription)")
                        self.isConnected = false
                        guardBox.resume(false)
                    } else {
                        guardBox.resume(true)
                    }
                }
            }
        }
        guard sent else { return .failed }

        guard useAck else {
            // Legacy server: no confirmation available — pop on successful send.
            _commitWSBatch(payloads.count)
            return .sent
        }

        switch await _awaitAck(id: batchID) {
        case .acked:
            _commitWSBatch(payloads.count)
            return .sent
        case .nacked(let reason):
            HealthKitManager.bgLog("WS: server NACK (\(reason)) — \(payloads.count) records kept, pausing \(Int(wsNackPause))s")
            wsPausedUntil = Date().addingTimeInterval(wsNackPause)
            return .paused
        case .failed:
            HealthKitManager.bgLog("WS: no ack within \(Int(wsAckTimeout))s — \(payloads.count) records kept, reconnecting")
            if let ws = wsTask {
                _stopHeartbeat()
                ws.cancel(with: .abnormalClosure, reason: nil)
                wsTask = nil
                isConnected = false
                _resetWSProtocolState()
                if shouldReconnect { _scheduleReconnect() }
            }
            return .failed
        }
    }

    private func _commitWSBatch(_ count: Int) {
        PendingStore.shared.pop(count: count)
        HealthKitManager.shared.markOldestAsSynced(count: count)
        HealthKitManager.bgLog("WS: Sent \(count) records")
    }

    /// Wait for the server's reply to the batch `id` (or time out).
    private func _awaitAck(id: String) async -> WSAckResult {
        await withCheckedContinuation { (continuation: CheckedContinuation<WSAckResult, Never>) in
            let waiter = AckWaiter(id: id, continuation: continuation)
            wsAckWaiter = waiter
            let timeout = wsAckTimeout
            Task { @MainActor [weak self, weak waiter] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                guard let waiter, waiter.resolve(.failed) else { return }
                if self?.wsAckWaiter === waiter { self?.wsAckWaiter = nil }
            }
        }
    }

    /// Route a decoded server frame (hello / ack / nack) from the receive loop.
    private func _handleWSMessage(_ message: URLSessionWebSocketTask.Message, from ws: URLSessionWebSocketTask) {
        guard wsTask === ws else { return }
        let raw: Data?
        switch message {
        case .string(let text): raw = text.data(using: .utf8)
        case .data(let d):      raw = d
        @unknown default:       raw = nil
        }
        guard let raw,
              let obj = try? JSONSerialization.jsonObject(with: raw) as? [String: Any],
              let type = obj["type"] as? String else { return }

        switch type {
        case "hello":
            if let level = obj["ack"] as? Int, level >= 1 { wsServerAcks = true }
            wsHelloSettled = true
            HealthKitManager.bgLog("WS: server hello (ack=\(wsServerAcks))")
        case "ack":
            _deliverAck(.acked, id: obj["id"] as? String)
        case "nack":
            _deliverAck(.nacked(obj["reason"] as? String ?? "unknown"), id: obj["id"] as? String)
        default:
            break
        }
    }

    private func _deliverAck(_ result: WSAckResult, id: String?) {
        guard let waiter = wsAckWaiter else { return }
        // A reply echoing a different id belongs to an earlier, abandoned
        // batch — ignore it. A reply without an id is matched in order.
        if let id, id != waiter.id { return }
        wsAckWaiter = nil
        waiter.resolve(result)
    }

    /// Forget everything negotiated with the previous socket and fail any
    /// batch still waiting for its ack (its data stays in PendingStore).
    private func _resetWSProtocolState() {
        wsServerAcks = false
        wsHelloSettled = false
        if let waiter = wsAckWaiter {
            wsAckWaiter = nil
            waiter.resolve(.failed)
        }
    }

    /// Re-kick the drain once the nack pause has elapsed.
    private func _scheduleWSResume() {
        wsResumeTask?.cancel()
        let delay = max(1.0, wsPausedUntil.timeIntervalSinceNow)
        wsResumeTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            await self._flush()
        }
    }

    private func _sendHTTP(_ payloads: [HealthPayload], appState: String) {
        guard let url = URL(string: httpBaseURL + "/ingest") else {
            HealthKitManager.bgLog("HTTP: Invalid ingest URL from httpBaseURL: \(httpBaseURL)")
            return
        }
        var request = APIClient.request(url, method: "POST")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(appState, forHTTPHeaderField: "X-Sync-Mode")

        let wireData = payloads.map { ["ts": $0.ts, "f": $0.f, "v": $0.v] }
        guard let data = try? JSONSerialization.data(withJSONObject: wireData) else { return }

        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".json")
        try? data.write(to: tmp)

        let useFG = (appState == "foreground" || appState == "active")
        let session = useFG ? fgSession : bgSession
        let task = session.uploadTask(with: request, fromFile: tmp)
        task.taskDescription = tmp.absoluteString
        pendingHTTPTasks[SessionDelegate.taskKey(session: session, task: task)] = payloads.count
        task.resume()
        HealthKitManager.bgLog("HTTP: Resume \(appState) upload (\(payloads.count)) — Backlog: \(PendingStore.shared.count)")
    }

    // MARK: - Delegate Callbacks

    func onWSTaskFailed(task: URLSessionTask, error: Error) {
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                guard self.wsTask?.taskIdentifier == task.taskIdentifier else { return }
                self._stopHeartbeat()
                self.wsTask = nil
                self._resetWSProtocolState()
                self.isConnected = false
                HealthKitManager.bgLog("WS: Error — \(error.localizedDescription)")
                if self.shouldReconnect { self._scheduleReconnect() }
            }
        }
    }

    func onWSOpened() async {
        self.isConnected = true
        self.reconnectDelay = 1.0
        HealthKitManager.bgLog("WS: Connected")
        guard let ws = wsTask else { return }
        _startHeartbeat(ws)
        // A new server announces ack support in its first frame. Give it a
        // short grace period before the first drain so the batch isn't sent in
        // legacy (un-acked) mode on a server that would have acknowledged it.
        // An old server never says hello; we just proceed after the grace.
        if !wsHelloSettled {
            for _ in 0..<20 {
                if wsHelloSettled || wsTask !== ws { break }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
        guard wsTask === ws else { return }
        wsHelloSettled = true
        await _flush()
    }

    func onHTTPTaskSucceeded(key: String) {
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                guard let count = self.pendingHTTPTasks.removeValue(forKey: key) else { return }
                PendingStore.shared.pop(count: count)
                HealthKitManager.shared.markOldestAsSynced(count: count)
                HealthKitManager.bgLog("HTTP: Success (\(count) records)")
                self.httpRetryDelay = 2.0
                self.httpConsecutiveClientErrors = 0
                // HTTP tasks only fire from bgSession under the current
                // transport policy, so keep the drain on the HTTP path.
                // If this dispatched to foreground, the WS-only rule would
                // stall the chain during bg wake when WS is not alive.
                if PendingStore.shared.count > 0 { await self._flush(appState: "background") }
            }
        }
    }

    func onHTTPTaskFailed(key: String, error: Error) {
        queue.async { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                // Remove from pending tracking but do NOT pop from PendingStore.
                // The data remains in PendingStore so the next flush cycle retries it.
                if let count = self.pendingHTTPTasks.removeValue(forKey: key) {
                    HealthKitManager.bgLog("HTTP: Task failed (\(count) records kept for retry) — \(error.localizedDescription)")
                    self._noteHTTPFailure(error, batchCount: count)
                } else {
                    HealthKitManager.bgLog("HTTP: Task failed — \(error.localizedDescription)")
                }
                // Symmetric to the success path: re-kick the drain, otherwise the
                // initial-install backfill freezes on the first failed chunk until
                // the user force-kills and relaunches the app. Backoff avoids
                // hammering a struggling server.
                guard PendingStore.shared.count > 0 else { return }
                let delay = self.httpRetryDelay
                self.httpRetryDelay = min(self.httpRetryDelay * 2, self.maxHttpRetryDelay)
                try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                await self._flush(appState: "background")
            }
        }
    }

    /// Track consecutive "this batch will never be accepted" answers (4xx other
    /// than auth / sync-disabled / timeout / rate-limit). After
    /// `maxHttpClientErrors` the head batch is parked in a quarantine file and
    /// popped, so one malformed batch can't block every later upload forever.
    private func _noteHTTPFailure(_ error: Error, batchCount: Int) {
        let ns = error as NSError
        let isPoisonStatus = ns.domain == "hime.http"
            && (400..<500).contains(ns.code)
            && ![401, 403, 408, 429].contains(ns.code)
        guard isPoisonStatus else {
            httpConsecutiveClientErrors = 0
            return
        }
        httpConsecutiveClientErrors += 1
        guard httpConsecutiveClientErrors >= maxHttpClientErrors else { return }
        httpConsecutiveClientErrors = 0

        let batch = PendingStore.shared.peek(limit: batchCount)
        guard !batch.isEmpty else { return }
        let fm = FileManager.default
        if let dir = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
            let file = dir.appendingPathComponent("quarantine_\(Int(Date().timeIntervalSince1970)).json")
            if let data = try? JSONEncoder().encode(batch) {
                try? data.write(to: file, options: .atomic)
            }
        }
        PendingStore.shared.pop(count: batch.count)
        HealthKitManager.bgLog("HTTP: quarantined \(batch.count) records after \(maxHttpClientErrors) consecutive rejections (HTTP \(ns.code))")
    }

    // MARK: - WS Lifecycle

    private func _openWS() {
        guard wsTask == nil else { return }

        guard var components = URLComponents(string: serverURL) else { return }
        // Ensure path ends with /ws
        let path = components.path.hasSuffix("/") ? components.path.dropLast() : components.path[...]
        if !path.hasSuffix("/ws") {
            components.path = String(path) + "/ws"
        }
        guard let url = components.url else { return }
        // Send the auth token as an Authorization header rather than a `?token=`
        // query parameter — query strings land in reverse-proxy / tunnel access
        // logs. `webSocketTask(with: URLRequest)` carries custom headers through
        // the HTTP upgrade, and the exporter's _check_auth accepts the header form.
        var wsRequest = URLRequest(url: url)
        let token = ServerConfig.authToken
        if !token.isEmpty {
            wsRequest.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        shouldReconnect = true
        _resetWSProtocolState()
        // Do NOT reset reconnectDelay here: _scheduleReconnect doubles it and
        // then calls back into _openWS, so resetting would pin the backoff at
        // 1s and reconnect once per second forever while the server is down.
        // onWSOpened resets it on a genuine connection.

        let ws = fgSession.webSocketTask(with: wsRequest)
        wsTask = ws
        ws.resume()
        
        ws.sendPing { _ in }
        _receiveLoop(ws)
    }

    private func _receiveLoop(_ ws: URLSessionWebSocketTask) {
        ws.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                Task { @MainActor in
                    switch result {
                    case .success(let message):
                        self._handleWSMessage(message, from: ws)
                        self._receiveLoop(ws)
                    case .failure:
                        guard self.wsTask === ws else { return }
                        self._stopHeartbeat()
                        self.wsTask = nil
                        self._resetWSProtocolState()
                        self.isConnected = false
                        if self.shouldReconnect { self._scheduleReconnect() }
                    }
                }
            }
        }
    }

    // MARK: - Heartbeat (dead-connection detection)

    /// Tear down `ws` if it is still the live socket and schedule a reconnect.
    /// Shared by the ping-failure and pong-timeout paths.
    private func _failHeartbeat(_ ws: URLSessionWebSocketTask, reason: String) {
        guard self.wsTask === ws else { return }
        HealthKitManager.bgLog("WS: \(reason)")
        _stopHeartbeat()
        wsTask?.cancel(with: .abnormalClosure, reason: nil)
        wsTask = nil
        _resetWSProtocolState()
        isConnected = false
        if shouldReconnect { _scheduleReconnect() }
    }

    private func _startHeartbeat(_ ws: URLSessionWebSocketTask) {
        _stopHeartbeat()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + heartbeatInterval, repeating: heartbeatInterval)
        // Capture these up front: they're MainActor-isolated state and the
        // event handler runs on `queue`.
        let workQueue = queue
        let pongTimeout = heartbeatTimeout
        timer.setEventHandler { [weak self, weak ws] in
            guard let self, let ws else { return }
            // Half-open detection: sendPing's completion may never fire when
            // the link drops silently (e.g. NAT timeout), which would leave
            // isConnected == true forever. Arm a timeout and disarm it when
            // the pong (or an error) comes back.
            let timeoutItem = DispatchWorkItem { [weak self, weak ws] in
                guard let self, let ws else { return }
                Task { @MainActor in
                    self._failHeartbeat(ws, reason: "Heartbeat pong timeout (\(Int(pongTimeout))s)")
                }
            }
            workQueue.asyncAfter(deadline: .now() + pongTimeout, execute: timeoutItem)
            ws.sendPing { [weak self] error in
                timeoutItem.cancel()
                guard let self else { return }
                if let error = error {
                    // Ping failed — connection is dead
                    workQueue.async {
                        Task { @MainActor in
                            self._failHeartbeat(ws, reason: "Heartbeat ping failed — \(error.localizedDescription)")
                        }
                    }
                }
                // Ping succeeded — connection is alive, nothing to do
            }
        }
        timer.resume()
        heartbeatTimer = timer
    }

    private func _stopHeartbeat() {
        heartbeatTimer?.cancel()
        heartbeatTimer = nil
    }

    private func _scheduleReconnect() {
        let delay = reconnectDelay
        reconnectDelay = min(reconnectDelay * 2, maxReconnectDelay)
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self else { return }
            Task { @MainActor in
                guard self.shouldReconnect else { return }
                self._openWS()
            }
        }
    }

    // MARK: - Background Session Management
    
    private var backgroundCompletionHandlers: [String: () -> Void] = [:]
    
    func addBackgroundCompletionHandler(identifier: String, completion: @escaping () -> Void) {
        backgroundCompletionHandlers[identifier] = completion
    }
    
    func executeBackgroundCompletionHandler(for identifier: String) {
        if let completion = backgroundCompletionHandlers.removeValue(forKey: identifier) {
            completion()
        }
    }
}
